"""措施組合 + 成本/回收期: 在同一個模型上疊加措施, 算出年省電、年省錢、簡單回收期。

模擬與修改一樣走 EnergyPlus-MCP 工具; 成本假設集中在 COSTS, 要改只改這裡。
"""
import csv
import json
import os
import queue
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from batch_driver import Mcp, meter_kwh, sim_args, MODELS as MODELS_DIR  # noqa: E402
from scan.scan import lighting_scaled_updates, parse  # noqa: E402
from building_report import write_html  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = Path(__file__).resolve().parent
MODELS = [
    "5ZoneAirCooled",
    "5ZoneAirCooledConvCoef",
    "5ZoneAirCooledDemandLimiting",
    "HVACTemplate-5ZoneVAVFanPowered",
    "HVACTemplate-5ZoneBaseboardHeat",
]

# 貨幣、能源價格與施工單價都在根目錄的 costs.json(假設值, 非報價), 報告與敏感度網頁也讀這份
COSTS = json.loads((Path(__file__).resolve().parent.parent / "costs.json").read_text(encoding="utf-8"))
COOLROOF_SOLAR_ABS = 0.3  # 反射塗料後屋頂外層的日射吸收率(一般深色屋頂約 0.7)
EQUIP_FACTOR = 0.8        # 節能設備: 插座 / 設備用電功率 ×0.8

IDEAL_COOL_COP = 3.0   # 理想負載換算: 冷氣(電)的 COP, 假設值
IDEAL_HEAT_EFF = 0.85  # 理想負載換算: 暖氣與熱水用瓦斯鍋爐的效率, 假設值
FILM_SHGC = 0.30       # 貼膜後的 SHGC
FILM_SKIP_SHGC = 0.35  # 原窗 SHGC 已經不高於這個值就不貼, 效果有限


def glazing(output_dir: Path):
    """從 E+ 報表的 Exterior Fenestration 取外窗面積加權的 (U, SHGC, 可見光穿透率); 沒有外窗回傳 None。"""
    table = next(output_dir.glob("*Table.htm"), None)
    text = table.read_text(encoding="latin-1") if table else ""
    i = text.find("<b>Exterior Fenestration</b>")
    if i < 0:
        # DesignBuilder 手動匯出的模型常沒開這張報表; 改讀 .eio 的 WindowConstruction(一律 SI,
        # 數值與報表的 Glass U-Factor / SHGC 相同)。多種構造時取平均, 因為 .eio 沒有各自的面積
        return glazing_from_eio(output_dir)
    block = text[i:text.find("</table>", i)]
    rows = [[re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            for r in re.findall(r"<tr>(.*?)</tr>", block, re.S)]
    head = rows[0]
    total = next((r for r in rows[1:] if r and r[0] == "Total or Average"), None)
    # DB 模擬時的報表是英制(DB 內跑模擬產生的 IDF 有這張表、卻沒有 Output:Constructions, .eio 讀不到)
    u_col, u_factor = ("Glass U-Factor [W/m2-K]", 1.0) if "Glass U-Factor [W/m2-K]" in head else \
        ("Glass U-Factor [Btu/h-ft2-F]", 5.678263)
    try:
        u, shgc, vt = (float(total[head.index(k)]) for k in (u_col, "Glass SHGC", "Glass Visible Transmittance"))
    except (TypeError, ValueError):
        return glazing_from_eio(output_dir)
    return round(u * u_factor, 3), shgc, vt


def glazing_from_eio(output_dir: Path):
    eio = next(output_dir.glob("*.eio"), None)
    if not eio:
        return None
    vals = []
    for line in eio.read_text(encoding="latin-1").splitlines():
        p = [x.strip() for x in line.split(",")]
        if p[0] == "WindowConstruction" and len(p) >= 11:
            try:
                vals.append((float(p[5]), float(p[8]), float(p[10])))
            except ValueError:
                pass
    if not vals:
        return None
    return tuple(round(sum(v[k] for v in vals) / len(vals), 3) for k in range(3))


def roof_area(output_dir: Path, what="Roof"):
    """報表的 Gross Roof / Wall Area(m²);DesignBuilder 的報表是英制 ft², 換算成 m²。"""
    table = next(output_dir.glob("*Table.htm"), None)
    if not table:
        return None
    m = re.search(rf"Gross {what} Area \[(m2|ft2)\]</td>\s*<td[^>]*>\s*([\d.,]+)",
                  table.read_text(encoding="latin-1"))
    if not m:
        return None
    area = float(m.group(2).replace(",", ""))
    return round(area * 0.09290304 if m.group(1) == "ft2" else area, 1)


def coolroof(client, src: Path, out_path: Path):
    """屋頂外層材料的日射吸收率改成 COOLROOF_SOLAR_ABS。回傳 (IDF 路徑或 None, 說明)。
    原本已經是淺色(不高於目標值)就不做: 塗了反而變差。"""
    out_path = out_path.resolve()
    out_path.unlink(missing_ok=True)
    text = client.tool("add_coating_outside", {"idf_path": str(src), "location": "roof", "solar_abs": COOLROOF_SOLAR_ABS,
                                               "thermal_abs": 0.9, "output_path": str(out_path)}, 120)
    data = parse(text)
    old = [float(m["old_value"]) for m in data.get("modifications", data.get("modifications_made", []))
           if m.get("field") == "Solar_Absorptance" and m.get("old_value") not in (None, "")]
    if not (data.get("success") and out_path.exists() and old):
        out_path.unlink(missing_ok=True)
        return None, "模型沒有外露的屋頂,或屋頂材料讀不到,這項不適用"
    avg = sum(old) / len(old)
    if avg <= COOLROOF_SOLAR_ABS:
        out_path.unlink(missing_ok=True)
        return None, f"屋頂本來就是淺色(日射吸收率 {avg:.2f}),塗反射漆沒有幫助"
    return out_path, f"屋頂日射吸收率 {avg:.2f} → {COOLROOF_SOLAR_ABS:.2f}"


def scaled_updates(client, tool, key, idf: Path, factor, level_field):
    """inspect_lights / inspect_electric_equipment 的結果, 依各物件的計算方式把功率 ×factor。
    level_field: 「以總功率表示」時的 (修改用欄位名, inspect 回傳的鍵), 照明是 Lighting_Level, 設備是 Design_Level。"""
    text = client.tool(tool, {"idf_path": str(idf.resolve())}, 120)
    data = json.loads(text.split("\n", 1)[1])
    mods = []
    for obj in data.get(key, []):
        name = obj.get("name")
        if not name:
            continue
        method = (obj.get("design_level_calculation_method") or "").lower()
        if "watt" in method and "area" in method:
            field, raw = "Watts_per_Floor_Area", obj.get("watts_per_floor_area")
        elif "person" in method:
            field, raw = "Watts_per_Person", obj.get("watts_per_person")
        else:
            field, raw = level_field[0], obj.get(level_field[1])
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        mods.append({"target": f"name:{name}", "field_updates": {field: round(value * factor, 2)}})
    return mods


def film_args(glz):
    """貼膜只降 SHGC, U 值沿用原窗: 工具會把外窗整個換成簡化玻璃, 固定 U 會把好窗換成差窗。
    回傳 (參數, 不貼的原因)。"""
    if glz is None:
        return None, "讀不到外窗資料(模型可能沒有外窗),貼膜不適用"
    u, shgc, vt = glz
    if shgc <= FILM_SKIP_SHGC:
        return None, f"原本的玻璃已經能擋大部分日射(SHGC {shgc:.2f}),貼膜幫助不大"
    return {"u_value": round(u, 3), "shgc": FILM_SHGC, "visible_transmittance": round(min(vt, 0.55), 3)}, ""


def run(client, idf: Path):
    started = time.time()
    payload = parse(client.tool("run_energyplus_simulation", sim_args(idf), 900))
    elec = gas = None
    district = ""
    cool = heat = 0.0
    out_dir = payload.get("output_directory")
    if payload.get("success") and out_dir:
        od = Path(out_dir)
        elec = meter_kwh(od, "Electricity:Facility")
        # 沒有天然氣電表的模型當作 0 (全電)
        if elec is not None:
            gas = meter_kwh(od, "NaturalGas:Facility") or 0.0
            # 理想負載(DesignBuilder 的簡化空調)只算出要供應多少冷 / 熱, 記在 District 電表, 不是耗能;
            # 用假設的設備效率換成電與天然氣, 否則空調能耗整個漏掉
            cool = meter_kwh(od, "DistrictCooling:Facility") or 0.0
            heat = (meter_kwh(od, "DistrictHeatingWater:Facility") or 0.0) + \
                (meter_kwh(od, "DistrictHeatingSteam:Facility") or 0.0)
            if cool or heat:
                elec = round(elec + cool / IDEAL_COOL_COP, 1)
                gas = round(gas + heat / IDEAL_HEAT_EFF, 1)
                # 記在 District 的可能是 DesignBuilder 的理想負載空調, 也可能只是熱水系統, 所以不說是哪一種
                parts = [f"{cool:,.0f} 度冷量以冷氣 COP {IDEAL_COOL_COP:g} 換成用電" if cool else "",
                         f"{heat:,.0f} 度熱量(暖氣或熱水)以瓦斯鍋爐效率 {IDEAL_HEAT_EFF:.0%} 換成天然氣" if heat else ""]
                district = "模型有部分冷 / 熱是用「區域能源」表示,沒有實際設備:" + "、".join(p for p in parts if p)
    return {"success": bool(payload.get("success")), "elec": elec, "gas": gas, "district": district,
            "cool_dist": cool, "heat_dist": heat,
            "seconds": round(time.time() - started, 1), "out_dir": out_dir,
            "error": (payload.get("error") or "")[:100]}


def mcp_retrofit(client, tool, src: Path, out_path: Path):
    """呼叫本機新增的 upgrade_insulation / upgrade_hvac_efficiency, 回傳 (IDF 路徑或 None, 修改清單)。"""
    out_path = out_path.resolve()
    out_path.unlink(missing_ok=True)
    data = parse(client.tool(tool, {"idf_path": str(src), "output_path": str(out_path)}, 120))
    changes = data.get("modifications_made") or []
    return (out_path if data.get("success") and changes and out_path.exists() else None), changes


def hvac_label(kind):
    if kind.startswith("CHILLER") or kind == "HVACTEMPLATE:PLANT:CHILLER":
        return "冰水主機 COP"
    if "DX" in kind:
        return "冷氣 COP"
    if "BOILER" in kind:
        return "鍋爐效率"
    if kind == "COIL:HEATING:FUEL":
        return "瓦斯加熱效率"
    return "風扇效率"


def retrofit_measures(client, name, src: Path, base, models_dir: Path):
    """沒有參數變體的模型(DesignBuilder / 範例)用 MCP 工具做加強隔熱與高效率空調。回傳 [(措施, 結果, 說明)]。"""
    out = []
    path, ch = mcp_retrofit(client, "upgrade_insulation", src, models_dir / f"{name}__insul.idf")
    if path:
        label, first = {"wall": "外牆", "roof": "屋頂"}, {}
        for c in ch:  # 同類可能有好幾種構造, 每類只列第一個
            first.setdefault(c["type"], c)
        note = "、".join(f"{label[t]} U {c['u_old']:.2f} → {c['u_new']:.2f}" for t, c in first.items()) + \
            " W/m²K(在原構造加一層保溫)"
        out.append(("insulation", run(client, path), note))
    else:
        out.append(("insulation", failed("牆和屋頂已經達到目標保溫(外牆 U ≤ 0.25、屋頂 ≤ 0.18),或沒有外露的牆與屋頂"), ""))

    path, ch = mcp_retrofit(client, "upgrade_hvac_efficiency", src, models_dir / f"{name}__hvac.idf")
    if path:
        seen = {}
        for c in ch:  # 用和參數模型一樣的白話名稱, 每類只列第一個
            seen.setdefault(hvac_label(c["object"]), c)
        note = "、".join(f"{k} {c['old']:.2f} → {c['new']:.2f}" for k, c in seen.items())
        out.append(("hvac_eff", run(client, path), note))
    elif base.get("success") and (base.get("cool_dist") or base.get("heat_dist")):
        # 理想負載沒有實際設備: 換算用的冷氣 COP 與鍋爐效率提高, 不必另跑模擬
        cool, heat = base["cool_dist"], base["heat_dist"]
        res = dict(base, elec=round(base["elec"] - cool / IDEAL_COOL_COP + cool / (IDEAL_COOL_COP * 1.25), 1),
                   gas=round(base["gas"] - heat / IDEAL_HEAT_EFF + heat / 0.95, 1), seconds=0, error="")
        out.append(("hvac_eff", res, f"理想負載沒有實際設備,改用換算效率:冷氣 COP {IDEAL_COOL_COP:g} → "
                                     f"{IDEAL_COOL_COP * 1.25:g}、鍋爐效率 {IDEAL_HEAT_EFF:.0%} → 95%"))
    else:
        out.append(("hvac_eff", failed("找不到可以提高效率的空調設備(或效率欄位是自動計算)"), ""))
    return out


def failed(error):
    return {"success": False, "elec": None, "gas": None, "seconds": 0, "out_dir": None, "error": error}


def apply(client, tool, args, out_path: Path):
    """呼叫 MCP 修改工具寫出 out_path, 失敗回傳 None。

    先刪掉上一輪留下的同名檔: 不然工具失敗時會默默拿舊檔去跑模擬。
    """
    out_path = out_path.resolve()  # MCP server 的 cwd 不同, 回傳相對路徑給下一個工具會找不到
    out_path.unlink(missing_ok=True)
    text = client.tool(tool, {**args, "output_path": str(out_path)}, 120)
    if parse(text).get("success") and out_path.exists():
        return out_path
    print(f"  {tool} 失敗: {text[:120]}", flush=True)
    return None


def areas(output_dir: Path):
    """從 eio 取樓地板面積與外窗面積, 都乘上 Zone Multiplier × Zone List Multiplier。

    樓地板只算有 Lights 的 Zone: plenum 在 eio 裡也標成建築面積的一部分,
    但不裝燈, 算進去會讓照明投資與 EUI 的分母多一倍。
    """
    eio = next(output_dir.glob("*.eio"), None)
    if not eio:
        return None, None
    zones = {}   # zone 名稱 -> (樓地板, 外窗, 倍數)
    lit = set()
    zi = li = None

    def num(parts, i):
        try:
            return float(parts[i])
        except (IndexError, ValueError):
            return 0.0

    with open(eio, encoding="latin-1") as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if parts[0] == "! <Zone Information>":
                zi = {k: next(i for i, p in enumerate(parts) if p.startswith(k))
                      for k in ("Zone Name", "Zone Multiplier", "Zone List Multiplier",
                                "Floor Area", "Exterior Window Area")}
            elif parts[0] == "! <Lights Internal Gains Nominal>":
                li = parts.index("Zone Name")
            elif parts[0] == "Zone Information" and zi:
                mult = num(parts, zi["Zone Multiplier"]) * num(parts, zi["Zone List Multiplier"])
                zones[parts[zi["Zone Name"]].upper()] = (
                    num(parts, zi["Floor Area"]), num(parts, zi["Exterior Window Area"]), mult)
            elif parts[0] == "Lights Internal Gains Nominal" and li is not None and li < len(parts):
                lit.add(parts[li].upper())
    floor = sum(fa * m for name, (fa, _, m) in zones.items() if name in lit)
    glass = sum(ga * m for _, ga, m in zones.values())
    return round(floor, 1), round(glass, 1)


def scan_models(client, models, models_dir: Path):
    """對 [(名稱, IDF 路徑[, 變體]), ...] 跑基準 + 全部措施, 回傳結果列。db_import.py 也用這個。
    變體: {"insulation" / "hvac_eff": (已準備好的 IDF 或 None, 說明)}, 只有 build_idf.py 建的模型才有。"""
    rows = []
    for name, src, *rest in models:
        extras = rest[0] if rest else {}
        src = Path(src).resolve()
        if not src.exists():
            print(f"跳過(找不到模型) {src}", flush=True)
            continue

        base = run(client, src)
        floor, glass = areas(Path(base["out_dir"])) if base["out_dir"] else (None, None)

        glz = glazing(Path(base["out_dir"])) if base["out_dir"] else None
        fargs, film_skip = film_args(glz)
        notes = {"baseline": base.get("district", "")}
        if fargs:
            notes["film_shgc0.30"] = notes["lights+film"] = \
                f"原窗 SHGC {glz[1]:.2f} → 貼膜後 {FILM_SHGC:.2f}(U 值維持 {glz[0]:.2f})"

        # 單一措施: 照明 -30%
        try:
            mods = lighting_scaled_updates(client, src, 0.7)
        except Exception as e:  # inspect_lights 回傳格式不對時, 只讓這個模型的照明措施失敗
            print(f"  inspect_lights 失敗: {e}", flush=True)
            mods = []
        lit_path = apply(client, "modify_lights", {"idf_path": str(src), "modifications": mods},
                         models_dir / f"{name}__lights.idf") if mods else None
        lit = run(client, lit_path) if lit_path else failed("照明修改失敗")

        # 單一措施: 貼膜
        film_path = apply(client, "add_window_film_outside", {"idf_path": str(src), **fargs},
                          models_dir / f"{name}__film.idf") if fargs else None
        film = run(client, film_path) if film_path else failed(film_skip or "貼膜修改失敗")

        # 組合: 照明 + 貼膜 (在照明成果上再貼膜); 任一個做不了就不組合, 免得「組合」其實只有一項
        combo_path = apply(client, "add_window_film_outside", {"idf_path": str(lit_path), **fargs},
                           models_dir / f"{name}__combo.idf") if (lit_path and fargs) else None
        combo = run(client, combo_path) if combo_path else failed(
            f"不做組合:{film_skip}" if film_skip else "照明或貼膜修改失敗, 無法組合")

        # 單一措施: 滲風 x0.5 (沒有成本假設, 只看省多少, 不算回收期)
        infil_path = apply(client, "change_infiltration_by_mult", {"idf_path": str(src), "mult": 0.5},
                           models_dir / f"{name}__infil.idf")
        infil = run(client, infil_path) if infil_path else failed("滲風修改失敗(模型可能沒有 infiltration 物件)")

        # 單一措施: 屋頂反射塗料(冷屋頂), 改屋頂外層材料的日射吸收率
        roof = roof_area(Path(base["out_dir"])) if base["out_dir"] else None
        cool_path, cool_skip = coolroof(client, src, models_dir / f"{name}__coolroof.idf")
        cool = run(client, cool_path) if cool_path else failed(cool_skip)
        if cool_path:
            notes["coolroof"] = cool_skip  # 這時放的是原本的吸收率說明

        # 單一措施: 節能設備(沒有成本假設, 設備汰換時順便選節能款, 只看省多少)
        try:
            emods = scaled_updates(client, "inspect_electric_equipment", "electric_equipment_objects", src,
                                   EQUIP_FACTOR, ("Design_Level", "design_level"))
        except Exception as e:
            print(f"  inspect_electric_equipment 失敗: {e}", flush=True)
            emods = []
        equip_path = apply(client, "modify_electric_equipment", {"idf_path": str(src), "modifications": emods},
                           models_dir / f"{name}__equip.idf") if emods else None
        equip = run(client, equip_path) if equip_path else failed(
            "模型沒有一般用電設備物件(ElectricEquipment),這項不適用" if not emods else "設備修改失敗")

        # 換構造 / 換設備的措施: MCP 沒有工具, 由 build_idf.py 另建的變體模型直接模擬(見 db_import.variants)
        var_results = []
        if extras:  # 參數模型: 用 build_idf.py 另建的變體(構造庫的真實構造、OpenStudio 改的設備)
            for key in ("insulation", "hvac_eff"):
                if key in extras:
                    path, note = extras[key]
                    var_results.append((key, run(client, Path(path)) if path else failed(note), key))
                    if path:
                        notes[key] = note
        else:  # DesignBuilder / 範例模型: 用本機新增的 MCP 工具直接改 IDF
            for key, res, note in retrofit_measures(client, name, src, base, models_dir):
                var_results.append((key, res, key))
                if note:
                    notes[key] = note
        walls = roof_area(Path(base["out_dir"]), "Wall") if base["out_dir"] else None

        capex = {
            "lights": (floor or 0) * COSTS["lighting_per_m2_floor"],
            "film": (glass or 0) * COSTS["film_per_m2_glass"],
            "coolroof": (roof or 0) * COSTS["coolroof_per_m2_roof"],
            "insulation": ((walls or 0) + (roof or 0)) * COSTS["insulation_per_m2_envelope"],
            "hvac_eff": (floor or 0) * COSTS["hvac_per_m2_floor"],
        }
        capex["combo"] = capex["lights"] + capex["film"]

        for label, res, key in [("baseline", base, None), ("lights_-30%", lit, "lights"),
                                ("film_shgc0.30", film, "film"), ("lights+film", combo, "combo"),
                                ("infil_x0.5", infil, None), ("coolroof", cool, "coolroof"),
                                ("equip_-20%", equip, None), *var_results]:
            # 省電與省氣分開算: 照明/貼膜常會讓暖房的天然氣變多, 只看電會高估效益
            ok = base["elec"] is not None and res["elec"] is not None
            total = res["elec"] + res["gas"] if res["elec"] is not None else None
            base_total = base["elec"] + base["gas"] if base["elec"] is not None else None
            s_elec = base["elec"] - res["elec"] if ok else None
            s_gas = base["gas"] - res["gas"] if ok else None
            saving_cost = (s_elec * COSTS["price_per_kwh"] + s_gas * COSTS["gas_per_kwh"]) if ok else None
            capex_val = capex.get(key) if key else 0
            parts = {
                "model": name,
                "case": label,
                "success": res["success"],
                "elec_kwh": res["elec"],
                "gas_kwh": res["gas"],
                "kwh": round(total, 1) if total is not None else None,
                "saving_elec_kwh": round(s_elec, 1) if ok else None,
                "saving_gas_kwh": round(s_gas, 1) if ok else None,
                "saving_pct": round((base_total - total) / base_total * 100, 1) if (ok and base_total) else None,
                "saving_cost": round(saving_cost) if ok else None,
                "capex_cost": round(capex_val) if capex_val else 0,
                "payback_years": round(capex_val / saving_cost, 1)
                if (saving_cost and saving_cost > 0 and capex_val) else None,
                "currency": COSTS["currency"],
                "seconds": res["seconds"],
                "error": res["error"] or notes.get(label, ""),
                "floor_m2": floor,
                "glass_m2": glass,
                "eui_kwh_m2": round(total / floor, 1) if (total is not None and floor) else None,
            }
            rows.append(parts)
            print(f"{name:38s} {label:14s} kwh={parts['kwh']} save={parts['saving_pct']}% "
                  f"payback={parts['payback_years']}", flush=True)
    return rows


def scan_parallel(models, models_dir: Path, workers: int = min(4, os.cpu_count() or 1)):
    """每個模型交給一個獨立的 MCP server 跑 scan_models; 結果照輸入順序回傳。

    模型之間互不相依, 同一模型的措施仍依序跑(組合要用照明的成果)。
    """
    workers = max(1, min(workers, len(models)))
    clients = queue.Queue()
    for _ in range(workers):
        clients.put(Mcp())

    def one(model):
        client = clients.get()
        try:
            return scan_models(client, [model], models_dir)
        except Exception as e:  # 一個模型出錯不拖垮整輪
            print(f"{model[0]} 中止: {e}", flush=True)
            return []
        finally:
            if client.p.poll() is not None:  # server 掛了就換一個新的
                client.close()
                client = Mcp()
            clients.put(client)

    try:
        with ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(one, models))
    finally:
        while not clients.empty():
            clients.get().close()
    return [r for rows in results for r in rows]


def write_outputs(rows, out_dir: Path, meta=None):
    """寫出 results.csv、report.md 與給一般人看的 report.html。meta: {模型名: 建模參數}。"""
    if not rows:
        raise SystemExit("沒有任何結果可寫出")
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    lines = ["# 措施組合與回收期", "",
             f"- 貨幣 {COSTS['currency']}; 電價: {COSTS['price_per_kwh']}/kWh; 天然氣: {COSTS['gas_per_kwh']}/kWh(costs.json)",
             f"- 貼膜: {COSTS['film_per_m2_glass']}/m² 玻璃; 照明: {COSTS['lighting_per_m2_floor']}/m² 樓地板(有照明的 Zone); "
             f"屋頂反射漆: {COSTS['coolroof_per_m2_roof']}/m² 屋頂",
             "- 年省 % 以總能源(電 + 天然氣)計",
             "", f"| 模型 | 情境 | 用電 kWh | 天然氣 kWh | EUI kWh/m² | 年省 % | 年省 {COSTS['currency']} | 投資 {COSTS['currency']} | 回收年 | 備註 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['model']} | {r['case']} | {r['elec_kwh'] or ''} | {r['gas_kwh'] if r['gas_kwh'] is not None else ''} | "
                     f"{r['eui_kwh_m2'] or ''} | {r['saving_pct'] if r['saving_pct'] is not None else ''} | "
                     f"{r['saving_cost'] or ''} | {r['capex_cost'] or ''} | {r['payback_years'] or ''} | {r['error']} |")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n網頁報告: {write_html(rows, out_dir / 'report.html', COSTS, meta)}")


def main():
    OUT.mkdir(exist_ok=True)
    models_dir = OUT / "models"
    models_dir.mkdir(exist_ok=True)
    rows = scan_parallel([(n, MODELS_DIR / f"{n}.idf") for n in MODELS], models_dir)
    write_outputs(rows, OUT)


if __name__ == "__main__":
    main()
