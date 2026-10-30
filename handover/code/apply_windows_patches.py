"""重新套用 eppy 的 Windows 修正(stdin 卡死)。

`uv sync` 會重建 .venv,把 site-packages 裡的 patch 蓋掉,跑一次這個就好:
    .venv/Scripts/python.exe apply_windows_patches.py
"""
import re
import subprocess
import sys
from pathlib import Path

MARK = "stdin=subprocess.DEVNULL"


def eppy_runner_path() -> Path:
    out = subprocess.run([sys.executable, "-c", "import eppy, os; print(os.path.dirname(eppy.__file__))"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip()) / "runner" / "run_functions.py"


def calls(src):
    """每個 check_call(...) 的 (開括號後, 對應的閉括號) 位置; 用括號配對, 引數裡有 open(...) 也不會切錯。"""
    for m in re.finditer(r"\bcheck_call\(", src):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        yield m.end(), i - 1


def main() -> int:
    path = eppy_runner_path()
    src = path.read_text(encoding="utf-8")
    spans = list(calls(src))
    if not spans:
        print(f"找不到 check_call, 請人工檢查: {path}")
        return 1
    todo = [(a, b) for a, b in spans if MARK not in src[a:b]]
    if not todo:
        print(f"已套用({len(spans)} 處): {path}")
        return 0
    for a, b in reversed(todo):
        src = src[:b] + ", " + MARK + src[b:]
    if not re.search(r"^import subprocess\b", src, re.MULTILINE):
        # 放在第一個一般 import 前; 放檔頭會排到 from __future__ 前面而語法錯誤
        m = re.search(r"^(?:import |from (?!__future__))", src, re.MULTILINE)
        at = m.start() if m else 0
        src = src[:at] + "import subprocess\n" + src[at:]
    path.write_text(src, encoding="utf-8")
    print(f"已修正 {len(todo)} / {len(spans)} 處: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
