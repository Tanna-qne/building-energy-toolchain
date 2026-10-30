"""DesignBuilder 匯出的 IDF -> 升級到 E+ 26.2 -> 補電表 -> 跑基準與措施(重用 scan2)。

用法:
    python db_import.py                 # 處理 db_models/inbox/ 裡所有 .idf
    python db_import.py a.idf b.idf     # 指定檔案或資料夾
輸出: db_models/out/results.csv、report.md; 中間檔在 db_models/work/。

版本升級用 E+ 自帶的 Transition 程式(MCP 沒有這個工具), 其餘修改與模擬都走 MCP。
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from batch_driver import Mcp, EP  # noqa: E402
from scan2.scan2 import apply, scan_parallel, write_outputs  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent / "db_models"
INBOX = ROOT / "inbox"
WORK = ROOT / "work"
OUT = ROOT / "out"
UPDATER = EP / "PreProcess" / "IDFVersionUpdater"
TARGET = (26, 2)


def idf_version(path: Path):
    text = re.sub(r"!.*", "", path.read_text(encoding="latin-1"))
    m = re.search(r"(?:^|;)\s*Version\s*,\s*(\d+)\.(\d+)", text, re.IGNORECASE)
    if not m:
        raise ValueError(f"{path.name}: 找不到 Version 物件")
    return int(m.group(1)), int(m.group(2))


def transitions():
    """{(舊版): ((新版), exe)}, 從 IDFVersionUpdater 目錄裡的檔名解析。"""
    steps = {}
    for exe in UPDATER.glob("Transition-V*-to-V*.exe"):
        m = re.match(r"Transition-V(\d+)-(\d+)-\d+-to-V(\d+)-(\d+)-\d+\.exe", exe.name)
        if m:
            a, b, c, d = map(int, m.groups())
            steps[(a, b)] = ((c, d), exe)
    return steps


def upgrade(path: Path):
    """原地把 IDF 一路升到 26.2。Transition 要在自己的目錄執行才找得到各版 IDD。"""
    steps = transitions()
    ver = idf_version(path)
    while ver < TARGET:
        if ver not in steps:
            raise RuntimeError(f"{path.name}: 沒有 {ver[0]}.{ver[1]} 的升級程式")
        nxt, exe = steps[ver]
        r = subprocess.run([str(exe), str(path.resolve())], cwd=UPDATER, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0 or idf_version(path) != nxt:
            raise RuntimeError(f"{path.name}: {ver[0]}.{ver[1]} -> {nxt[0]}.{nxt[1]} 失敗\n{r.stdout[-500:]}")
        print(f"  升級 {ver[0]}.{ver[1]} -> {nxt[0]}.{nxt[1]}", flush=True)
        ver = nxt
    for suffix in (".idfold", ".idfnew"):
        path.with_suffix(suffix).unlink(missing_ok=True)
    if ver > TARGET:
        raise RuntimeError(f"{path.name}: 版本 {ver[0]}.{ver[1]} 比本機 E+ 26.2 新")


def safe_name(stem: str) -> str:
    """EnergyPlus 讀不到非 ASCII 路徑(中文檔名會 File not found, MCP 還會卡到逾時), 工作檔一律用 ASCII 名。"""
    if stem.isascii() and re.fullmatch(r"[\w.-]+", stem):
        return stem
    ascii_part = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return f"{ascii_part or 'model'}_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:6]}"


def prepare(client, src: Path, name: str):
    """複製到 work/ -> 升級 -> 關掉 sizing period 模擬 -> 補電表。回傳可以直接跑的 IDF。"""
    WORK.mkdir(parents=True, exist_ok=True)
    work = WORK / f"{name}.idf"
    shutil.copyfile(src, work)
    upgrade(work)

    # 設計日若也跑模擬, 電表 CSV 會多出設計日的列, 加總會灌水
    step = apply(client, "modify_simulation_control",
                 {"idf_path": str(work.resolve()),
                  "field_updates": {"Run_Simulation_for_Sizing_Periods": "No",
                                    "Run_Simulation_for_Weather_File_Run_Periods": "Yes"}},
                 WORK / f"{name}__simctl.idf")
    work = step or work

    step = apply(client, "add_output_meters",
                 {"idf_path": str(work.resolve()), "validation_level": "lenient",
                  # District 兩項給理想負載的模型用(DesignBuilder 預設的簡化空調), scan2.run() 會換算成電與氣
                  "meters": [["Electricity:Facility", "RunPeriod"], ["NaturalGas:Facility", "RunPeriod"],
                             ["DistrictCooling:Facility", "RunPeriod"], ["DistrictHeatingWater:Facility", "RunPeriod"]]},
                 WORK / f"{name}__ready.idf")
    return step or work


def main():
    args = [Path(a) for a in sys.argv[1:]] or [INBOX]
    srcs = []
    for a in args:
        srcs += sorted(a.glob("*.idf")) if a.is_dir() else [a]
    if not srcs:
        raise SystemExit(f"沒有 IDF: 把 DesignBuilder 匯出的檔案放到 {INBOX}/ 或在命令列指定")

    OUT.mkdir(parents=True, exist_ok=True)
    client = Mcp()
    try:
        models, display, meta = [], {}, {}
        for src in srcs:
            name = safe_name(src.stem)
            display[name] = src.stem
            params = src.with_suffix(".json")  # build_idf.py 會把建模參數放在 IDF 旁邊, 報告用來描述建築
            if params.exists():
                meta[src.stem] = json.loads(params.read_text(encoding="utf-8"))
            print(f"準備 {src.name}" + (f" (工作檔名 {name})" if name != src.stem else ""), flush=True)
            try:
                models.append((name, prepare(client, src, name)))
            except (RuntimeError, ValueError, OSError) as e:
                print(f"  跳過: {e}", flush=True)
    finally:
        client.close()
    rows = scan_parallel(models, WORK)
    for r in rows:
        r["model"] = display.get(r["model"], r["model"])
    write_outputs(rows, OUT, meta)


if __name__ == "__main__":
    main()
