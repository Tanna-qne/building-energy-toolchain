"""節能措施掃描: 對一批模型套用不同措施, 用 MCP 跑模擬, 比較總用電。

措施全部透過 EnergyPlus-MCP 的工具完成(變更 + 模擬), 這裡只負責排程與匯總。
"""
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from batch_driver import Mcp, meter_kwh, sim_args, MODELS as MODELS_DIR  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCAN = Path(__file__).resolve().parent
MODELS = [
    "5ZoneAirCooled",
    "5ZoneAirCooledConvCoef",
    "5ZoneAirCooledDemandLimiting",
    "HVACTemplate-5ZoneVAVFanPowered",
    "HVACTemplate-5ZoneBaseboardHeat",
]


def parse(text):
    try:
        return json.loads(text.split("\n", 1)[1])
    except (IndexError, json.JSONDecodeError):
        return {"success": False, "error": text[:160]}


def run(client, idf: Path):
    started = time.time()
    payload = parse(client.tool("run_energyplus_simulation", sim_args(idf), 900))
    out_dir = payload.get("output_directory")
    kwh = meter_kwh(Path(out_dir), "Electricity:Facility") if out_dir and payload.get("success") else None
    # 暖房多半燒天然氣, 只看電會讓滲風這類措施看起來沒效果; 沒有天然氣電表的模型當作 0
    gas = (meter_kwh(Path(out_dir), "NaturalGas:Facility") or 0.0) if kwh is not None else None
    return {"success": bool(payload.get("success")), "kwh": kwh, "gas_kwh": gas,
            "seconds": round(time.time() - started, 1), "error": (payload.get("error") or "")[:100]}


def lighting_scaled_updates(client, idf: Path, factor: float = 0.7):
    """把每個 Lights 物件按其計算方式縮放 30%, 回傳 modify_lights 的 modifications。"""
    text = client.tool("inspect_lights", {"idf_path": str(idf.resolve())}, 120)
    data = json.loads(text.split("\n", 1)[1])
    mods = []
    for obj in data.get("lights_objects", []):
        name = obj.get("name")
        if not name:
            continue
        method = (obj.get("design_level_calculation_method") or "").lower()
        if "watt" in method and "area" in method:
            key, raw = "Watts_per_Floor_Area", obj.get("watts_per_floor_area")
        elif "person" in method:
            key, raw = "Watts_per_Person", obj.get("watts_per_person")
        else:
            key, raw = "Lighting_Level", obj.get("lighting_level")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        mods.append({"target": f"name:{name}", "field_updates": {key: round(value * factor, 2)}})
    return mods


def main():
    SCAN.mkdir(exist_ok=True)
    models_dir = SCAN / "models"
    models_dir.mkdir(exist_ok=True)
    client = Mcp()
    rows = []

    for name in MODELS:
        src = MODELS_DIR / f"{name}.idf"
        if not src.exists():
            print(f"跳過(找不到模型) {name}")
            continue

        base = run(client, src)
        rows.append({"model": name, "measure": "baseline", **base})
        print(f"{name:40s} baseline  ok={base['success']} {base['kwh']}", flush=True)

        # 措施 1: 滲風量 x0.5
        out1 = models_dir / f"{name}__infil.idf"
        r1 = client.tool("change_infiltration_by_mult",
                         {"idf_path": str(src.resolve()), "mult": 0.5, "output_path": str(out1.resolve())}, 120)
        if parse(r1).get("success") and out1.exists():
            res = run(client, out1)
        else:
            res = {"success": False, "kwh": None, "gas_kwh": None, "seconds": 0, "error": "工具無法套用(模型沒有 infiltration 物件)"}
        rows.append({"model": name, "measure": "infil_x0.5", **res})
        print(f"{name:40s} infil     ok={res['success']} {res['kwh']}", flush=True)

        # 措施 2: 外窗貼膜 (SHGC 0.3)
        out2 = models_dir / f"{name}__film.idf"
        r2 = client.tool("add_window_film_outside",
                         {"idf_path": str(src.resolve()), "u_value": 3.0, "shgc": 0.30,
                          "visible_transmittance": 0.55, "output_path": str(out2.resolve())}, 120)
        if parse(r2).get("success") and out2.exists():
            res = run(client, out2)
        else:
            res = {"success": False, "kwh": None, "gas_kwh": None, "seconds": 0, "error": "工具無法套用(模型沒有外窗)"}
        rows.append({"model": name, "measure": "film_shgc0.30", **res})
        print(f"{name:40s} film      ok={res['success']} {res['kwh']}", flush=True)

        # 措施 3: 照明 -30%
        out3 = models_dir / f"{name}__lights.idf"
        try:
            mods = lighting_scaled_updates(client, src, 0.7)
        except Exception:
            mods = []
        if mods:
            r3 = client.tool("modify_lights",
                             {"idf_path": str(src.resolve()), "modifications": mods,
                              "output_path": str(out3.resolve())}, 120)
            if parse(r3).get("success") and out3.exists():
                res = run(client, out3)
            else:
                res = {"success": False, "kwh": None, "gas_kwh": None, "seconds": 0, "error": "修改照明失敗:" + r3[:80]}
        else:
            res = {"success": False, "kwh": None, "gas_kwh": None, "seconds": 0, "error": "讀不到照明物件"}
        rows.append({"model": name, "measure": "lights_-30%", **res})
        print(f"{name:40s} lights    ok={res['success']} {res['kwh']}", flush=True)

    client.close()

    # 匯總: 相對 baseline 的變化
    base_kwh = {r["model"]: r["kwh"] for r in rows if r["measure"] == "baseline"}
    base_site = {r["model"]: r["kwh"] + r["gas_kwh"] for r in rows
                 if r["measure"] == "baseline" and r["kwh"] is not None}
    for r in rows:
        b = base_kwh.get(r["model"])
        r["delta_pct"] = round((r["kwh"] / b - 1) * 100, 1) if (r["kwh"] and b) else None
        bs = base_site.get(r["model"])
        site = r["kwh"] + r["gas_kwh"] if r["kwh"] is not None else None
        r["site_delta_pct"] = round((site / bs - 1) * 100, 1) if (site and bs) else None

    with open(SCAN / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["model", "measure", "success", "kwh", "gas_kwh", "delta_pct",
                                          "site_delta_pct", "seconds", "error"])
        w.writeheader()
        w.writerows(rows)

    def pct(v, ok):
        return f"{v:+.1f}%" if v is not None else ("n/a" if not ok else "")

    lines = ["# 節能措施掃描", "",
             f"- 模型數: {len(base_kwh)}",
             f"- 情境數: {len(rows)}",
             f"- 總耗時: {sum(r['seconds'] for r in rows):.0f} 秒",
             "- 總能源 = 用電 + 天然氣(kWh)", "",
             "| 模型 | 措施 | 總用電 kWh | 天然氣 kWh | 用電相對基準 | 總能源相對基準 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        gas = r["gas_kwh"] if r["gas_kwh"] is not None else ""
        lines.append(f"| {r['model']} | {r['measure']} | {r['kwh'] or ''} | {gas} | "
                     f"{pct(r['delta_pct'], r['success'])} | {pct(r['site_delta_pct'], r['success'])} |")
    (SCAN / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
