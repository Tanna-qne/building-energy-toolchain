"""一個指令: 參數 JSON -> 建模 -> 模擬基準與全部措施 -> db_models/out/report.html。

用法:
    python run_building.py tools/modeler/example.json

建模用 tools/modeler 的 venv(OpenStudio + Ladybug Tools)跑 build_idf.py, 模擬用 db_import.py。
順序不能反過來: 由那個 venv 的 Python 帶起的 E+ Transition 程式會當掉, 所以由這支(主 Python)當父程序。
"""
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


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    r = subprocess.run([str(MODELER_PY), str(HERE / "tools/modeler/build_idf.py"), sys.argv[1]],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout, end="")
    if r.returncode:
        # build_idf 的參數錯誤是一行說明, 其他錯誤才需要看完整訊息
        raise SystemExit(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "建模失敗")
    idf = re.search(r"已產生 (.+?\.idf)", r.stdout)
    if not idf:
        raise SystemExit("建模沒有產生 IDF")
    sys.argv = [sys.argv[0], idf.group(1)]
    db_import.main()


if __name__ == "__main__":
    main()
