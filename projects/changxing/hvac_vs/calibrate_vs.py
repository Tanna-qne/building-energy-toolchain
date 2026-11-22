"""變頻熱泵版本的重新校準, 並替換定版。

    uv run --with eppy python projects/changxing/hvac_vs/calibrate_vs.py

1. convert.py: 單速 PTHP 盤管 → 變頻盤管(cx_singlespeed.idf → 記憶體中的模型);
2. 與冬季供暖版相同的校準迴圈(winter_heating/calibrate.py): 每輪跑完全年, 以 365 天同鐘點均值
   (扣附屬支路 7)對照長興原表 24 點, 殘差換算成營業強度 op_level, 超過 1 的部分給分攤負荷 eq_bus,
   降到 0 仍偏高的部分調低站房支路 5; 其餘參數不動。起點是單速定版的校準值;
3. 收斂後寫出新的定版 cx_lowest_max.idf 與根目錄的 hourly_2025.csv、hourly_2025_07.csv、
   schedules.csv、calibration_24h.csv(原表, 不公開)。
"""
import csv
import sys
from pathlib import Path

from eppy.modeleditor import IDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "winter_heating"))
sys.path.insert(0, str(HERE))
import calibrate as cal  # noqa: E402
from convert import EXAMPLE, SRC, convert  # noqa: E402

OUT_IDF = ROOT / "cx_lowest_max.idf"
WORK_IDF = HERE / "cx_vs_calibrating.idf"
cal.RUN = HERE / "run"
TOL_KW = 1.0
MAX_ITER = 10
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    IDF.setiddname(str(cal.EP / "Energy+.idd"))
    idf = IDF(str(SRC))
    convert(idf, IDF(str(cal.EP / "ExampleFiles" / EXAMPLE)))
    ops, buses = cal.compact_hours(idf, "op_level"), cal.compact_hours(idf, "eq_bus")
    op, bus, anc5 = cal.read_values(ops), cal.read_values(buses), cal.anc5_values(idf)
    d_op, d_bus = cal.op_design_kw(idf), cal.bus_design_kw(idf)
    tgt, anc7 = cal.target(), cal.ancillary7()
    log = []
    for it in range(MAX_ITER + 1):
        cal.write_values(ops, op)
        cal.write_values(buses, bus)
        cal.write_anc5(idf, anc5)
        idf.saveas(str(WORK_IDF))
        res = cal.simulate(WORK_IDF)
        comp = [a - b for a, b in zip(res["Electricity:Facility"], anc7)]
        mean = [sum(comp[h::24]) / 365 for h in range(24)]
        err = [m - t for m, t in zip(mean, tgt)]
        worst = max(abs(e) for e in err)
        log.append((it, round(sum(mean), 1), round(worst, 3)))
        print(f"第 {it} 輪: 平均日 {sum(mean):,.1f} kWh(原表 {sum(tgt):,.0f}), 最大鐘點偏差 {worst:.3f} kW", flush=True)
        if worst <= TOL_KW:
            break
        for h in range(24):
            x = op[h] - err[h] / d_op + bus[h] * d_bus / d_op
            op[h] = min(max(x, 0.0), 1.0)
            bus[h] = max(x - 1.0, 0.0) * d_op / d_bus
            if x < 0:
                anc5[h] = min(max(anc5[h] + x * d_op / cal.ANC5_KW, 0.0), 1.0)
    else:
        raise SystemExit(f"{MAX_ITER} 輪後仍未收斂: {log}")

    WORK_IDF.replace(OUT_IDF)
    with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as f:
        old = list(csv.DictReader(f))
    fields = ["interval_start", "interval_end", "comparison_kWh", "station_total_kWh", "ancillary7_kWh"]
    rows = [[r["interval_start"], r["interval_end"], f"{c:.9f}", f"{s:.9f}", r["ancillary7_kWh"]]
            for r, c, s in zip(old, comp, res["Electricity:Facility"])]
    for name, sel in (("hourly_2025.csv", rows), ("hourly_2025_07.csv", [r for r in rows if r[0][5:7] == "07"])):
        with open(ROOT / name, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(fields)
            w.writerows(sel)
    with open(ROOT / "schedules.csv", encoding="utf-8") as f:
        anc7_frac = [r["ancillary7_fraction"] for r in csv.DictReader(f)]
    with open(ROOT / "schedules.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hour_start", "op_level", "eq_bus", "ancillary5_fraction", "ancillary7_fraction"])
        for h in range(24):
            w.writerow([h, round(op[h], 4), round(bus[h], 4), round(anc5[h], 4), anc7_frac[h]])
    with open(ROOT / "calibration_24h.csv", encoding="utf-8-sig") as f:
        head = next(csv.reader(f))
    with open(ROOT / "calibration_24h.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for h in range(24):
            w.writerow([h, f"{tgt[h]:.6f}", f"{mean[h]:.6f}", f"{err[h]:.6f}"])
    print("完成:", log)


if __name__ == "__main__":
    main()
