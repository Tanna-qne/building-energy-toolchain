"""長興服務區: 工具鏈 8 種節能措施 + 全部組合, 全年 EnergyPlus 模擬, 並看對 7 月光伏消納的影響。

    uv run --with eppy python projects/changxing/ecm/ecm.py

措施定義與工具鏈(scan2/scan2.py、tools/modeler/build_idf.py、tools/retrofit.py)相同,
只有一處依長興的系統調整: 長興是變頻分體熱泵(PTHP + 變速盤管), 高效率空調把製冷與製熱各速 COP 一起 ×1.25(工具鏈只改單速製冷)。
基準是定版模型 cx_lowest_max.idf; 營業排程、分攤負荷與充電序列不動, 所以省下的電全部來自建築本身。
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "sensitivity"))
sys.path.insert(0, str(ROOT.parents[1] / "tools"))
from common import BASE_IDF, EP, run_many, scale_vs_cop  # noqa: E402
from eppy.modeleditor import IDF  # noqa: E402
from retrofit import upgrade_hvac_efficiency, upgrade_insulation  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LIGHT_FACTOR, EQUIP_FACTOR, INFIL_FACTOR = 0.7, 0.8, 0.5
FILM_SHGC, COOLROOF_ABS, COP_FACTOR = 0.30, 0.30, 1.25
PRICES = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]  # 元/kWh, 掃描用; 浙江分時電價 2026 年調整中, 不固定單一值
PAYBACK_YEARS = 5


def lights(i, notes):
    for o in i.idfobjects["LIGHTS"]:
        o.Watts_per_Floor_Area = round(o.Watts_per_Floor_Area * LIGHT_FACTOR, 3)
    notes.append(f"照明功率密度 ×{LIGHT_FACTOR}")


def film(i, notes):
    for o in i.idfobjects["WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM"]:
        notes.append(f"外窗 SHGC {o.Solar_Heat_Gain_Coefficient} → {FILM_SHGC}(U {o.UFactor} 不變)")
        o.Solar_Heat_Gain_Coefficient = FILM_SHGC
        o.Visible_Transmittance = min(float(o.Visible_Transmittance), 0.55)


def infil(i, notes):
    for o in i.idfobjects["ZONEINFILTRATION:DESIGNFLOWRATE"]:
        o.Flow_Rate_per_Exterior_Surface_Area = o.Flow_Rate_per_Exterior_Surface_Area * INFIL_FACTOR
    notes.append(f"滲風量 ×{INFIL_FACTOR}")


def coolroof(i, notes):
    roofs = {s.Construction_Name.upper() for s in i.idfobjects["BUILDINGSURFACE:DETAILED"]
             if s.Surface_Type.lower() == "roof" and s.Outside_Boundary_Condition.lower() == "outdoors"}
    outer = {c.Outside_Layer.upper() for c in i.idfobjects["CONSTRUCTION"] if c.Name.upper() in roofs}
    for o in i.idfobjects["MATERIAL"]:
        if o.Name.upper() in outer and float(o.Solar_Absorptance) > COOLROOF_ABS:
            notes.append(f"屋面外層「{o.Name}」日射吸收率 {o.Solar_Absorptance} → {COOLROOF_ABS}")
            o.Solar_Absorptance = COOLROOF_ABS


def equip(i, notes):
    for o in i.idfobjects["ELECTRICEQUIPMENT"]:
        o.Watts_per_Floor_Area = round(o.Watts_per_Floor_Area * EQUIP_FACTOR, 3)
    notes.append(f"一般設備功率密度 ×{EQUIP_FACTOR}(廚房電器不含)")


def insulation(i, notes):
    for c in upgrade_insulation(i):
        notes.append(f"{c['construction']} U {c['u_old']:.3f} → {c['u_new']:.2f}")


def hvac(i, notes):
    ch = upgrade_hvac_efficiency(i, cop_factor=COP_FACTOR)  # 這裡只改到風機效率(工具鏈的欄位表沒有變頻盤管)
    for kind, old, new in scale_vs_cop(i, COP_FACTOR):  # 變頻盤管各速 COP 一起 ×1.25
        ch.append({"field": "額定製冷 COP" if "COOLING" in kind else "額定製熱 COP", "old": old, "new": new})
    seen = {}
    for c in ch:
        seen.setdefault(c["field"], c)
    notes.extend(f"{k} {c['old']:g} → {c['new']:g}" for k, c in seen.items())


MEASURES = [
    ("lights", "照明 −30%", [lights]),
    ("film", "外窗貼膜", [film]),
    ("lights_film", "照明 + 貼膜", [lights, film]),
    ("infil", "加強氣密", [infil]),
    ("coolroof", "屋面反射塗料", [coolroof]),
    ("equip", "節能設備 −20%", [equip]),
    ("insulation", "加強牆與屋面隔熱", [insulation]),
    ("hvac", "高效熱泵 COP ×1.25", [hvac]),
    ("package", "全部措施組合", [lights, film, infil, coolroof, equip, insulation, hvac]),
]


def july_pv(load_saving_july):
    """7 月: 總負荷(建築 + 充電)扣掉該措施的逐時節電後, 再與光伏比較。"""
    with open(ROOT / "pv_2025" / "july_load_pv.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    load = [float(r["load_kWh"]) - s for r, s in zip(rows, load_saving_july)]
    pv = [float(r["pv_kWh"]) for r in rows]
    net = [a - b for a, b in zip(load, pv)]
    surplus = [-x for x in net if x < 0]
    pv_total = sum(pv)
    return {"july_load_kWh": sum(load), "surplus_hours": len(surplus), "surplus_kWh": sum(surplus),
            "pv_self_use_pct": 100 * (pv_total - sum(surplus)) / pv_total, "peak_net_kW": max(net)}


def main():
    IDF.setiddname(str(EP / "Energy+.idd"))
    work = HERE / "_runs"
    work.mkdir(exist_ok=True)
    paths, notes = [work / "baseline.idf"], {"baseline": []}
    IDF(str(BASE_IDF)).saveas(str(paths[0]))
    for key, _, fns in MEASURES:
        i = IDF(str(BASE_IDF))
        notes[key] = []
        for fn in fns:
            fn(i, notes[key])
        p = work / f"{key}.idf"
        i.saveas(str(p))
        paths.append(p)
    sims = dict(zip(["baseline"] + [m[0] for m in MEASURES], run_many(paths, work)))

    with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as f:
        hourly = list(csv.DictReader(f))
    ref = [float(r["station_total_kWh"]) for r in hourly]
    base = sims["baseline"]["Electricity:Facility"]
    check = max(abs(a - b) for a, b in zip(base, ref))
    july = [k for k, r in enumerate(hourly) if r["interval_start"][5:7] == "07"]

    def summary(res, save):
        e = res["Electricity:Facility"]
        return {"annual_kWh": sum(e), "july_kWh": sum(e[k] for k in july), "peak_kW": max(e),
                "cooling_kWh": sum(res["Cooling:Electricity"]), "heating_kWh": sum(res["Heating:Electricity"]),
                "fans_kWh": sum(res["Fans:Electricity"]), **july_pv([save[k] for k in july])}

    b = summary(sims["baseline"], [0.0] * len(base))
    out = {"baseline_check_max_abs_kWh": check, "baseline": b, "measures": []}
    for key, label, _ in MEASURES:
        e = sims[key]["Electricity:Facility"]
        save = [x - y for x, y in zip(base, e)]
        s = summary(sims[key], save)
        saved = b["annual_kWh"] - s["annual_kWh"]
        out["measures"].append({
            "key": key, "label": label, "changes": notes[key], **s,
            "saving_kWh": saved, "saving_pct": 100 * saved / b["annual_kWh"],
            "d_cooling_kWh": b["cooling_kWh"] - s["cooling_kWh"], "d_heating_kWh": b["heating_kWh"] - s["heating_kWh"],
            "d_fans_kWh": b["fans_kWh"] - s["fans_kWh"],
            "peak_cut_kW": b["peak_kW"] - s["peak_kW"],
            "saving_yuan": {str(p): saved * p for p in PRICES},
            f"breakeven_capex_{PAYBACK_YEARS}y_yuan": {str(p): saved * p * PAYBACK_YEARS for p in PRICES},
        })
    (HERE / "ecm_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    cols = ["key", "label", "annual_kWh", "saving_kWh", "saving_pct", "d_cooling_kWh", "d_heating_kWh", "d_fans_kWh",
            "july_kWh", "peak_kW", "peak_cut_kW", "surplus_hours", "surplus_kWh", "pv_self_use_pct", "peak_net_kW"]
    with open(HERE / "ecm_results.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols + [f"saving_yuan_at_{p}" for p in PRICES])
        w.writerow(["baseline", "基準(定版)"] + [round(b.get(c, 0), 2) for c in cols[2:]] + [0] * len(PRICES))
        for m in out["measures"]:
            w.writerow([m["key"], m["label"]] + [round(m[c], 2) for c in cols[2:]] +
                       [round(m["saving_yuan"][str(p)]) for p in PRICES])
    print(f"基準重算與 hourly_2025.csv 最大差 {check:.4f} kWh")
    for m in out["measures"]:
        print(f"{m['label']:14s} 省 {m['saving_kWh']:9,.0f} kWh {m['saving_pct']:5.2f}%  冷 {m['d_cooling_kWh']:8,.0f}"
              f" 熱 {m['d_heating_kWh']:8,.0f} 峰 −{m['peak_cut_kW']:5.1f} kW  7 月餘電 {m['surplus_hours']} h"
              f" {m['surplus_kWh']:7,.0f} kWh  消納 {m['pv_self_use_pct']:.2f}%")
    print("基準", {k: round(v, 1) for k, v in b.items()})


if __name__ == "__main__":
    main()
