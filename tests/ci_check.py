"""CI 重現性檢查: 不依賴 MCP server、DesignBuilder 或未公開資料, Linux 與 Windows 都能跑。

    uv run --with eppy python tests/ci_check.py

1. 所有 Python 檔可以編譯(語法檢查);
2. 長興定版模型重跑全年, 逐時用電與倉庫裡公開的 hourly_2025.csv 一致;
3. 長興 9 種節能措施重跑, 節電量與倉庫裡的 ecm_results.csv 一致;
4. tools/eplus_summary.py 讀 EnergyPlus 範例 5ZoneAirCooled 的報表, 電與天然氣和 scan2/results.csv 的基準一致。
跨平台浮點有微小差異, 所以用容許誤差比較, 不要求逐位元相同。
"""
import csv
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WS = Path(__file__).resolve().parents[1]
CX = WS / "projects" / "changxing"
sys.path.insert(0, str(CX / "sensitivity"))
HOURLY_TOL_KWH = 0.5   # 逐時最大差
SAVING_TOL = 0.01      # 節電量相對差 1%(另有 50 kWh 的絕對容許, 給省很少的措施)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check_compile():
    files = [WS / f for f in subprocess.run(["git", "ls-files", "*.py"], cwd=WS, capture_output=True,
                                            text=True, check=True).stdout.split()]
    for f in files:
        py_compile.compile(str(f), doraise=True)
    print(f"[ok] {len(files)} 個 Python 檔可編譯")


def check_baseline():
    from common import BASE_IDF, EP, simulate
    print(f"EnergyPlus: {EP}")
    with open(CX / "hourly_2025.csv", encoding="utf-8-sig") as f:
        ref = [float(r["station_total_kWh"]) for r in csv.DictReader(f)]
    with tempfile.TemporaryDirectory() as tmp:
        sim = simulate(BASE_IDF, Path(tmp) / "baseline")["Electricity:Facility"]
    assert len(sim) == len(ref) == 8760, f"小時數不對: 模擬 {len(sim)}, 參考 {len(ref)}"
    diff = max(abs(a - b) for a, b in zip(sim, ref))
    print(f"全年 {sum(sim):,.1f} kWh(參考 {sum(ref):,.1f}), 逐時最大差 {diff:.4f} kWh")
    assert diff <= HOURLY_TOL_KWH, f"逐時最大差 {diff:.3f} kWh 超過 {HOURLY_TOL_KWH}"
    print("[ok] 定版模型重現 hourly_2025.csv")


def check_ecm():
    path = CX / "ecm" / "ecm_results.csv"
    with open(path, encoding="utf-8") as f:
        ref = {r["key"]: float(r["saving_kWh"]) for r in csv.DictReader(f)}
    keep = [path, CX / "ecm" / "ecm_summary.json"]
    backup = [p.read_bytes() for p in keep]
    try:
        subprocess.run([sys.executable, str(CX / "ecm" / "ecm.py")], cwd=WS, check=True)
        with open(path, encoding="utf-8") as f:
            new = {r["key"]: float(r["saving_kWh"]) for r in csv.DictReader(f)}
    finally:
        for p, b in zip(keep, backup):  # 本機跑也不改動倉庫裡的結果檔
            p.write_bytes(b)
        shutil.rmtree(CX / "ecm" / "_runs", ignore_errors=True)
    bad = []
    for k, v in ref.items():
        tol = max(SAVING_TOL * abs(v), 50)
        print(f"  {k:12s} 倉庫 {v:10,.1f}  重跑 {new[k]:10,.1f} kWh")
        if abs(new[k] - v) > tol:
            bad.append(k)
    assert not bad, f"節電量和倉庫結果不一致: {bad}"
    print("[ok] 節能措施重現 ecm_results.csv")


def check_summary():
    from common import EP, EP_EXE
    sys.path.insert(0, str(WS / "tools"))
    from eplus_summary import summarize, to_markdown
    with open(WS / "scan2" / "results.csv", encoding="utf-8-sig") as f:
        ref = next(r for r in csv.DictReader(f) if r["model"] == "5ZoneAirCooled" and r["case"] == "baseline")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([str(EP_EXE), "-w", str(EP / "WeatherData" / "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"),
                        "-d", tmp, "-r", str(EP / "ExampleFiles" / "5ZoneAirCooled.idf")],
                       capture_output=True, check=True, stdin=subprocess.DEVNULL)
        s = summarize(Path(tmp))
        to_markdown(s)
    fuels = s["by_fuel_kWh"]
    for key, fuel in (("elec_kwh", "Electricity"), ("gas_kwh", "Natural Gas")):
        want, got = float(ref[key]), fuels[fuel]
        print(f"  {fuel:12s} scan2 {want:10,.1f}  摘要 {got:10,.1f} kWh")
        assert abs(got - want) <= 0.01 * want, f"{fuel} 摘要與 scan2 結果差超過 1%"
    assert abs(sum(u["kWh"] for u in s["end_uses"]) - s["site_kWh"]) <= 0.01 * s["site_kWh"], "分項加總與總用能不符"
    print("[ok] eplus_summary 讀報表正確")


if __name__ == "__main__":
    check_compile()
    check_baseline()
    check_ecm()
    check_summary()
