"""一個指令: 參數 JSON -> 建模 -> 模擬基準與全部措施 -> db_models/out/report.html。

用法:
    python run_building.py tools/modeler/example.json
    python run_building.py 方案A.json 方案B.json 方案C.json   # 多個方案, 報告最前面會並排比較

建模用 tools/modeler 的 venv(OpenStudio + Ladybug Tools)跑 build_idf.py, 模擬用 db_import.py(多個方案平行跑)。
順序不能反過來: 由那個 venv 的 Python 帶起的 E+ Transition 程式會當掉, 所以由這支(主 Python)當父程序。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELER_PY = HERE / "tools/modeler/.venv/Scripts/python.exe"
sys.path.insert(0, str(HERE))
import db_import  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def build(params: Path):
    r = subprocess.run([str(MODELER_PY), str(HERE / "tools/modeler/build_idf.py"), str(params)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout, end="")
    if r.returncode:
        # build_idf 的參數錯誤是一行說明, 其他錯誤才需要看完整訊息
        raise SystemExit(f"{params.name}: " + (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "建模失敗"))
    idf = re.search(r"已產生 (.+?\.idf)", r.stdout)
    if not idf:
        raise SystemExit(f"{params.name}: 建模沒有產生 IDF")
    return idf.group(1)


def main():
    files = [Path(a) for a in sys.argv[1:]]
    if not files:
        raise SystemExit(__doc__)
    names = [json.loads(f.read_text(encoding="utf-8")).get("name") for f in files]
    dup = {n for n in names if names.count(n) > 1}
    if dup:  # 同名會互相覆蓋 IDF 與結果
        raise SystemExit(f"每個方案的 name 要不同,重複的有: {', '.join(map(str, dup))}")
    idfs = [build(f) for f in files]
    sys.argv = [sys.argv[0], *idfs]
    db_import.main()


if __name__ == "__main__":
    main()
