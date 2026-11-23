"""長興定版模型對照 GB 55015-2021《建筑节能与可再生能源利用通用规范》→ gb55015/compliance.json。

    uv run --with eppy python projects/changxing/gb55015/check.py

1. 規定性指標逐項對照: 甲類公共建築(附錄 B.0.1)、夏熱冬冷地區表 3.1.10-4 圍護限值、3.1.15 遮陽、
   3.3.7 照明功率密度、3.2.14 房間空調器、5.2.1 太陽能。
2. 附錄 C 權衡判斷: 參照建築 = 設計建築把不達標的圍護項改到限值(屋面 K、南向 SHGC),
   總耗電量 E = 供冷 Q_C / COP_C(3.50) + 供暖按燃氣鍋爐折算(C.0.7-4), 兩者都用 EnergyPlus 盤管的全年耗冷 / 耗熱量。
   注意: 附錄 C.0.6 規定的標準工況(作息、人員、設備)這裡沒有採用, 兩個模型都用長興校準後的運行工況。
3. 另跑一個「圍護 + 照明都改到規範限值」的情景, 看實際運行下能省多少電。
限值取自維基文庫的規範轉錄本(見 MODEL_README 第 16 節), 不是住建部正式文本。
"""
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eppy.modeleditor import IDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "sensitivity"))
sys.path.insert(0, str(ROOT / "hvac_vs"))
from common import BASE_IDF, EP  # noqa: E402
from compare import run  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RSI, RSE = 0.11, 0.04  # GB 50176 內外表面換熱阻 m²K/W
# 表 3.1.10-4 夏熱冬冷地區甲類公共建築
ROOF_K = 0.40
WALL_K = {"D<=2.5": 0.60, "D>2.5": 0.80}
WINDOW = [(0.20, 3.00, 0.45, 0.45), (0.30, 2.60, 0.40, 0.45), (0.40, 2.20, 0.35, 0.40), (0.50, 2.20, 0.30, 0.35),
          (0.60, 2.10, 0.30, 0.35), (0.70, 2.10, 0.25, 0.30), (0.80, 2.00, 0.25, 0.30), (9.99, 1.80, 0.20, 0.20)]
# 窗墙比上限, K, SHGC(東南西), SHGC(北)
# 表 3.3.7-x: 熱區清單 → (對應條目, 限值 W/m²); 服務區沒有專門條目, 取最接近的房間類型
LPD = {
    "CX_office": ("表 3.3.7-3 普通辦公室", 8.0),
    "CX_shop": ("表 3.3.7-4 一般商店營業廳(超市為 10.0)", 9.0),
    "CX_dining": ("表 3.3.7-5 中餐廳", 8.0),
    "CX_lobby": ("表 3.3.7-5 大堂", 8.0),
    "CX_dorm": ("表 3.3.7-7 學生宿舍(員工宿舍無專門條目)", 4.5),
    "CX_toilet": ("表 3.3.7-12 廁所(普通)", 3.0),
    "CX_store": ("表 3.3.7-12 一般件庫", 3.5),
    "CX_kitchen": ("無對應條目(規範未列商業廚房)", None),
}
COP_C = 3.50                                   # C.0.7-2 公共建築供冷綜合性能係數
GAS = {"eta2": 0.85, "q3": 9.87, "q2": 0.330, "phi": 1.21}  # C.0.7-4 公共建築燃氣鍋爐供暖折算
TOL_AZ = 45


def layers(idf, cons_name):
    c = idf.getobject("CONSTRUCTION", cons_name)
    return [idf.getobject("MATERIAL", n) for n in c.fieldvalues[2:] if n]


def u_and_d(idf, cons_name):
    """GB 50176: K = 1/(Rsi + ΣR + Rse); D = Σ R·S, S = √(2π λ ρ c / 86400)。"""
    r = d = 0.0
    for m in layers(idf, cons_name):
        ri = float(m.Thickness) / float(m.Conductivity)
        s = math.sqrt(2 * math.pi * float(m.Conductivity) * float(m.Density) * float(m.Specific_Heat) / 86400)
        r, d = r + ri, d + ri * s
    return 1 / (RSI + r + RSE), d


def facades(idf):
    north = float(idf.idfobjects["BUILDING"][0].North_Axis)
    walls, wins = {}, {}
    for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]:
        if s.Surface_Type.lower() == "wall" and s.Outside_Boundary_Condition.lower() == "outdoors":
            az = round((s.azimuth + north) % 360)
            walls[az] = walls.get(az, 0) + s.area
    for f in idf.idfobjects["FENESTRATIONSURFACE:DETAILED"]:
        host = idf.getobject("BUILDINGSURFACE:DETAILED", f.Building_Surface_Name)
        az = round((host.azimuth + north) % 360)
        wins.setdefault(az, []).append(f)
    return walls, wins


def orient(az):
    return min((abs((az - a + 180) % 360 - 180), n) for a, n in ((0, "北"), (90, "東"), (180, "南"), (270, "西")))[1]


def window_limit(wwr, north):
    for top, k, shgc_esw, shgc_n in WINDOW:
        if wwr <= top:
            return k, shgc_n if north else shgc_esw


def roof_layer_for(idf, cons_name, k_target):
    """把屋面保溫層(PU)的導熱係數調到使 K = k_target。"""
    ms = layers(idf, cons_name)
    pu = next(m for m in ms if m.Name.startswith("PU foam"))
    other = sum(float(m.Thickness) / float(m.Conductivity) for m in ms if m is not pu)
    r_pu = 1 / k_target - RSI - RSE - other
    pu.Conductivity = round(float(pu.Thickness) / r_pu, 5)


def make_reference(idf, fixes):
    """參照建築: 只把不達標的圍護項改到限值(C.0.3)。"""
    wins = facades(idf)[1]
    for f in fixes:
        if f["item"] == "屋面":
            roof_layer_for(idf, f["construction"], ROOF_K)
        elif f["item"].startswith("外窗"):
            src = idf.idfobjects["WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM"][0]
            g = idf.copyidfobject(src)
            g.Name = f"{src.Name} SHGC{f['limit_shgc']}"
            g.Solar_Heat_Gain_Coefficient = f["limit_shgc"]
            c = idf.copyidfobject(idf.getobject("CONSTRUCTION", f["construction"]))
            c.Name = f"{f['construction']} az{f['azimuth']}"
            c.Outside_Layer = g.Name
            for w in wins[f["azimuth"]]:
                w.Construction_Name = c.Name


def lights_to_limit(idf):
    for o in idf.idfobjects["LIGHTS"]:
        lim = LPD.get(o.Zone_or_ZoneList_or_Space_or_SpaceList_Name, (None, None))[1]
        if lim is not None and float(o.Watts_per_Floor_Area) > lim:
            o.Watts_per_Floor_Area = lim


def appendix_c(coils, area):
    qc = coils["Cooling Coil Total Cooling Energy"] * 1000
    qh = (coils["Heating Coil Heating Energy"] + coils.get("Heating Coil Heating Energy (supplemental)", 0)) * 1000
    ec = qc / (area * COP_C)
    eh = qh * GAS["phi"] / (area * GAS["eta2"] * GAS["q3"] * GAS["q2"])
    return {"Q_C_MWh": qc / 1000, "Q_H_MWh": qh / 1000, "E_C_kWh_m2": ec, "E_H_kWh_m2": eh, "E_kWh_m2": ec + eh}


def main():
    IDF.setiddname(str(EP / "Energy+.idd"))
    idf = IDF(str(BASE_IDF))
    area = sum(s.area for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"] if s.Surface_Type.lower() == "floor")
    rows, fixes = [], []

    def add(clause, item, value, limit, ok, note=""):
        rows.append({"clause": clause, "item": item, "value": value, "limit": limit, "ok": ok, "note": note})

    add("附錄 B.0.1", "公共建築分類", f"單棟 {area:,.0f} m²", "> 300 m² 為甲類", True, "按甲類公共建築要求")
    cons = {s.Construction_Name for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]
            if s.Outside_Boundary_Condition.lower() == "outdoors" and s.Surface_Type.lower() in ("wall", "roof")}
    for cn in sorted(cons):
        k, d = u_and_d(idf, cn)
        is_roof = "Roof" in cn
        if is_roof:
            ok = k <= ROOF_K
            add("表 3.1.10-4", "屋面", f"K {k:.2f}", f"K ≤ {ROOF_K}", ok,
                "附錄 C.0.1-1: 屋面不得通過權衡判斷降低, 必須直接達標")
            if not ok:
                fixes.append({"item": "屋面", "construction": cn})
        else:
            lim = WALL_K["D>2.5" if d > 2.5 else "D<=2.5"]
            add("表 3.1.10-4", "外牆", f"K {k:.2f}, D {d:.2f}", f"K ≤ {lim}(D {'>' if d > 2.5 else '≤'} 2.5)", k <= lim)
    walls, wins = facades(idf)
    glz = idf.idfobjects["WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM"]
    assert len(glz) == 1, "預期全部外窗共用一種玻璃"
    k_win, shgc = float(glz[0].UFactor), float(glz[0].Solar_Heat_Gain_Coefficient)
    for az in sorted(walls):
        ws = wins.get(az, [])
        wwr = sum(w.area for w in ws) / walls[az]
        o = orient(az)
        lk, ls = window_limit(wwr, o == "北")
        ok_k, ok_s = k_win <= lk, shgc <= ls
        add("表 3.1.10-4", f"外窗 {o}向(方位 {az}°)", f"窗牆比 {wwr:.2f}, K {k_win}, SHGC {shgc}",
            f"K ≤ {lk}, SHGC ≤ {ls}", ok_k and ok_s)
        if not ok_s:
            fixes.append({"item": f"外窗 {o}", "orientation": o, "azimuth": az, "construction": ws[0].Construction_Name,
                          "limit_shgc": ls})
    n_shade = sum(len(idf.idfobjects[k]) for k in ("SHADING:ZONE:DETAILED", "SHADING:BUILDING:DETAILED",
                                                    "SHADING:OVERHANG", "SHADING:FIN", "WINDOWSHADINGCONTROL"))
    add("3.1.15", "南、東、西向外窗遮陽", f"模型中遮陽物件 {n_shade} 個", "應採取遮陽措施", n_shade > 0,
        "原型論文未給遮陽構造, 模型未建; 實際建築是否有挑簷或遮陽需現場核實")
    for o in idf.idfobjects["LIGHTS"]:
        name, lim = LPD[o.Zone_or_ZoneList_or_Space_or_SpaceList_Name]
        v = float(o.Watts_per_Floor_Area)
        add("3.3.7", f"照明 {o.Zone_or_ZoneList_or_Space_or_SpaceList_Name[3:]}", f"{v:g} W/m²",
            "—" if lim is None else f"≤ {lim:g} W/m²", None if lim is None else v <= lim, name)
    add("3.2.14", "房間空調器能效", "額定製冷 COP 3.5、製熱 COP 3.3(變頻)", "APF ≥ 3.30–4.00(依製冷量)", None,
        "規範以 APF 判定; 模型只有額定點 COP, 無法直接比較, 見第 16 節說明")
    add("5.2.1", "新建建築應安裝太陽能系統", "站內光伏(第 11.1 節)", "應安裝", True)

    ref = IDF(str(BASE_IDF))
    make_reference(ref, fixes)
    full = IDF(str(BASE_IDF))
    make_reference(full, fixes)
    lights_to_limit(full)
    work = HERE / "_runs"
    work.mkdir(exist_ok=True)
    paths = {"design": BASE_IDF, "reference": work / "reference.idf", "code_limits": work / "code_limits.idf"}
    ref.saveas(str(paths["reference"]))
    full.saveas(str(paths["code_limits"]))
    with ThreadPoolExecutor(3) as ex:
        sims = dict(zip(paths, ex.map(run, paths.values())))
    out = {"source": "GB 55015-2021(維基文庫轉錄本)", "area_m2": area, "checks": rows,
           "reference_changes": fixes, "simulations": {}}
    for tag, (res, coils) in sims.items():
        out["simulations"][tag] = {
            "facility_MWh": sum(res["Electricity:Facility"]) / 1000,
            "heating_MWh": sum(res["Heating:Electricity"]) / 1000, "cooling_MWh": sum(res["Cooling:Electricity"]) / 1000,
            "fans_MWh": sum(res["Fans:Electricity"]) / 1000, "appendix_c": appendix_c(coils, area)}
    d, r = out["simulations"]["design"]["appendix_c"]["E_kWh_m2"], out["simulations"]["reference"]["appendix_c"]["E_kWh_m2"]
    out["tradeoff_pass"] = d <= r
    (HERE / "compliance.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in rows:
        mark = {True: "符合", False: "不符", None: "—"}[x["ok"]]
        print(f"[{mark}] {x['clause']:12s} {x['item']:24s} {x['value']:34s} 限值 {x['limit']}  {x['note']}")
    for tag, s in out["simulations"].items():
        c = s["appendix_c"]
        print(f"{tag:12s} 全站 {s['facility_MWh']:8.1f} MWh  冷 {s['cooling_MWh']:6.1f} 熱 {s['heating_MWh']:5.1f}"
              f"  附錄C E = {c['E_C_kWh_m2']:.2f} + {c['E_H_kWh_m2']:.2f} = {c['E_kWh_m2']:.2f} kWh/m²")
    print("權衡判斷:", "設計 ≤ 參照, 通過" if out["tradeoff_pass"] else "設計 > 參照, 不通過", f"({d:.2f} vs {r:.2f})")


if __name__ == "__main__":
    main()
