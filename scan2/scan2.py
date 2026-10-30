"""措施組合 + 成本/回收期: 在同一個模型上疊加措施, 算出年省電、年省錢、簡單回收期。

模擬與修改一樣走 EnergyPlus-MCP 工具; 成本假設集中在 COSTS, 要改只改這裡。
"""
import csv
import json
import os
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from batch_driver import Mcp, meter_kwh, sim_args, MODELS as MODELS_DIR  # noqa: E402
from scan.scan import lighting_scaled_updates, parse  # noqa: E402

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

COSTS = {
    "price_twd_per_kwh": 4.5,      # 商業用電, 假設值
    "gas_twd_per_kwh": 1.5,        # 天然氣約 16 TWD/m³ ÷ 10.5 kWh/m³, 假設值
    "film_twd_per_m2_glass": 1200,  # 貼膜含施工, 假設值
    "lighting_twd_per_m2_floor": 600,  # 燈具更新含施工, 假設值
}


def run(client, idf: Path):
    started = time.time()
    payload = parse(client.tool("run_energyplus_simulation", sim_args(idf), 900))
    elec = gas = None
    out_dir = payload.get("output_directory")
    if payload.get("success") and out_dir:
        elec = meter_kwh(Path(out_dir), "Electricity:Facility")
        # 沒有天然氣電表的模型當作 0 (全電)
        if elec is not None:
            gas = meter_kwh(Path(out_dir), "NaturalGas:Facility") or 0.0
    return {"success": bool(payload.get("success")), "elec": elec, "gas": gas,
            "seconds": round(time.time() - started, 1), "out_dir": out_dir,
            "error": (payload.get("error") or "")[:100]}


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
    """對 [(名稱, IDF 路徑), ...] 跑基準 + 照明 / 貼膜 / 組合, 回傳結果列。db_import.py 也用這個。"""
    rows = []
    for name, src in models:
        src = Path(src).resolve()
        if not src.exists():
            print(f"跳過(找不到模型) {src}", flush=True)
            continue

        base = run(client, src)
        floor, glass = areas(Path(base["out_dir"])) if base["out_dir"] else (None, None)

        film_args = {"u_value": 3.0, "shgc": 0.30, "visible_transmittance": 0.55}

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
        film_path = apply(client, "add_window_film_outside", {"idf_path": str(src), **film_args},
                          models_dir / f"{name}__film.idf")
        film = run(client, film_path) if film_path else failed("貼膜修改失敗")

        # 組合: 照明 + 貼膜 (在照明成果上再貼膜); 照明失敗就不做, 免得「組合」其實只有貼膜
        combo_path = apply(client, "add_window_film_outside", {"idf_path": str(lit_path), **film_args},
                           models_dir / f"{name}__combo.idf") if lit_path else None
        combo = run(client, combo_path) if combo_path else failed("照明或貼膜修改失敗, 無法組合")

        # 單一措施: 滲風 x0.5 (沒有成本假設, 只看省多少, 不算回收期)
        infil_path = apply(client, "change_infiltration_by_mult", {"idf_path": str(src), "mult": 0.5},
                           models_dir / f"{name}__infil.idf")
        infil = run(client, infil_path) if infil_path else failed("滲風修改失敗(模型可能沒有 infiltration 物件)")

        capex = {
            "lights": (floor or 0) * COSTS["lighting_twd_per_m2_floor"],
            "film": (glass or 0) * COSTS["film_twd_per_m2_glass"],
        }
        capex["combo"] = capex["lights"] + capex["film"]

        for label, res, key in [("baseline", base, None), ("lights_-30%", lit, "lights"),
                                ("film_shgc0.30", film, "film"), ("lights+film", combo, "combo"),
                                ("infil_x0.5", infil, None)]:
            # 省電與省氣分開算: 照明/貼膜常會讓暖房的天然氣變多, 只看電會高估效益
            ok = base["elec"] is not None and res["elec"] is not None
            total = res["elec"] + res["gas"] if res["elec"] is not None else None
            base_total = base["elec"] + base["gas"] if base["elec"] is not None else None
            s_elec = base["elec"] - res["elec"] if ok else None
            s_gas = base["gas"] - res["gas"] if ok else None
            saving_twd = (s_elec * COSTS["price_twd_per_kwh"] + s_gas * COSTS["gas_twd_per_kwh"]) if ok else None
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
                "saving_twd": round(saving_twd) if ok else None,
                "capex_twd": round(capex_val) if capex_val else 0,
                "payback_years": round(capex_val / saving_twd, 1)
                if (saving_twd and saving_twd > 0 and capex_val) else None,
                "seconds": res["seconds"],
                "error": res["error"],
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


def write_outputs(rows, out_dir: Path):
    """寫出 results.csv 與 report.md。"""
    if not rows:
        raise SystemExit("沒有任何結果可寫出")
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    lines = ["# 措施組合與回收期", "",
             f"- 電價假設: {COSTS['price_twd_per_kwh']} TWD/kWh; 天然氣: {COSTS['gas_twd_per_kwh']} TWD/kWh",
             f"- 貼膜: {COSTS['film_twd_per_m2_glass']} TWD/m² 玻璃; 照明: {COSTS['lighting_twd_per_m2_floor']} TWD/m² 樓地板(有照明的 Zone)",
             "- 年省 % 以總能源(電 + 天然氣)計",
             "", "| 模型 | 情境 | 用電 kWh | 天然氣 kWh | EUI kWh/m² | 年省 % | 年省 TWD | 投資 TWD | 回收年 | 備註 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['model']} | {r['case']} | {r['elec_kwh'] or ''} | {r['gas_kwh'] if r['gas_kwh'] is not None else ''} | "
                     f"{r['eui_kwh_m2'] or ''} | {r['saving_pct'] if r['saving_pct'] is not None else ''} | "
                     f"{r['saving_twd'] or ''} | {r['capex_twd'] or ''} | {r['payback_years'] or ''} | {r['error']} |")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    OUT.mkdir(exist_ok=True)
    models_dir = OUT / "models"
    models_dir.mkdir(exist_ok=True)
    rows = scan_parallel([(n, MODELS_DIR / f"{n}.idf") for n in MODELS], models_dir)
    write_outputs(rows, OUT)


if __name__ == "__main__":
    main()
