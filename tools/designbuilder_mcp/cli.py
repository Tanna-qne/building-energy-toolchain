"""不經 AI、直接用命令列跑 DesignBuilder 模型: DB 年度模擬 → 摘要 → 節能措施報告。給「建築能耗.bat」用。

    <基底 Python 3.12> tools/designbuilder_mcp/cli.py <模型.dsb>

和 MCP 工具(db_simulate → db_measures_report → db_report_status)是同一套程式。
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print("啟動 DesignBuilder 跑年度模擬(會跳 Windows 的確認視窗,請按「是」)…", flush=True)
    text = server.db_simulate(sys.argv[1])
    print(text, flush=True)
    m = re.search(r"輸出資料夾:`(.+?)`", text)
    out = Path(m.group(1)) if m else None
    if not out or not (out / "in.idf").exists():
        raise SystemExit(1)
    print("\n" + server.db_measures_report(str(out)), flush=True)
    while True:
        time.sleep(15)
        status = server.db_report_status(str(out))
        if not status.startswith("## 還在算"):
            break
        print("  …還在算", flush=True)
    print("\n" + status, flush=True)
    print(f"\n報告:{out / 'report.html'}", flush=True)
    if "失敗" in status.splitlines()[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
