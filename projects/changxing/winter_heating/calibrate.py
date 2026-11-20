"""冬季供暖 + 重新校準: 由定版 cx_lowest_max.idf 產生 cx_winter_heating.idf。

    vendor/EnergyPlus-MCP/energyplus-mcp-server/.venv/Scripts/python.exe projects/changxing/winter_heating/calibrate.py

1. 空調可用時段(ac_*)原本只有 5/1–9/30 開; 改成全年都用 5–9 月的每日時段。冷 / 熱由恆溫器依
   洪章飛論文表 4.6 的設定溫度決定(夏季 25–26 °C、冬季 20–22 °C), 過渡季室溫在兩者之間時不運行。
2. 重新校準: 每輪跑完全年, 以 365 天同鐘點平均(與原表同口徑, 扣掉單獨計量的附屬支路 7)對照長兴原表 24 點,
   依殘差調整營業強度 op_level; 超過 1 的部分轉給分攤負荷 eq_bus, 降到 0 仍偏高的部分調低站房支路 5
   (與定版相同的三個校準量)。其他參數(圍護、密度、設定溫度、附屬支路 7)不動。
"""
import csv
import shutil
import subprocess
import sys
from pathlib import Path

from eppy.modeleditor import IDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "cx_lowest_max.idf"
OUT_IDF = HERE / "cx_winter_heating.idf"
EPW = ROOT / "weather" / "CHN_Changxing_ERA5_31.00N_120.00E_2025.epw"
EP = Path(r"C:/Users/kiri/AppData/Local/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64")
RUN = HERE / "run"
AC = ["ac_comm", "ac_dining", "ac_dorm", "ac_office", "ac_shop_n"]
TOL_KW = 1.5          # 每個鐘點容許偏差
MAX_ITER = 8
WINTER_OFF_HOURS = (15, 16)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def target():
    with open(ROOT / "calibration_24h.csv", encoding="utf-8-sig") as f:
        return [float(r["reference_kW"]) for r in csv.DictReader(f)]


def ancillary7():
    """附屬支路 7(3 kW)不計入原表口徑; 它的排程不受本次修改影響, 直接取定版逐時值。"""
    with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as f:
        return [float(r["ancillary7_kWh"]) for r in csv.DictReader(f)]


def year_round_ac(idf):
    for name in AC:
        s = idf.getobject("SCHEDULE:COMPACT", name)
        f = [str(x) for x in s.fieldvalues[3:]]
        i5 = f.index("Through: 9/30")
        i_end = f.index("Through: 12/31")
        summer_day = f[i5 + 1:i_end]  # "For: AllDays", "Until: …", 值 …
        vals, t = [], 0  # 展開成 24 個小時值
        for i, x in enumerate(summer_day):
            if x.lower().startswith("until:"):
                h = int(x.split(":")[1])
                while t < h:
                    vals.append(summer_day[i + 1]); t += 1
        winter_day = ["For: AllDays"]
        for h, v in enumerate(vals):  # 非夏季 15–17 時停機(假設: 原表 15 時低谷來自午後停機)
            winter_day += [f"Until: {h + 1:02d}:00", "0" if h in WINTER_OFF_HOURS else v]
        new = ["Through: 4/30"] + winter_day + ["Through: 9/30"] + summer_day + ["Through: 12/31"] + winter_day
        for k in range(len(s.fieldvalues) - 1, 2, -1):  # 清掉舊的欄位
            s.obj.pop()
        for v in new:
            s.obj.append(v)


def compact_hours(idf, name):
    """op_level / eq_bus 是單段 Schedule:Compact(Through: 12/31 / For: AllDays / 24 個 Until)。
    回傳 (物件, 24 個數值所在的欄位索引)。"""
    s = idf.getobject("SCHEDULE:COMPACT", name)
    idx = [i + 1 for i, v in enumerate(s.obj) if str(v).strip().lower().startswith("until:")]
    assert len(idx) == 24 and "Through: 12/31" in [str(v).strip() for v in s.obj], f"{name} 不是預期的單段 24 小時格式"
    return s, idx


def read_values(sched):
    s, idx = sched
    return [float(s.obj[i]) for i in idx]


def write_values(sched, vals):
    s, idx = sched
    for i, v in zip(idx, vals):
        s.obj[i] = round(v, 4)


ANC5 = "CX_anc5_afternoon"   # 12 kW 站房支路(定版已用它處理 15–16 時的低谷)
ANC5_KW = 12.0


def anc5_values(idf):
    """把 CX_anc5_afternoon 展開成 24 個小時值(定版寫法是分段 Until)。"""
    s = idf.getobject("SCHEDULE:COMPACT", ANC5)
    f = [str(x).strip() for x in s.obj[3:]]
    vals, t = [], 0
    for i, x in enumerate(f):
        if x.lower().startswith("until:"):
            h = int(x.split(":")[1].strip())
            while t < h:
                vals.append(float(f[i + 1]))
                t += 1
    return vals


def write_anc5(idf, vals):
    s = idf.getobject("SCHEDULE:COMPACT", ANC5)
    del s.obj[3:]
    s.obj.extend(["Through: 12/31", "For: AllDays"])
    for h, v in enumerate(vals):
        s.obj.extend([f"Until: {h + 1:02d}:00", round(v, 4)])


def op_design_kw(idf):
    """以 op_level 運行的照明、設備、廚房的設計功率(kW); 用來把 kW 殘差換算成 op_level 的調整量。"""
    area = {}
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Surface_Type.lower() == "floor":
            area[s.Zone_Name] = area.get(s.Zone_Name, 0) + s.area
    space_zone = {s.Name: s.Zone_Name for s in idf.idfobjects["SPACE"]}
    lists = {s.Name: [space_zone.get(x, x) for x in s.fieldvalues[2:] if x] for s in idf.idfobjects["SPACELIST"]}
    total = 0.0
    for cls, field in (("LIGHTS", "Watts_per_Floor_Area"), ("ELECTRICEQUIPMENT", "Watts_per_Floor_Area")):
        for o in idf.idfobjects[cls]:
            if o.Schedule_Name == "op_level":
                zs = lists.get(o.Zone_or_ZoneList_or_Space_or_SpaceList_Name, [])
                total += float(o[field]) * sum(area.get(z, 0) for z in zs)
    for o in idf.idfobjects["OTHEREQUIPMENT"]:
        if o.Schedule_Name == "op_level" and o.Design_Level not in ("", None):
            total += float(o.Design_Level)
    return total / 1000


def bus_design_kw(idf):
    return sum(float(o.Power_per_Floor_Area) * a for o, a in
               ((o, sum(s.area for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]
                        if s.Surface_Type.lower() == "floor" and s.Zone_Name == o.Zone_or_ZoneList_or_Space_or_SpaceList_Name))
                for o in idf.idfobjects["OTHEREQUIPMENT"] if o.Schedule_Name == "eq_bus")) / 1000


def simulate(idf_path):
    shutil.rmtree(RUN, ignore_errors=True)
    RUN.mkdir(parents=True)
    r = subprocess.run([str(EP / "energyplus.exe"), "-w", str(EPW), "-d", str(RUN), "-r", str(idf_path)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise SystemExit((RUN / "eplusout.err").read_text(encoding="latin-1")[-2000:])
    with open(RUN / "eplusmtr.csv", encoding="latin-1") as f:
        rows = list(csv.reader(f))
    head = rows[0]
    cols = {n: head.index(n + " [J](Hourly)") for n in ("Electricity:Facility", "Heating:Electricity",
                                                       "Cooling:Electricity", "Fans:Electricity")}
    return {n: [float(r[i]) / 3.6e6 for r in rows[1:]] for n, i in cols.items()}


def main():
    IDF.setiddname(str(EP / "Energy+.idd"))
    idf = IDF(str(SRC))
    year_round_ac(idf)
    ops, buses = compact_hours(idf, "op_level"), compact_hours(idf, "eq_bus")
    op, bus = read_values(ops), read_values(buses)
    anc5 = anc5_values(idf)
    d_op, d_bus = op_design_kw(idf), bus_design_kw(idf)
    tgt, anc7 = target(), ancillary7()
    print(f"op_level 設計功率 {d_op:.1f} kW; eq_bus 設計功率 {d_bus:.1f} kW")
    log = []
    for it in range(MAX_ITER + 1):
        write_values(ops, op)
        write_values(buses, bus)
        write_anc5(idf, anc5)
        idf.saveas(str(OUT_IDF))
        res = simulate(OUT_IDF)
        comp = [a - b for a, b in zip(res["Electricity:Facility"], anc7)]
        mean = [sum(comp[h::24]) / 365 for h in range(24)]
        err = [m - t for m, t in zip(mean, tgt)]
        worst = max(abs(e) for e in err)
        log.append((it, round(sum(mean)), round(worst, 2)))
        print(f"第 {it} 輪: 全年平均日 {sum(mean):,.1f} kWh(原表 {sum(tgt):,.0f}), 最大鐘點偏差 {worst:.2f} kW", flush=True)
        if worst <= TOL_KW:
            break
        for h in range(24):  # 殘差換算成 op_level; 超過 1 的部分給 eq_bus, 低於 0 的部分從站房支路 5 扣
            x = op[h] - err[h] / d_op + bus[h] * d_bus / d_op
            op[h] = min(max(x, 0.0), 1.0)
            bus[h] = max(x - 1.0, 0.0) * d_op / d_bus
            if x < 0:
                anc5[h] = min(max(anc5[h] + x * d_op / ANC5_KW, 0.0), 1.0)
    with open(HERE / "schedules_winter_heating.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hour_start", "op_level", "eq_bus", "ancillary5_fraction"])
        for h in range(24):
            w.writerow([h, round(op[h], 4), round(bus[h], 4), round(anc5[h], 4)])
    with open(HERE / "calibration_24h.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hour_start", "reference_kW", "simulation_kW", "signed_error_kW"])
        for h in range(24):
            w.writerow([h, tgt[h], round(mean[h], 6), round(err[h], 6)])
    with open(HERE / "hourly_2025.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["interval_start", "comparison_kWh", "station_total_kWh", "heating_kWh", "cooling_kWh", "fans_kWh"])
        with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as g:
            stamps = [r["interval_start"] for r in csv.DictReader(g)]
        for i, t in enumerate(stamps):
            w.writerow([t, round(comp[i], 9), round(res["Electricity:Facility"][i], 9), round(res["Heating:Electricity"][i], 6),
                        round(res["Cooling:Electricity"][i], 6), round(res["Fans:Electricity"][i], 6)])
    print("完成:", log)


if __name__ == "__main__":
    main()
