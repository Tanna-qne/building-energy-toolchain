"""完整流程(給 Dify 表單工作流用): 建模或既有模型 → 地點 → ASHRAE 構造 → 年度模擬 → 節能措施, 回傳結構化結果。

一個流程是一個背景工作(server.db_job_start("db_workflow", ...)), 依序呼叫既有的 MCP 工具; 每一步的數值都來自
DB 範本庫或模擬輸出, 這裡不產生數字。表單送來的都是字串, 先全部檢查過才開始開 DB(免得跑了幾分鐘才發現填錯)。
"""
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

import dblibrary
import server
from building_report import MEASURES, num, verdict
from eplus_summary import summarize

SHAPE_FIELDS = {"rect": (), "L": ("notch_width", "notch_depth"), "U": ("notch_width", "notch_depth"),
                "T": ("stem_width", "bar_depth"), "courtyard": ("court_width", "court_depth")}
MEASURES_TIMEOUT_S = 40 * 60


def _f(form, key, required=True):
    v = str(form.get(key, "") or "").strip()
    if not v:
        if required:
            raise ValueError(f"缺少「{key}」")
        return None
    try:
        return float(v)
    except ValueError:
        raise ValueError(f"「{key}」要是數字, 收到 {v!r}")


def parse_uses(text):
    """"1:大廳; 2-:辦公" → {"1": "大廳", "2-": "辦公"}; 用途要在 DB 對照表裡。"""
    uses = {}
    for part in re.split(r"[;；,，\n]+", text or ""):
        if not part.strip():
            continue
        if ":" not in part.replace("：", ":"):
            raise ValueError(f"各層用途的寫法是「樓層:用途」, 例如「1:大廳; 2-:辦公」, 看不懂 {part.strip()!r}")
        floors, use = (x.strip() for x in part.replace("：", ":").split(":", 1))
        dblibrary.resolve_space(use)  # 不認得的用途拋 KeyError, 列出可用的
        uses[floors] = use
    return uses


def resolve_location(text):
    """地點: 編號、完整名稱, 或只對到一個地點的關鍵字; 對到多個時列出候選, 不自己挑。"""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return dblibrary.resolve_location(text)
    except KeyError:
        pass
    rows, total = dblibrary.find_locations(text, 8)
    if total == 1:
        return rows[0]
    if not total:
        raise ValueError(f"DB 地點庫裡找不到「{text}」; 試試英文城市名, 例如 Chicago、New York、Taipei")
    raise ValueError(f"「{text}」對到 {total} 個地點, 請填編號: " +
                     "; ".join(f"{r['Id']} {r['LocationName']}" for r in rows))


def form_to_params(form):
    """表單(字串) → 流程參數; 有錯直接拋 ValueError, 訊息給使用者看。"""
    mode = str(form.get("mode", "") or "new").strip()
    mode = {"新建模型": "new", "分析既有模型": "existing"}.get(mode, mode)
    if mode not in ("new", "existing"):
        raise ValueError("mode 要是「新建模型」或「分析既有模型」")
    p = {"mode": mode, "measures": str(form.get("run_measures", "是")).strip() in ("是", "yes", "true", "1", "True"),
         "building_type": str(form.get("building_type", "") or "非住宅").strip()}
    if p["building_type"] not in dblibrary.BUILDING_TYPES:
        raise ValueError(f"建築類型只能是 {'、'.join(dblibrary.BUILDING_TYPES)}")
    loc = resolve_location(form.get("location"))
    if loc:
        if not dblibrary.weather_of(loc)[1]:
            raise ValueError(f"地點 {loc['LocationName']} 的氣象檔不在 DB 電腦上; 請先在 DB 的地點設定下載, "
                             f"或用 db_set_location 提供 EPW 檔")
        p["location"] = loc
    if mode == "existing":
        dsb = str(form.get("dsb_path", "") or "").strip().strip('"')
        if not dsb:
            raise ValueError("分析既有模型要填模型路徑(.dsb)")
        server._guard(dsb)
        if not Path(dsb).exists():
            raise ValueError(f"找不到模型 {dsb}")
        p["dsb_path"] = dsb
        p["constructions"] = str(form.get("apply_constructions", "否")).strip() in ("是", "yes", "true", "1")
        return p
    if not loc:
        raise ValueError("新建模型要填地點(用來決定氣象檔與 ASHRAE 氣候區)")
    shape = str(form.get("shape", "") or "rect").strip()
    name = str(form.get("name", "") or "").strip() or f"model_{datetime.now():%Y%m%d_%H%M%S}"
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError(f"模型名稱只能用英文、數字、底線、減號, 收到 {name!r}")
    m = {"name": name, "shape": shape, "uses": parse_uses(form.get("uses", "")),
         "default_use": str(form.get("default_use", "") or "辦公").strip()}
    dblibrary.resolve_space(m["default_use"])
    for k in ("floor_height", "wwr"):
        v = _f(form, k, required=False)
        if v is not None:
            m[k] = v
    if shape == "podium_tower":
        m["podium"] = {"width": _f(form, "width"), "depth": _f(form, "depth"), "stories": int(_f(form, "stories"))}
        m["tower"] = {"width": _f(form, "tower_width"), "depth": _f(form, "tower_depth"),
                      "stories": int(_f(form, "tower_stories"))}
        for k in ("offset_x", "offset_y"):
            v = _f(form, f"tower_{k}", required=False)
            if v is not None:
                m["tower"][k] = v
    elif shape in SHAPE_FIELDS:
        m.update(width=_f(form, "width"), depth=_f(form, "depth"), stories=int(_f(form, "stories")))
        for k in SHAPE_FIELDS[shape]:
            m[k] = _f(form, k)
    else:
        raise ValueError(f"量體只能是 {', '.join(list(SHAPE_FIELDS) + ['podium_tower'])}")
    p["model"] = m
    return p


def _measures(sim: Path):
    """跑節能措施並等它算完(背景程序約 5–10 分鐘), 回傳 (措施清單, 幣別, 問題)。"""
    server.db_measures_report(str(sim))
    start = time.time()
    while time.time() - start < MEASURES_TIMEOUT_S:
        time.sleep(15)
        log = (sim / "run.log").read_text(encoding="utf-8", errors="replace") if (sim / "run.log").exists() else ""
        if "Traceback" in log:
            return [], "", "節能措施計算失敗:" + log.strip().splitlines()[-1]
        if "完成" in log and (sim / "results.csv").exists():
            break
    else:
        return [], "", f"節能措施超過 {MEASURES_TIMEOUT_S // 60} 分鐘還沒算完"
    with open(sim / "results.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if r["case"] not in MEASURES:
            continue
        level, _, label, why = verdict(r)
        out.append({"measure": MEASURES[r["case"]][0], "what": MEASURES[r["case"]][1], "verdict": label, "why": why,
                    "saving_pct": num(r["saving_pct"]), "saving_per_year": num(r["saving_cost"]),
                    "investment": num(r["capex_cost"]), "payback_years": num(r["payback_years"]), "level": level})
    order = {"good": 0, "ok": 1, "info": 2, "bad": 3, "skip": 4}
    out.sort(key=lambda m: order[m["level"]])
    return out, rows[0].get("currency", "") if rows else "", None


def run(form):
    """跑完整流程, 回傳給 Dify 的結構化結果。每一步的工具輸出也放在 steps, 方便追查。"""
    p = form_to_params(form)
    steps, warnings, out = [], [], {"mode": p["mode"]}

    def step(name, fn, *a, **k):
        t = time.time()
        text = fn(*a, **k)
        steps.append({"step": name, "seconds": round(time.time() - t), "output": text})
        if "不一致" in text or "失敗" in text:
            warnings.append(f"{name}:有項目沒通過核對,細節見 steps")
        return text

    loc = p.get("location")
    if p["mode"] == "new":
        m = p["model"]
        step("建模與各層用途", server.db_build_model, m, location=loc["Id"])
        dsb = server.WORK / m["name"] / f"{m['name']}.dsb"
        info = json.loads((dsb.parent / f"{m['name']}_zones.json").read_text(encoding="utf-8"))
        out["model"] = {"dsb": str(dsb), "plans_html": info["plans"], "floors": info["floors"],
                        "zones": len(info["zones"]), "floor_m2_outline": info["floor_m2"],
                        "uses_by_floor": {z["block"]: z["use"] for z in info["zones"]}}
    else:
        dsb = server._working_copy(p["dsb_path"])
        out["model"] = {"dsb": str(dsb), "source": p["dsb_path"]}
        if loc:
            step("地點與氣象檔", server.db_set_location, str(dsb), loc["Id"], apply=True)
    if loc:
        out["location"] = {"id": loc["Id"], "name": loc["LocationName"], "climate_zone": loc.get("ClimaticRegion", ""),
                           "latitude": loc["Latitude"], "longitude": loc["Longitude"],
                           "weather_file": dblibrary.weather_of(loc)[0].get("Filename", "")}
    if p["mode"] == "new" or p.get("constructions"):
        cz = loc.get("ClimaticRegion") if loc else ""
        if not cz:
            warnings.append("沒有地點的氣候區,用預設 4A 套構造")
            cz = "4A"
        step("ASHRAE 基準構造", server.db_assign_construction, str(dsb), cz, p["building_type"], apply=True)
        tmpl, glazing = dblibrary.baseline_envelope(cz, p["building_type"])
        out["constructions"] = {"climate_zone": cz, "template": tmpl["Name"], "glazing": glazing["Name"]}
    text = step("年度模擬", server.db_simulate, str(dsb))
    sim = max(dsb.parent.glob("sim_*"), key=lambda q: q.stat().st_mtime)
    if not (sim / "eplustbl.htm").exists() or "模擬沒有成功" in text:
        raise RuntimeError("年度模擬沒有成功:" + text[:800])
    s = summarize(sim / "eplustbl.htm")
    out["simulation"] = {"output_dir": str(sim), "weather": s["weather"], "site_kWh": round(s["site_kWh"]),
                         "floor_m2": round(s["floor_m2"], 1), "eui_kWh_m2": round(s["eui_kWh_m2"], 1),
                         "end_uses": [{"name": e["name"], "kWh": round(e["kWh"]),
                                       "share_pct": round(100 * e["kWh"] / s["site_kWh"], 1)} for e in s["end_uses"]],
                         "by_fuel_kWh": {k: round(v) for k, v in s["by_fuel_kWh"].items()},
                         "peak_electricity_kW": round(s["peak_elec_kW"], 1), "peak_time": s["peak_elec_time"],
                         "unmet_heating_h": s["unmet_heating_h"], "unmet_cooling_h": s["unmet_cooling_h"],
                         "district_loads": any("District" in k for k in s["by_fuel_kWh"])}
    if p["measures"]:
        t = time.time()
        measures, currency, problem = _measures(sim)
        steps.append({"step": "節能措施", "seconds": round(time.time() - t), "output": problem or f"{len(measures)} 項"})
        if problem:
            warnings.append(problem)
        out["measures"] = {"currency": currency, "items": measures, "report_html": str(sim / "report.html"),
                           "cost_note": "投資金額與能源價格是 costs.json 的假設值,不是報價"}
    out["warnings"] = warnings
    out["steps"] = steps
    return out
