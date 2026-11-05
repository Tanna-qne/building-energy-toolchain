"""DB 的 AfterSimReport script 呼叫這支: 對 DB 剛模擬完的 IDF 跑全部節能措施, 算完用瀏覽器打開報告。

    python tools/designbuilder/from_db.py <in.idf 的副本> [氣象檔]

輸出在 IDF 旁邊: report.html、results.csv、run.log(出錯時看這個)。
"""
import os
import shutil
import sys
import traceback
import webbrowser
from pathlib import Path

WS = Path(__file__).resolve().parents[2]


def main():
    idf = Path(sys.argv[1]).resolve()  # MCP server 的 cwd 不同, 一律用絕對路徑
    epw = str(Path(sys.argv[2]).resolve()) if len(sys.argv) > 2 and sys.argv[2] else ""
    log = open(idf.parent / "run.log", "w", encoding="utf-8")
    sys.stdout = sys.stderr = log  # DB 帶起來時沒有主控台
    if epw and Path(epw).exists():
        os.environ["EPW"] = epw  # batch_driver 在 import 時讀, 要先設
    sys.path.insert(0, str(WS))
    try:
        import db_import
        sys.argv = [sys.argv[0], str(idf)]
        db_import.main()
        for f in ("report.html", "report.md", "results.csv"):
            shutil.copyfile(db_import.OUT / f, idf.parent / f)
        print("完成", flush=True)
        webbrowser.open((idf.parent / "report.html").as_uri())
    except BaseException:  # SystemExit 也要記下來
        traceback.print_exc()
        raise
    finally:
        log.flush()


if __name__ == "__main__":
    main()
