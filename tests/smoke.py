"""端到端檢查: 把每個重要功能實際跑一次, 檢查結果合理。改完程式、uv sync、升級 E+ 後都跑一次。

    python tests/smoke.py          # 全部(約 10 分鐘)
    python tests/smoke.py modeler  # 只跑名稱含 modeler 的項目

會覆寫 db_models/out、batch/、scan2/、report/ 的結果(內容與正常執行相同)。
"""
import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
FIX = HERE / "fixtures"
PY = sys.executable
OUT = WS / "db_models" / "out"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run(*args, timeout=1500):
    r = subprocess.run([PY, *map(str, args)], cwd=WS, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)


def by_case(rs, model):
    return {r["case"]: r for r in rs if r["model"] == model}


SKIP_OK = ("幫助不大", "不適用", "沒有幫助")  # 措施對這個模型不適用時的合理略過


def all_cases_ok(rs, model):
    """情境都要成功; 貼膜、組合、屋頂、設備可以因為不適用而略過(要有說明)。
    參數建模的模型有 9 個(多加強隔熱、高效率空調), DesignBuilder 與範例模型 7 個。"""
    cases = by_case(rs, model)
    check(len(cases) in (7, 9), f"{model}: 應有 7 或 9 個情境, 實際 {sorted(cases)}")
    for case, r in cases.items():
        skipped = case != "baseline" and any(w in r["error"] for w in SKIP_OK)
        check(r["success"] == "True" or skipped, f"{model} {case} 失敗: {r['error']}")
    return cases


def test_modeler_office():
    """參數 -> 建模 -> 全部措施 -> 網頁報告(長方形辦公, VAV + 冰水主機 + 鍋爐)。"""
    code, log = run(WS / "run_building.py", FIX / "office_rect.json")
    check(code == 0, f"run_building 失敗:\n{log[-1500:]}")
    cases = all_cases_ok(rows(OUT / "results.csv"), "demo_office")
    base = cases["baseline"]
    # 150.5 含熱水用熱(honeybee 把熱水記在區域供熱, 換算成天然氣); 不含熱水是 146.9
    check(abs(float(base["eui_kwh_m2"]) - 150.5) < 1.0, f"EUI 應約 150.5, 實際 {base['eui_kwh_m2']}")
    check(float(base["gas_kwh"]) > 0, "有鍋爐卻沒有天然氣: 空調沒進模型?")
    check(float(base["floor_m2"]) == 3000.0, f"樓地板應為 3000, 實際 {base['floor_m2']}")
    for case in ("lights_-30%", "infil_x0.5", "equip_-20%"):
        check(float(cases[case]["saving_pct"]) > 0, f"{case} 應該要省能源")
    check("U 值維持" in cases["film_shgc0.30"]["error"], "貼膜應沿用原窗 U 值")
    check("0.70 → 0.30" in cases["coolroof"]["error"], "屋頂反射漆應從 0.70 改成 0.30")
    for case, text in (("insulation", "外牆 U"), ("hvac_eff", "冰水主機 COP")):
        check(case in cases and text in cases[case]["error"] and float(cases[case]["saving_pct"]) > 0,
              f"參數模型應有「{case}」且要省能源: {cases.get(case)}")
    html = (OUT / "report.html").read_text(encoding="utf-8")
    for text in ("中型辦公", "中央空調(VAV)", "換省電燈具", "這些數字怎麼來的"):
        check(text in html, f"report.html 缺少「{text}」")


def test_modeler_shapes_and_hvac():
    """L 形零售(箱型冷氣 + 瓦斯)與小辦公(窗型冷氣 + 電暖器)都能建模並跑完。"""
    for fx, name, lo, hi in (("shop_L.json", "shop_L", 50, 600), ("small_office.json", "small_office", 30, 400)):
        code, log = run(WS / "run_building.py", FIX / fx)
        check(code == 0, f"{fx}: run_building 失敗:\n{log[-1500:]}")
        base = all_cases_ok(rows(OUT / "results.csv"), name)["baseline"]
        eui = float(base["eui_kwh_m2"])
        check(lo < eui < hi, f"{name}: EUI {eui} 不在合理範圍 {lo}–{hi}")
    floor = float(by_case(rows(OUT / "results.csv"), "small_office")["baseline"]["floor_m2"])
    check(floor == 240.0, f"small_office 樓地板應為 240, 實際 {floor}")


def test_compare_options():
    """兩個方案(同棟、不同空調)一起跑, 報告最前面要有並排比較; 金額依 costs.json 的貨幣。"""
    code, log = run(WS / "run_building.py", FIX / "option_vav.json", FIX / "option_psz.json")
    check(code == 0, f"run_building 多方案失敗:\n{log[-1500:]}")
    rs = rows(OUT / "results.csv")
    for name in ("office_VAV", "office_PSZ"):
        all_cases_ok(rs, name)
    cur = json.loads((WS / "costs.json").read_text(encoding="utf-8"))
    check(all(r["currency"] == cur["currency"] for r in rs), "結果的貨幣和 costs.json 不一致")
    html = (OUT / "report.html").read_text(encoding="utf-8")
    for text in ("方案比較", "一年能源費用最低的是", "最省", 'href="#office_PSZ"', cur["symbol"]):
        check(text in html, f"比較報告缺少「{text}」")


def test_modeler_bad_input():
    """參數填錯(選項不存在、欄位拼錯、L 形缺尺寸、JSON 格式錯): 一行說明原因, 不能噴 traceback。"""
    for fx, text in (("bad.json", "hvac 要是其中之一"), ("bad_typo.json", "不認得的參數 storeys"),
                     ("bad_L.json", "缺少 notch_width"), ("bad_json.json", "不是正確的 JSON")):
        code, log = run(WS / "run_building.py", FIX / fx, timeout=300)
        check(code != 0 and text in log and "Traceback" not in log, f"{fx}: 錯誤訊息不清楚:\n{log[-800:]}")


def test_db_import_upgrade_and_unicode():
    """舊版(25.1)IDF 自動升級; 中文檔名可以跑; 兩個模型平行。結果要和 26.2 範例一模一樣。"""
    tmp = WS / "db_models" / "test_smoke"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    shutil.copyfile(FIX / "old_v25_1.idf", tmp / "old_v25_1.idf")
    shutil.copyfile(FIX / "old_v25_1.idf", tmp / "辦公室 舊版.idf")
    code, log = run(WS / "db_import.py", tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    check(code == 0, f"db_import 失敗:\n{log[-1500:]}")
    check("升級 25.1 -> 25.2" in log and "升級 26.1 -> 26.2" in log, "沒有看到完整升級鏈")
    rs = rows(OUT / "results.csv")
    for model in ("old_v25_1", "辦公室 舊版"):
        base = all_cases_ok(rs, model)["baseline"]
        check((base["elec_kwh"], base["gas_kwh"]) == ("43510.1", "19018.4"),
              f"{model}: 應為 43510.1 / 19018.4, 實際 {base['elec_kwh']} / {base['gas_kwh']}")


def test_db_designbuilder_export():
    """真正從 DesignBuilder 2025.1 匯出的 IDF(23.1 版、評估版壓縮格式、理想負載空調、英制報表)。"""
    tmp = WS / "db_models" / "test_smoke"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    shutil.copyfile(FIX / "designbuilder_v23_1.idf", tmp / "designbuilder_v23_1.idf")
    code, log = run(WS / "db_import.py", tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    check(code == 0, f"db_import 失敗:\n{log[-1500:]}")
    check("升級 23.1 -> 23.2" in log and "升級 26.1 -> 26.2" in log, "沒有看到 23.1 -> 26.2 的升級鏈")
    cases = all_cases_ok(rows(OUT / "results.csv"), "designbuilder_v23_1")
    base = cases["baseline"]
    check(float(base["gas_kwh"]) > 0 and "區域能源" in base["error"],
          f"理想負載的暖氣沒有換算成天然氣: gas={base['gas_kwh']} note={base['error']}")
    check("U 值維持 1.96" in cases["film_shgc0.30"]["error"], "沒有從 .eio 讀到窗戶性質")
    check(float(cases["infil_x0.5"]["saving_pct"]) > 0, "只有暖氣的模型, 滲風減半應該要省")
    check("外牆 U" in cases["insulation"]["error"] and float(cases["insulation"]["saving_pct"]) > 0,
          f"DB 模型應能用 MCP 工具加強隔熱: {cases['insulation']}")
    check("理想負載" in cases["hvac_eff"]["error"] and float(cases["hvac_eff"]["saving_pct"]) > 0,
          f"理想負載模型的高效率空調應改用換算效率: {cases['hvac_eff']}")


def test_scan2_samples():
    """5 個範例模型 × 9 情境平行跑完, 並重產生敏感度網頁。"""
    code, log = run(WS / "scan2" / "scan2.py")
    check(code == 0, f"scan2 失敗:\n{log[-1500:]}")
    rs = rows(WS / "scan2" / "results.csv")
    check(len(rs) == 45 and all(r["success"] == "True" for r in rs),
          f"45 個情境應全部成功: {[(r['model'], r['case'], r['error']) for r in rs if r['success'] != 'True']}")
    base = by_case(rs, "5ZoneAirCooled")["baseline"]
    check((base["elec_kwh"], base["gas_kwh"], base["floor_m2"]) == ("43510.1", "19018.4", "463.6"),
          f"5ZoneAirCooled 基準改變了: {base['elec_kwh']} / {base['gas_kwh']} / {base['floor_m2']}")
    code, log = run(WS / "sensitivity.py", timeout=120)
    check(code == 0 and "個措施組合" in log, f"sensitivity 失敗:\n{log[-800:]}")


def test_batch():
    """11 個範例模型批量模擬, 每個都要讀得到用電。"""
    code, log = run(WS / "batch_driver.py")
    check(code == 0, f"batch_driver 失敗:\n{log[-1500:]}")
    rs = rows(WS / "batch" / "results.csv")
    bad = [r["model"] for r in rs if r["success"] != "True" or not r["electricity_kwh"]]
    check(len(rs) == 11 and not bad, f"這些模型失敗或沒有用電: {bad}")


def test_eppy_patch():
    """eppy 的 stdin 修正還在(uv sync 會蓋掉)。"""
    srv = WS / "vendor/EnergyPlus-MCP/energyplus-mcp-server"
    r = subprocess.run([str(srv / ".venv/Scripts/python.exe"), "apply_windows_patches.py"], cwd=srv,
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    check(r.returncode == 0 and "已套用(4 處)" in r.stdout, f"eppy patch 狀態不對: {r.stdout} {r.stderr}")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and only in n]
    failed = 0
    for name, fn in tests:
        started = time.time()
        try:
            fn()
            print(f"PASS {name}  ({time.time() - started:.0f}s)", flush=True)
        except Exception as e:
            failed += 1
            print(f"FAIL {name}  ({time.time() - started:.0f}s)\n     {e}", flush=True)
    print(f"\n{len(tests) - failed} / {len(tests)} 通過")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
