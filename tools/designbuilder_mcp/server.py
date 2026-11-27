"""DesignBuilder MCP server: 讓 agent 透過 DB 官方的命令列(/process=)操作 DesignBuilder。

只是把現成專案接起來, 不自己寫模擬或建模邏輯:
- db-process(DesignBuilder 官方): /process= 指令鏈與找 DesignBuilder.exe
- 匯出的 dsbXML 用標準 XML 讀(格式見 DesignBuilder 官方 db-dsbxml-schema)
- honeybee-designbuilder(Ladybug Tools): 參數 -> DB 模型(params_to_dsbxml.py)
- 節能措施報告沿用工作區的 tools/designbuilder/from_db.py

DB 一次只能開一個; 工具在 DB 開著時會拒絕執行, 不會關掉使用者的視窗。
操作一律用模型的副本(db_models/dbmcp/<模型名>/), 不改使用者原本的 .dsb。

    <Python 3.12 安裝目錄>/python.exe tools/designbuilder_mcp/server.py

用 venv 的「基底」Python 執行, 不要用 .venv/Scripts/python.exe: 在 venv 啟動器底下開的子程序裡,
E+ 的 Transition(版本升級)會當掉(HANDOVER 坑 18), 節能報告就跑不出來。套件從 .venv 載入。
"""
import csv
import json
import os
import queue
import re
import secrets
import shutil
import site
import subprocess
import threading
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
site.addsitedir(str(HERE / ".venv" / "Lib" / "site-packages"))  # addsitedir 會處理 .pth(pywin32 需要)
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1])]
from building_report import MEASURES, num, verdict, yrs  # noqa: E402  白話措施名稱與「值不值得做」的判讀
from eplus_summary import summarize, to_markdown  # noqa: E402
sys.path.insert(0, str(HERE))
import dblibrary  # noqa: E402  DB 本機範本庫(活動範本、作息、構造)
import results  # noqa: E402  讀 eplusout.eso / .mtr 的逐時、逐熱區結果

from db_process import ProcessChain, Screen, find_designbuilder, is_running, kill_process  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

WS = HERE.parents[1]
WORK = WS / "db_models" / "dbmcp"
DB_EP_DIR = Path(os.environ["LOCALAPPDATA"]) / "DesignBuilder" / "EnergyPlus"
# 跑節能報告(from_db.py)的 Python: 預設是 DSH 內建 runtime, 可用環境變數 REPORT_PYTHON 指定
MAIN_PY = Path(os.environ.get("REPORT_PYTHON") or
               Path.home() / ".dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe")
# 參數 → dsbXML 用建模環境: 官方 26.2 配的 .venv, 或只用 DesignBuilder 引擎時的 .venv231(安裝.bat 建的)
MODELER_PY = next((p for p in (WS / "tools" / "modeler" / v / "Scripts" / "python.exe" for v in (".venv", ".venv231"))
                   if p.exists()), WS / "tools" / "modeler" / ".venv" / "Scripts" / "python.exe")
EP_OUTPUTS = ("in.idf", "eplusout.err", "eplustbl.htm", "eplusout.eso", "eplusout.mtr")

# 常看的建築層屬性(ChangeAttributeValue 用同一組名稱)
KEY_ATTRIBUTES = ("Title", "ConstructionTemplateId", "GlazingTemplateId", "ActivityTemplateId", "HVACTemplateId",
                  "LightingTemplateId", "InfiltrationValue", "OccupancyValue", "OccupancySchedule",
                  "HeatingSetpointTemperature", "CoolingSetpointTemperature")

mcp = FastMCP("designbuilder")


def _launch(args: str):
    """啟動 DesignBuilder, 不等。這台電腦的 DB 設成「以系統管理員身分執行」, CreateProcess(subprocess /
    db_process.run)會 WinError 740; PowerShell Start-Process 走 Windows 正常的提權流程(實測可行)。"""
    exe = str(find_designbuilder()).replace("'", "''")
    script = f"Start-Process -FilePath '{exe}' -ArgumentList '{args.replace(chr(39), chr(39) * 2)}'"
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600,  # 含使用者按 Windows 提權確認的時間
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise RuntimeError(f"無法啟動 DesignBuilder: {r.stderr.strip()[-300:]}")


def _close_db():
    """DB 做完 /process= 指令後常常不會自己關(db-process 文件也提到), 由我們關掉。
    DB 以系統管理員身分執行時, 一般權限關不掉, 只能請使用者關。"""
    for _ in range(3):
        if not is_running():
            return True
        try:
            kill_process()
        except Exception:  # noqa: BLE001  以系統管理員身分執行的 DB: psutil.AccessDenied, 關不掉就請使用者關
            return False
        time.sleep(3)
    return not is_running()


def _check_idle():
    if is_running():
        raise RuntimeError("DesignBuilder 正在執行。DB 一次只能開一個: 請使用者先存檔並關掉 DB, 再呼叫這個工具。")


# HTTP 模式(http_server.py, 給 Dify 用)時, 檔案路徑只能在這些資料夾底下: token 外流也讀不到其他檔案。
# DBMCP_ALLOWED_DIRS 用分號分隔, 例如放使用者模型的資料夾
HTTP_MODE = os.environ.get("DBMCP_HTTP") == "1"
ALLOWED_DIRS = [WORK] + [Path(p) for p in os.environ.get("DBMCP_ALLOWED_DIRS", "").split(";") if p.strip()]


def _guard(path) -> Path:
    p = Path(path)
    if HTTP_MODE:
        r = p.resolve()
        if not any(r == d.resolve() or d.resolve() in r.parents for d in ALLOWED_DIRS):
            raise ValueError(f"HTTP 模式只能使用這些資料夾裡的檔案:{'; '.join(str(d) for d in ALLOWED_DIRS)}"
                             "(要加資料夾,啟動時設定 DBMCP_ALLOWED_DIRS)")
    return p


def _working_copy(dsb_path: str) -> Path:
    src = _guard(dsb_path)
    if src.suffix.lower() != ".dsb" or not src.exists():
        raise ValueError(f"找不到 .dsb 檔: {dsb_path}")
    # E+ 與 DB 對非 ASCII 路徑不穩, 工作副本用 ASCII 名稱
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem).strip("_") or "model"
    dst = WORK / name / f"{name}.dsb"
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 原檔比副本新才複製; copy2 保留時間, db_inspect 的 XML 快取才判斷得出來
    if src.resolve() != dst.resolve() and (not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime):
        shutil.copy2(src, dst)
    return dst


DIAG_DIR = Path(os.environ["LOCALAPPDATA"]) / "DesignBuilder" / "Diagnostics"
STALL_S = 300  # DB 的診斷紀錄這麼久沒有新內容、又還沒做完, 視為停在對話框


def _diag_log(dsb: Path, started: float):
    """這次啟動的 DB 寫的診斷紀錄(DB 每次啟動開一個新檔, 開頭記下命令列)。"""
    for f in sorted(DIAG_DIR.glob("Diagnostics_*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.stat().st_mtime < started - 5:
            break
        head = f.read_text(encoding="latin-1", errors="replace")[:20000]
        if str(dsb).lower() in head.lower():
            return f
    return None


def _diag_state(log: Path, last_cmd: str):
    """(做完了沒, 紀錄最後幾行)。做完 = 處理過最後一個指令之後, DB 放開了模型檔("Removing 'In Use' file")。
    匯出 XML、模擬、設計負載實測都是這個順序(2026-11-24)。"""
    text = log.read_text(encoding="latin-1", errors="replace")
    i = text.rfind(f"Processing batch command: {last_cmd}")
    done = i >= 0 and "Removing 'In Use' file" in text[i:]
    tail = [ln.strip() for ln in text.strip().splitlines()[-8:] if ln.strip()]
    return done, tail


def _run_chain(dsb: Path, chain: ProcessChain, timeout_s: int, finished=None):
    """跑一串 /process= 指令, 用 DB 自己的診斷紀錄判斷做完沒有; 做完就關掉 DB。
    finished(): 另外的完成條件(例如結果檔出現), 任一成立就算完成。回傳 (秒數, DB 有沒有關掉)。
    紀錄太久沒動時拋錯, 附上紀錄最後幾行, 通常是 DB 停在某個對話框。"""
    _check_idle()
    start = time.time()
    _launch(f'"{dsb}" {chain.to_string()}')
    last_cmd = chain.to_string().split("=", 1)[-1].split(",")[-1].strip()
    log, last_size, last_change = None, -1, time.time()
    seen = False
    while time.time() - start < timeout_s:
        time.sleep(5)
        log = log or _diag_log(dsb, start)
        if log:
            size = log.stat().st_size
            if size != last_size:
                last_size, last_change = size, time.time()
            done, tail = _diag_state(log, last_cmd)
            if done:
                break
            if time.time() - last_change > STALL_S and is_running():
                _close_db()
                raise RuntimeError("DesignBuilder 超過 {} 秒沒有進展,可能停在對話框。DB 紀錄最後幾行:\n    {}".format(
                    STALL_S, "\n    ".join(tail)))
        if finished and finished():
            time.sleep(5)  # 讓 DB 把檔案寫完
            break
        running = is_running()
        seen = seen or running
        if seen and not running:
            break
    else:
        _close_db()
        raise RuntimeError(f"DesignBuilder 超過 {timeout_s} 秒還沒做完")
    time.sleep(3)
    closed = _close_db()
    return round(time.time() - start), closed


CLOSE_NOTE = ("DesignBuilder 做完後沒有關掉, 這邊也關不掉(通常是 DB 被設成「以系統管理員身分執行」): "
              "請使用者在工作管理員結束 DesignBuilder.exe, 下一個 DB 工具才能執行。")


def _newer_than(path: Path, t: float):
    return lambda: path.exists() and path.stat().st_mtime > t


def _export_xml(dsb: Path, timeout_s=600):
    """回傳 (XML 路徑, DB 有沒有關掉)。"""
    xml = dsb.with_suffix(".xml")
    closed = True
    if not xml.exists() or xml.stat().st_mtime < dsb.stat().st_mtime:
        _, closed = _run_chain(dsb, ProcessChain().export_as_xml(), timeout_s, _newer_than(xml, time.time()))
    if not xml.exists():
        raise RuntimeError("DesignBuilder 沒有產生 XML")
    return xml, closed


def _attrs(el) -> dict:
    a = el.find("Attributes")
    return {x.get("key"): (x.text or "") for x in a} if a is not None else {}


LICENCE_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "DesignBuilder" / "Licences"
MODULES = {"EP": "EnergyPlus 模擬", "HV": "HVAC 詳細系統", "DY": "採光", "OP": "最佳化"}  # 其他代碼含義未確認, 原樣顯示


def _licences():
    """[(模組代碼, 到期日)]; 讀不到回傳空清單。"""
    out = []
    for f in sorted(LICENCE_DIR.glob("*.lic")) if LICENCE_DIR.exists() else []:
        m = re.search(r"(\d{1,2})-([a-z]{3})-(\d{4})", f.read_text(encoding="latin-1", errors="replace"))
        if m:
            d = datetime.strptime(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "%d-%b-%Y").date()
            out.append((f.stem.split("-")[-1], d))
    return out


def _err_lines(err: str, n=5):
    """eplusout.err 裡最值得看的幾行: Severe / Fatal 優先。"""
    bad = [ln.strip() for ln in err.splitlines() if "** Severe" in ln or "Fatal" in ln]
    return bad[:n] or [ln.strip() for ln in err.strip().splitlines()[-n:]]


@mcp.tool()
def db_status() -> str:
    """DesignBuilder 有沒有在執行、執行檔在哪裡、授權哪天到期。其他工具都需要 DB 沒開著。"""
    lines = ["## DesignBuilder 狀態", ""]
    running = is_running()
    lines.append("- **正在執行**:其他 DB 工具要等使用者存檔關掉 DB 才能用。" if running else "- 沒有在執行,可以使用其他工具。")
    try:
        lines.append(f"- 執行檔:`{find_designbuilder()}`")
    except Exception as e:  # noqa: BLE001
        lines.append(f"- **找不到 DesignBuilder 執行檔**:{e}")
    lic = _licences()
    if lic:
        # 同一模組可能有新舊幾份授權檔(續約後舊的還在), 每個模組取最晚的到期日
        latest = {}
        for code, d in lic:
            latest[code] = max(d, latest.get(code, d))
        today = date.today()
        valid = {c: d for c, d in latest.items() if d >= today}
        if valid:
            soonest = min(valid.values())
            left = (soonest - today).days
            mods = "、".join(sorted(MODULES.get(c, c) for c in valid))
            when = "**今天到期**" if left == 0 else f"還有 {left} 天"
            lines.append(f"- 授權到 {soonest:%Y-%m-%d}({when});有效模組:{mods}。")
            if left <= 7:
                lines.append("  授權到期後 DB 的模擬與計算都不能用,需要續約。")
        else:
            lines.append(f"- 授權**已過期**(最晚到 {max(latest.values()):%Y-%m-%d}),DB 的模擬與計算都不能用。")
        expired = sorted(MODULES.get(c, c) for c in latest if c not in valid)
        if valid and expired:
            lines.append(f"- 已過期的模組:{'、'.join(expired)}。")
    else:
        lines.append(f"- 讀不到授權檔({LICENCE_DIR})。")
    return "\n".join(lines)


@mcp.tool()
def db_inspect(dsb_path: str, attribute_filter: str = "") -> str:
    """讀 DesignBuilder 模型(.dsb)的內容: 建築、區塊、zone, 以及建築層的屬性。
    第一次會在背景開 DB 匯出 XML(約 1–3 分鐘), 之後用快取。

    Args:
        dsb_path: .dsb 的完整路徑
        attribute_filter: 只列出名稱含這段文字(不分大小寫)的屬性, 例如 "Infiltration"、"Occupancy";
            留空只列常用屬性。屬性名稱可直接給 db_simulate 的 attributes 用。
    """
    xml, closed = _export_xml(_working_copy(dsb_path))
    root = ET.parse(xml).getroot()
    buildings = []
    for b in root.iter("Building"):
        a = _attrs(b)
        if attribute_filter:
            shown = {k: v for k, v in a.items() if k and attribute_filter.lower() in k.lower()}
            shown = dict(list(shown.items())[:80])
        else:
            shown = {k: a[k] for k in KEY_ATTRIBUTES if k in a}
        # zone 的名稱在它的 <Body> 底下
        blocks = [{"block": _attrs(bb).get("Title", ""),
                   "zones": [_attrs(z.find("Body") if z.find("Body") is not None else z).get("Title", "")
                             for z in bb.iter("Zone")]}
                  for bb in b.iter("BuildingBlock")]
        buildings.append({"title": a.get("Title", ""), "blocks": blocks, "attributes": shown,
                          "attribute_count": len(a)})
    return json.dumps({"xml": str(xml), "buildings": buildings, **({} if closed else {"note": CLOSE_NOTE})},
                      ensure_ascii=False)


@mcp.tool()
def db_simulate(dsb_path: str, attributes: Optional[dict] = None, start_date: str = "", end_date: str = "",
                hourly: bool = False, timeout_s: int = 1800) -> str:
    """用 DesignBuilder 本身跑 EnergyPlus 年度模擬(在模型副本上, 不改原檔), 收集 in.idf / eplustbl.htm 等結果。

    Args:
        dsb_path: .dsb 的完整路徑
        attributes: 模擬前要改的建築層屬性, 例如 {"InfiltrationValue": 0.35}; 名稱用 db_inspect 查。
            數值用 SI 單位; 布林用 1/0; 資料表選項用數字 Id。
        start_date / end_date: "日/月", 例如 "1/1"、"31/12"; 留空用模型原本的設定
        hourly: True 時開啟 DB 的逐時輸出(要逐時結果給 db_results 用時才開; 輸出檔會大很多)。需要模型裡有 DbMcpBridge
        timeout_s: 最多等幾秒
    Returns: 輸出資料夾、EnergyPlus 是否成功、總能耗與 EUI。要算節能措施與回本, 接著呼叫 db_measures_report;
        要逐時、逐熱區結果, 呼叫 db_results。
    """
    dsb = _working_copy(dsb_path)
    # 有 DbMcpBridge 的模型: 在 DB 產生 IDF 前記下熱區代號與名稱的對照(評估版授權的 IDF 用數字代號當熱區名稱),
    # 需要時也開啟逐時輸出。這個設定是 Site 屬性, DB 跑完存檔後會留在模型副本裡
    job = None
    bridge = _has_bridge(dsb)
    if hourly and bridge:
        # DB 在 BeforeEnergyIdfGeneration 之前就決定了輸出變數, 在模擬裡才設定要到下一次才生效(實測);
        # 所以先用一次短作業開啟並存檔(約 1 分鐘)
        _bridge(dsb, [("sset", "HourlyOutput", "1"), ("save",)])
    if bridge:
        job = _write_job(dsb, [("sdump", OUTPUT_KEYS), ("handles",)])
        os.environ["DBMCP_JOB"] = str(job)
    try:
        report = _simulate(dsb_path, dsb, attributes, start_date, end_date, timeout_s)
    finally:
        os.environ.pop("DBMCP_JOB", None)
    out = max(dsb.parent.glob("sim_*"), key=lambda p: p.stat().st_mtime)
    notes = []
    rows = _job_rows(job) if job else None
    if rows:
        zmap = {r[1]: {"block": r[2], "zone": r[3], "idf": r[4]} for r in rows if r[0] == "handle"}
        (out / "zones.json").write_text(json.dumps(zmap, ensure_ascii=False, indent=1), encoding="utf-8")
        site = next((dict(c.split("=", 1) for c in r[1:]) for r in rows if r[0] == "site"), {})
        on = [k.replace("Output", "") for k in OUTPUT_KEYS.split(",") if site.get(k) == "1"]
        notes.append(f"輸出頻率:{'、'.join(on) or '只有全年'}" + ("(這次開啟了逐時輸出,模型副本會保留這個設定)" if hourly else ""))
        if hourly and site.get("HourlyOutput") != "1":
            notes.append("**逐時輸出沒有開成功**,請在 DB 的模擬選項手動勾選 Hourly。")
    elif hourly:
        notes.append("**沒有開到逐時輸出**:模型裡沒有 DbMcpBridge(或沒啟用),這次只有 DB 原本設定的輸出頻率。")
    if notes:
        report += "\n\n" + "\n".join(notes) + "\n要逐時或逐熱區的結果,呼叫 `db_results`。"
    return report


OUTPUT_KEYS = "HourlyOutput,DailyOutput,MonthlyOutput"


@mcp.tool()
def db_results(output_dir: str, items: list, freq: str = "hourly", zones: Optional[list] = None) -> str:
    """從 db_simulate 的輸出資料夾取出逐時 / 逐日 / 逐月結果, 存成 CSV(Excel 可直接打開)。
    只取模擬已輸出的資料; 沒輸出的項目會列出來並說明怎麼開, 不推算。

    Args:
        output_dir: db_simulate 回傳的輸出資料夾(sim_...)
        items: 要的項目, 從這些選: 總用電、照明用電、設備用電、供暖量、供冷量、天然氣、室外溫度(建築整體),
            室溫、操作溫度、相對濕度、供暖負荷、供冷負荷(每個熱區一欄)
        freq: hourly(預設)/ daily / monthly
        zones: 熱區類項目只取這些熱區或區塊(例如 ["F01", "F02 Core"]); 不給或 "*" 為全部
    """
    sim = _guard(output_dir)
    if not (sim / "eplusout.eso").exists():
        raise ValueError(f"{sim} 裡沒有 eplusout.eso;請給 db_simulate 回傳的輸出資料夾")
    zmap_file = sim / "zones.json"
    zmap = json.loads(zmap_file.read_text(encoding="utf-8")) if zmap_file.exists() else {}
    cols, time_cols, rows, missing, checks, title = results.extract(sim, items, freq, zones, zmap)
    lines = [f"## 模擬結果({freq}):{sim.name}", ""]
    if cols:
        out = sim / f"results_{freq}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(out, "w", newline="", encoding="utf-8-sig") as f:  # 有 BOM, Excel 打開中文不會亂碼
            w = csv.writer(f)
            w.writerow(time_cols + cols)
            w.writerows(rows)
        lines += [f"- 存成 `{out}`:{len(rows)} 列 × {len(cols)} 欄(模擬期間 {title})。", "",
                  "| 欄 | 最小 | 平均 | 最大 |", "|---|---:|---:|---:|"]
        for j, c in enumerate(cols[:30]):
            vals = [r[len(time_cols) + j] for r in rows if r[len(time_cols) + j] is not None]
            if vals:
                lines.append(f"| {c} | {min(vals):,.2f} | {sum(vals) / len(vals):,.2f} | {max(vals):,.2f} |")
        if len(cols) > 30:
            lines.append(f"(只列出前 30 欄;共 {len(cols)} 欄,完整資料在 CSV)")
    if checks:
        lines += ["", "核對(各時段加總 vs EnergyPlus 的全年值):"]
        for c, s, total in checks:
            ok = total is not None and abs(s - total) <= 1e-6 * max(1.0, abs(total))
            lines.append(f"- {c}:加總 {s:,.1f},全年 {total:,.1f}{'(一致)' if ok else '(**不一致**)'}")
    if missing:
        lines += ["", "**沒有的項目**(模擬沒有輸出,不會推算):"]
        for item, have in missing:
            lines.append(f"- {item}:" + ("只有 " + "、".join(have) + " 的輸出" if have and have[0] != "沒有符合的熱區"
                                          else "沒有符合的熱區" if have else "這次模擬完全沒有輸出這個量"))
        if any(have and have[0] != "沒有符合的熱區" for _, have in missing) and freq == "hourly":
            lines.append("要逐時資料,請用 `db_simulate(..., hourly=True)` 重跑(需要模型裡有 DbMcpBridge)。")
    if any(results.ITEMS[i][0] == "zone" for i in items) and not zmap:
        lines.append("\n這個資料夾沒有 zones.json(熱區代號對照),熱區欄用 EnergyPlus 的數字代號;"
                     "用新版 `db_simulate` 重跑會自動記下熱區名稱。")
    return "\n".join(lines)


def _has_bridge(dsb: Path):
    with zipfile.ZipFile(dsb) as z:
        return BRIDGE_MARKER in z.read(z.infolist()[0])


def _simulate(dsb_path, dsb: Path, attributes, start_date, end_date, timeout_s):
    chain = ProcessChain()
    for label, d in (("start", start_date), ("end", end_date)):
        if d:
            day, month = (int(x) for x in d.split("/"))
            chain.sim_start_date(day, month) if label == "start" else chain.sim_end_date(day, month)
    for k, v in (attributes or {}).items():
        chain.change_attribute(k, v)
    chain.switch_screen(Screen.SIMULATION).run()

    started = time.time()
    err_file = DB_EP_DIR / "eplusout.err"

    def finished():  # E+ 結束時 .err 最後一行是 Completed / Terminated; 表格報告稍後才寫完
        if not _newer_than(err_file, started)():
            return False
        tail = err_file.read_text(encoding="latin-1", errors="replace")[-400:]
        return ("Completed Successfully" in tail or "Terminated" in tail) and \
            _newer_than(DB_EP_DIR / "eplustbl.htm", started)()

    secs, closed = _run_chain(dsb, chain, timeout_s, finished)
    return _collect(dsb_path, dsb, started, secs, closed)


def _collect(dsb_path, dsb: Path, started: float, secs, closed=True):
    """把 DB 這次(started 之後)寫出的 E+ 結果複製到模型副本旁的 sim_<時間>/, 再寫摘要。"""
    out = dsb.parent / datetime.now().strftime("sim_%Y%m%d_%H%M%S")
    out.mkdir()
    # in.epw 是 DB 從氣象庫複製的, 保留原檔的舊時間, 不能用時間判斷; 只要這次有新的 in.idf 就一起收
    fresh = [f for f in EP_OUTPUTS if (DB_EP_DIR / f).exists() and (DB_EP_DIR / f).stat().st_mtime >= started - 5]
    if "in.idf" in fresh and (DB_EP_DIR / "in.epw").exists():
        fresh.append("in.epw")
    for f in fresh:
        shutil.copyfile(DB_EP_DIR / f, out / f)
    err = (out / "eplusout.err").read_text(encoding="latin-1") if (out / "eplusout.err").exists() else ""
    return _simulate_report(dsb_path, out, err, secs, closed)


def _simulate_report(dsb_path, out: Path, err: str, secs, closed=True):
    ok = "EnergyPlus Completed Successfully" in err
    warn = len(re.findall(r"\*\* Warning \*\*", err))
    lines = []
    if ok and (out / "eplustbl.htm").exists():
        md = to_markdown(summarize(out / "eplustbl.htm"), title=f"{Path(dsb_path).stem}:年度模擬完成({secs} 秒)")
        (out / "summary.md").write_text(md, encoding="utf-8")
        lines.append(md)
        if warn:
            lines.append(f"EnergyPlus 有 {warn} 個警告(一般不影響結果,細節在 eplusout.err)。")
        lines.append("下一步:要算節能措施和回本年數,呼叫 `db_measures_report`,output_dir 填下面的資料夾。")
    elif ok:
        lines += ["## 模擬完成,但沒有產生 eplustbl.htm", "", "DB 的輸出設定可能關掉了表格報告。"]
    else:
        lines += ["## 模擬沒有成功", ""]
        lines += [f"    {ln}" for ln in _err_lines(err)] if err else [
            "沒有產生 eplusout.err:EnergyPlus 沒有啟動。常見原因是 DB 停在對話框(缺氣象檔、模型錯誤),請使用者看一下 DB 畫面。"]
    lines.append(f"\n輸出資料夾:`{out}`")
    if not closed:
        lines.append(CLOSE_NOTE)
    return "\n".join(lines)


@mcp.tool()
def db_measures_report(output_dir: str) -> str:
    """對 db_simulate 的結果跑 8 種節能措施(燈具、貼膜、氣密、屋頂反射漆、設備、隔熱、空調), 算省多少與幾年回本。
    在背景執行約 5–10 分鐘, 用和 DB 同一個氣象檔; 完成後產生 report.html(白話)與 results.csv。

    Args:
        output_dir: db_simulate 回傳的 output_dir
    Returns: 報告會出現的位置; 用 db_report_status 查進度。
    """
    d = _guard(output_dir)
    if not (d / "in.idf").exists():
        raise ValueError(f"{d} 沒有 in.idf; 先用 db_simulate 跑一次")
    for old in ("run.log", "report.html", "report.md", "results.csv"):  # 不然 db_report_status 會讀到上次的結果
        (d / old).unlink(missing_ok=True)
    name = d.parent.name
    idf = d / f"{name}.idf"
    shutil.copyfile(d / "in.idf", idf)
    epw = d / "in.epw"
    subprocess.Popen(stdin=subprocess.DEVNULL, args=[str(MAIN_PY), str(WS / "tools" / "designbuilder" / "from_db.py"), str(idf),
                      str(epw) if epw.exists() else ""], cwd=WS, creationflags=subprocess.CREATE_NO_WINDOW)
    return "\n".join(["## 節能措施報告開始計算", "",
                      "在背景跑基準與 8 種措施,約 5–10 分鐘。用 `db_report_status` 查進度(output_dir 同上)。", "",
                      f"- 完成後的報告:`{d / 'report.html'}`", f"- 進度紀錄:`{d / 'run.log'}`"])


@mcp.tool()
def db_report_status(output_dir: str) -> str:
    """查 db_measures_report 的進度。完成時回傳每個措施的省能 %、年省金額與回本年數。"""
    d = _guard(output_dir)
    log = (d / "run.log").read_text(encoding="utf-8", errors="replace") if (d / "run.log").exists() else ""
    if "完成" not in log:
        tail = "\n".join(f"    {ln}" for ln in log.strip().splitlines()[-5:]) or "    (還沒有輸出)"
        if "Traceback" in log:
            return f"## 節能報告失敗\n\n最後幾行紀錄:\n{tail}\n\n完整紀錄:`{d / 'run.log'}`"
        return f"## 還在算(通常 5–10 分鐘)\n\n最後幾行紀錄:\n{tail}"
    return _measures_table(d)


def _measures_table(d: Path):
    with open(d / "results.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    cur = rows[0].get("currency", "") if rows else ""
    lines = ["## 節能措施結果", "", f"| 措施 | 判讀 | 省能 | 每年省 {cur} | 投資 {cur} | 回本 |", "|---|---|---:|---:|---:|---|"]
    order = {"good": 0, "ok": 1, "info": 2, "bad": 3, "skip": 4}
    for r in sorted((r for r in rows if r["case"] in MEASURES), key=lambda r: order[verdict(r)[0]]):
        level, _, label, why = verdict(r)
        name = MEASURES[r["case"]][0]
        if level == "skip":
            lines.append(f"| {name} | {label} | — | — | — | {why} |")
            continue
        y = float(r["payback_years"]) if r["payback_years"] else None
        capex = f"{float(r['capex_cost']):,.0f}" if num(r["capex_cost"]) else "—"
        lines.append(f"| {name} | {label} | {float(r['saving_pct']):.1f}% | {float(r['saving_cost'] or 0):,.0f} | "
                     f"{capex} | {yrs(y) if y else '—'} |")
    lines += ["", "金額是 costs.json 的假設值,不是報價。", f"給使用者看的完整報告:`{d / 'report.html'}`"]
    return "\n".join(lines)


@mcp.tool()
def db_calculate(dsb_path: str, kind: str, timeout_s: int = 3600) -> str:
    """用 DesignBuilder 跑其他計算, 結果存在模型副本裡(用 db_open 打開副本就能在 DB 看結果頁)。

    Args:
        dsb_path: .dsb 的完整路徑
        kind: "heating_cooling_design"(暖氣 / 冷氣設計負載)、"daylighting"(採光)、"daylighting_annual"、"cfd"
    """
    chains = {
        "heating_cooling_design": ProcessChain().switch_screen(Screen.HEATING_DESIGN).run()
                                                .switch_screen(Screen.COOLING_DESIGN).run(),
        "daylighting": ProcessChain().switch_screen(Screen.DAYLIGHTING).run(),
        "daylighting_annual": ProcessChain().switch_screen(Screen.DAYLIGHTING).run().tab_change(2).run(),
        "cfd": ProcessChain().switch_screen(Screen.CFD).run(),
    }
    if kind not in chains:
        raise ValueError(f"kind 要是其中之一: {', '.join(chains)}")
    dsb = _working_copy(dsb_path)
    # 結果存回模型檔; 以 .dsb 被 DB 存檔當作完成(未實測, DB 若不自動存檔會等到逾時)
    secs, closed = _run_chain(dsb, chains[kind], timeout_s, _newer_than(dsb, time.time()))
    lines = [f"## {kind} 計算完成({secs} 秒)", "", f"結果存在模型副本 `{dsb}`;用 `db_open` 打開它,在 DB 的結果頁查看。"]
    if not closed:
        lines.append(CLOSE_NOTE)
    return "\n".join(lines)


# ---- 經由模型裡的 DbMcpBridge 腳本讀寫熱區屬性(DB 官方 API) ----

BRIDGE_SETUP = ("這個模型還沒有裝 DbMcpBridge 腳本(或沒有啟用)。請在 DB 打開模型 → Scripts → Script Manager:"
                "在右邊 CS-Script 資料夾新增腳本、貼上 tools/designbuilder_mcp/DbMcpBridge.cs, Enable program = Yes;"
                "左邊點「Script」列, 右邊選這個腳本按綠色勾勾; 勾 Enable scripts; 存檔後關閉 DB。")
ZONE_KEYS = ("ActivityTemplateId", "OccupancyValue", "HeatingDesignSetPointTemperature",
             "ActiveCoolingDesignSetPointTemperature", "MinFreshAir", "OccupancySchedule", "FluorescentLightingValue")


def _write_job(dsb: Path, lines):
    job = dsb.parent / f"_job_{datetime.now():%Y%m%d_%H%M%S}.txt"
    job.write_text("\n".join("\t".join(map(str, ln)) for ln in lines) + "\n", encoding="ascii")
    return job


def _job_rows(job: Path):
    """DbMcpBridge 的輸出各行(欄位清單); 腳本沒執行(沒有 .out)回傳 None。"""
    out = Path(str(job) + ".out")
    if not out.exists():
        return None
    return [ln.split("\t") for ln in out.read_text(encoding="utf-8", errors="replace").splitlines() if ln]


def _bridge(dsb: Path, lines, timeout_s=900, after=False):
    """把工作清單交給模型裡的 DbMcpBridge 執行。DB 只在開始計算時載入腳本, 所以順便跑一次暖房設計(約 1 分鐘)。
    after=True 時等暖房設計算完才執行(匯入模型這類會換掉整棟建築的工作, 計算中做會讓 DB 當掉)。
    回傳 (輸出各行的欄位清單, 秒數)。"""
    job = _write_job(dsb, lines)
    os.environ["DBMCP_JOB"] = str(job)
    if after:
        os.environ["DBMCP_PHASE"] = "after"
    try:
        secs, _ = _run_chain(dsb, ProcessChain().switch_screen(Screen.HEATING_DESIGN).run(), timeout_s)
    finally:
        os.environ.pop("DBMCP_JOB", None)
        os.environ.pop("DBMCP_PHASE", None)
    rows = _job_rows(job)
    if rows is None:
        raise RuntimeError(BRIDGE_SETUP)
    err = [r for r in rows if r[0] == "error"]
    if err:
        raise RuntimeError("DbMcpBridge 執行失敗:" + " ".join(err[0][1:]))
    return rows, secs


def _zones(rows):
    """dump 的輸出 → [{block, zone, idf, area, attrs}];同一份輸出裡有多次 dump 時取最後一次。"""
    dumps, cur = [], None
    for r in rows:
        if r[0] == "zone":
            if cur is None:
                cur = []
                dumps.append(cur)
            # 剛匯入、還沒算過的模型沒有 IDF 名稱, 改用熱區名稱(DbMcpBridge 的 set 兩者都認)
            cur.append({"block": r[2], "zone": r[3], "idf": r[4] or r[3], "area": float(r[5]),
                        "attrs": dict(c.split("=", 1) for c in r[6:] if "=" in c)})
        else:
            cur = None
    return dumps


def _after_map(rows):
    """寫入後讀回的熱區屬性, 用 IDF 名稱與熱區名稱都查得到: 剛匯入的模型寫入時還沒有 IDF 名稱(用熱區名稱),
    同一次 DB 計算後才產生 IDF 名稱。"""
    out = {}
    for z in (_zones(rows) or [[]])[-1]:
        out[z["idf"]] = out[z["zone"]] = z["attrs"]
    return out


def _resolve(zones, selector):
    """熱區選擇器: IDF 名稱、區塊名稱(該區塊所有熱區)、或 * (全部)。"""
    if selector == "*":
        return zones
    s = selector.strip().lower()
    hit = [z for z in zones if z["idf"].lower() == s]
    return hit or [z for z in zones if z["block"].strip().lower() == s]


def _fmt_zone_table(zones):
    lines = ["| 區塊 | 熱區 | IDF 名稱 | 面積 m² | 目前用途 | 人員 人/m² | 暖 / 冷設定 °C |", "|---|---|---|---:|---|---:|---|"]
    for z in zones:
        a = z["attrs"]
        lines.append(f"| {z['block']} | {z['zone']} | `{z['idf']}` | {z['area']:,.1f} | "
                     f"{dblibrary.activity_label(a.get('ActivityTemplateId', ''))} | {a.get('OccupancyValue', '')} | "
                     f"{a.get('HeatingDesignSetPointTemperature', '')} / {a.get('ActiveCoolingDesignSetPointTemperature', '')} |")
    return lines


@mcp.tool()
def db_zones(dsb_path: str) -> str:
    """列出模型(副本)的所有熱區:所屬區塊、IDF 名稱、面積、目前生效的用途、人員密度、冷暖設定。
    讀的是 DB 實際生效值(含從上層繼承的)。需要模型裡裝有 DbMcpBridge 腳本; 會在背景跑一次暖房設計(約 1 分鐘)。"""
    dsb = _working_copy(dsb_path)
    rows, secs = _bridge(dsb, [("dump", ",".join(ZONE_KEYS))])
    zones = (_zones(rows) or [[]])[-1]
    return "\n".join([f"## {dsb.stem}:{len(zones)} 個熱區({secs} 秒)", ""] + _fmt_zone_table(zones) +
                     ["", "要改用途,呼叫 `db_assign_activity`,熱區用 IDF 名稱或區塊名稱指定。",
                      f"可用的用途:{'、'.join(dblibrary.SPACE_TYPES)}"])


BRIDGE_CS = HERE / "DbMcpBridge.cs"
BRIDGE_MARKER = b"/*^DbMcpBridge"


def _patch_bridge(dsb: Path):
    """把 .dsb 裡已裝的 DbMcpBridge 換成 repo 版本。.dsb 是只含一個 .skh 的 zip;
    腳本存成帶長度的字串(0xFF + 2 位元組 little-endian 長度), 換行存成 ^, # 存成 _HASH_。
    回傳 (舊長度, 新長度, 備份檔); 已是最新版回傳 None。"""
    src = BRIDGE_CS.read_text(encoding="ascii").replace("\r\n", "\n")
    new = src.replace("#", "_HASH_").replace("\n", "^").encode("ascii")
    with zipfile.ZipFile(dsb) as z:
        infos = z.infolist()
        if len(infos) != 1:
            raise RuntimeError(f"{dsb.name} 的格式和預期不同(zip 裡有 {len(infos)} 個檔), 沒有更動。")
        data = z.read(infos[0])
    n = data.count(BRIDGE_MARKER)
    if n == 0:
        raise RuntimeError("這個模型裡沒有 DbMcpBridge, 只能更新、不能第一次安裝。" + BRIDGE_SETUP)
    if n > 1:
        raise RuntimeError(f"模型裡有 {n} 份 DbMcpBridge, 不確定要換哪一份, 沒有更動。請在 Script Manager 刪到只剩一份。")
    start = data.index(BRIDGE_MARKER)
    if data[start - 3] != 0xFF:
        raise RuntimeError("腳本前的長度欄位和預期不同, 沒有更動。")
    old_len = int.from_bytes(data[start - 2:start], "little")
    if data[start:start + old_len] == new:
        return None
    patched = data[:start - 2] + len(new).to_bytes(2, "little") + new + data[start + old_len:]
    backup = dsb.with_name(f"{dsb.stem}_before_bridge_update_{datetime.now():%Y%m%d_%H%M%S}.dsb")
    shutil.copy2(dsb, backup)
    tmp = dsb.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(infos[0], patched, compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(dsb)
    return old_len, len(new), backup


@mcp.tool()
def db_update_bridge(dsb_path: str, check: bool = True) -> str:
    """把模型(副本)裡已裝的 DbMcpBridge 腳本換成 repo 裡的最新版, 不用開 DB 手動貼。
    只能更新已裝過的模型; 第一次安裝仍要在 Script Manager 做。原檔先備份在同一個資料夾。
    check=True 時在背景開 DB 跑一次測試工作(約 1 分鐘), 確認 DB 讀得到新版。"""
    _check_idle()
    dsb = _working_copy(dsb_path)
    res = _patch_bridge(dsb)
    if res is None:
        lines = [f"`{dsb}` 裡的 DbMcpBridge 已經是最新版, 沒有更動。"]
    else:
        old_len, new_len, backup = res
        lines = [f"已更新 `{dsb}` 裡的 DbMcpBridge({old_len} → {new_len} 字元)。備份:`{backup.name}`。"]
    if check:
        # 新版對找不到的熱區回 nozone(舊版寫 no matching zone), 這行不會改到任何東西
        rows, secs = _bridge(dsb, [("dump", "ActivityTemplateId"), ("set", "_DBMCP_NO_SUCH_ZONE_", "OccupancyValue", "1")])
        zones = (_zones(rows) or [[]])[-1]
        ok = any(r[0] == "nozone" for r in rows) and rows[-1] == ["done"]
        lines.append(f"DB 測試({secs} 秒):讀到 {len(zones)} 個熱區, " +
                     ("新版腳本正常執行。" if ok else "但回應不是新版格式, 請確認 Script Manager 裡用的是這份腳本。"))
    return "\n".join(lines)


def _same(a, b):
    try:
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    except ValueError:
        return str(a).strip() == str(b).strip()


def _lpd(light):
    return f"{float(light['FluorescentLightingValue']):.2f}" if light else "(不變)"


@mcp.tool()
def db_assign_activity(dsb_path: str, assignments: dict, apply: bool = False) -> str:
    """把熱區換成指定用途: 寫入 DB 活動範本編號, 並逐項寫入該範本的人員、設備、作息、設定溫度、新風等數值
    (DB 介面換範本會自動帶入這些值, API 不會)。數值全部取自本機 DB 範本庫, 不由 AI 決定。

    Args:
        dsb_path: .dsb 的完整路徑(操作的是副本, 不改原檔)
        assignments: {熱區選擇器: 中文用途}; 選擇器是 IDF 名稱、區塊名稱或 "*"; 用途見 db_zones 輸出的清單,
            例如 {"FIRSTFLOORBLOCK:ZONE1": "廁所", "Ground floor block": "辦公"}
        apply: False(預設)只產生預覽, 不改模型; 使用者確認後再用 True 寫入、存檔並讀回核對
    """
    dsb = _working_copy(dsb_path)
    try:
        plan_uses = {sel: (dblibrary.resolve_space(use), dblibrary.lighting_for_space(use))
                     for sel, use in assignments.items()}
    except KeyError as e:
        raise ValueError(str(e).strip("'\""))
    rows, secs = _bridge(dsb, [("dump", ",".join(ZONE_KEYS))])
    zones = (_zones(rows) or [[]])[-1]
    targets = {}
    for sel, (tpl, light) in plan_uses.items():
        hit = _resolve(zones, sel)
        if not hit:
            raise ValueError(f"找不到熱區或區塊「{sel}」;可用 db_zones 查看名稱")
        for z in hit:
            targets[z["idf"]] = (z, tpl, light)
    lines = [f"## 套用用途{'(已寫入)' if apply else '(預覽,尚未寫入)'}:{dsb.stem}", "",
             "| 熱區 | 目前用途 | 改成 | 人員 人/m² | 暖 / 冷設定 °C | 新風 L/s·人 | 照明 W/m² | 在室作息 |",
             "|---|---|---|---|---|---|---|---|"]
    for idf, (z, tpl, light) in targets.items():
        a, s = z["attrs"], dblibrary.activity_summary(tpl)
        sch = s["occupancy_hours"]
        lines.append(f"| `{idf}` | {dblibrary.activity_label(a.get('ActivityTemplateId', ''))} | "
                     f"{dblibrary.activity_label(tpl['Id'])} | {a.get('OccupancyValue', '')} → {tpl.get('OccupancyValue', '')} | "
                     f"{a.get('HeatingDesignSetPointTemperature', '')}/{a.get('ActiveCoolingDesignSetPointTemperature', '')} → "
                     f"{tpl.get('HeatingDesignSetPointTemperature', '')}/{tpl.get('ActiveCoolingDesignSetPointTemperature', '')} | "
                     f"{a.get('MinFreshAir', '')} → {tpl.get('MinFreshAir', '')} | "
                     f"{a.get('FluorescentLightingValue', '')} → {_lpd(light)} | "
                     f"`{dblibrary.spark(sch['平日']) if sch else '—'}` |")
    lines += ["", f"數值來源:本機 DB 範本庫(活動範本編號見「改成」欄),每個熱區寫入範本編號加 "
                  f"{len(dblibrary.ACTIVITY_ATTRIBUTES)} 項以內的屬性(範本裡空白的不寫)。"
                  f"照明功率密度用 {dblibrary.LIGHTING_SOURCE}(Appendix G 基準)的照明範本,計算方式設為 W/m²。"]
    fixes = {}
    for _, tpl, _ in targets.values():
        fix = dblibrary.lighting_schedule_fix(tpl)
        if fix:
            fixes[dblibrary.activity_label(tpl["Id"])] = fix
    if fixes:
        lines.append("照明作息修正(DB 的 ASHRAE 活動範本把照明作息指到空調可用時段,營業時間內全開;"
                     "改用同系列的 ASHRAE 照明作息):")
        lines += [f"- {use}:{old} → {new['Name']}(#{new['Id']},平日 `{dblibrary.spark(dblibrary.schedule_hours(new['Id'])['平日'])}`)"
                  for use, (new, old) in fixes.items()]
    if not apply:
        lines.append("確認無誤後,用同樣參數加上 apply=True 寫入。")
        return "\n".join(lines)

    job, want = [], {}
    for idf, (z, tpl, light) in targets.items():
        attrs = [("ActivityTemplateId", tpl["Id"])] + dblibrary.activity_attributes(tpl)
        if light:
            attrs += dblibrary.lighting_attributes(light)
        want[idf] = dict(attrs)
        job += [("set", idf, k, v) for k, v in attrs]
    keys = sorted({k for w in want.values() for k in w})
    job += [("dump", ",".join(keys)), ("save",)]
    rows, secs2 = _bridge(dsb, job)
    after = _after_map(rows)
    problems = [f"`{r[1]}` {r[2]}:這個熱區沒有此屬性" if r[0] == "nozone" else f"第 {r[1]} 行:{' '.join(r[2:])}"
                for r in rows if r[0] in ("nozone", "lineerror")]
    mismatch = [f"`{idf}` {k}:應為 {v},讀回 {after.get(idf, {}).get(k, '(沒讀到)')}"
                for idf, w in want.items() for k, v in w.items() if not _same(after.get(idf, {}).get(k, ""), v)]
    saved = any(r[0] == "save" and r[1] == "True" for r in rows)
    lines += ["", f"### 寫入結果({secs + secs2} 秒)", "",
              f"- 寫入 {sum(len(w) for w in want.values())} 項屬性,{len(targets)} 個熱區;存檔:{'成功' if saved else '**失敗**'}。",
              f"- 讀回核對:{'全部一致' if not mismatch else f'**{len(mismatch)} 項不一致**'}。"]
    lines += [f"  - {m}" for m in (problems + mismatch)[:20]]
    lines.append(f"\n修改後的模型副本:`{dsb}`(用 `db_open` 打開檢查)")
    return "\n".join(lines)


# ---- 地點與氣象檔: 只從 DB 地點庫裡選 ----

LOCATION_KEYS = ("LocationTemplateId", "ClimaticRegion", "Latitude", "Longitude", "Elevation", "HourlyWeatherData",
                 "WinterToaDB996", "SummerToaMaxDB04")


def _loc_line(loc):
    w, local = dblibrary.weather_of(loc)
    return (f"| {loc['Id']} | {loc['LocationName']} | {loc.get('ClimaticRegion', '')} | "
            f"{loc.get('Latitude', '')}, {loc.get('Longitude', '')} | {loc.get('WinterToaDB996', '')} | "
            f"{loc.get('SummerToaMaxDB04', '')} | {w.get('Filename', '—')} | {'本機有' if local else '**需下載**'} |")


LOC_HEAD = ["| 編號 | 地點 | ASHRAE 氣候區 | 緯度, 經度 | 冬季設計 °C(99.6%) | 夏季設計 °C(0.4%) | 氣象檔 | |",
            "|---:|---|---|---|---:|---:|---|---|"]


@mcp.tool()
def db_find_locations(keyword: str, limit: int = 15) -> str:
    """在 DB 地點庫裡找地點(名稱包含所有關鍵字, 不分大小寫), 列出 ASHRAE 氣候區、經緯度、設計溫度與氣象檔是否已在本機。
    地點名稱多為機場氣象站, 例如 "new york"、"chicago"、"taipei"、"shanghai"。不開 DB。"""
    rows, total = dblibrary.find_locations(keyword, limit)
    lines = [f"## 地點「{keyword}」:{total} 筆" + (f",列出前 {limit} 筆" if total > limit else ""), ""] + LOC_HEAD
    lines += [_loc_line(r) for r in rows]
    lines += ["", "用 `db_set_location` 套用(用編號指定)。「需下載」的氣象檔要先在 DB 的地點設定裡下載,"
                  "或提供 EPW 檔路徑(epw_path)。"]
    return "\n".join(lines)


@mcp.tool()
def db_set_location(dsb_path: str, location: str, epw_path: str = "", apply: bool = False) -> str:
    """把模型(副本)的地點換成 DB 地點庫裡的地點: 經緯度、時區、海拔、ASHRAE 氣候區、冬夏設計條件與逐時氣象檔,
    全部取自 DB 的地點範本(DB 介面換範本會帶入這些, API 只改編號不會)。

    Args:
        dsb_path: .dsb 的完整路徑(操作的是副本, 不改原檔)
        location: 地點範本編號(用 db_find_locations 找)或完整名稱
        epw_path: 氣象檔不在本機時, 使用者提供的 EPW 檔; 會檢查檔頭經緯度與地點相差不到 0.5°,
            寫入時複製到 DB 的氣象檔資料夾(用 DB 預期的檔名)
        apply: False(預設)只產生預覽; 使用者確認後再用 True 寫入、存檔並讀回核對
    """
    try:
        loc = dblibrary.resolve_location(location)
    except KeyError as e:
        raise ValueError(str(e).strip("'\""))
    w, local = dblibrary.weather_of(loc)
    if not w:
        raise ValueError(f"地點「{loc['LocationName']}」在 DB 庫裡沒有對應的逐時氣象檔")
    epw_note = ""
    if not local and epw_path:
        src = _guard(epw_path)
        if not src.exists() or src.suffix.lower() != ".epw":
            raise ValueError(f"找不到 EPW 檔 {epw_path}")
        head = dblibrary.epw_location(src)
        d = max(abs(head["lat"] - float(loc["Latitude"])), abs(head["lon"] - float(loc["Longitude"])))
        if d > 0.5:
            raise ValueError(f"EPW 檔是 {head['city']}({head['lat']}, {head['lon']}),和地點 {loc['LocationName']}"
                             f"({loc['Latitude']}, {loc['Longitude']})相差 {d:.1f}°,不像同一個地方,沒有使用")
        epw_note = f"使用你提供的 EPW({head['city']},WMO {head['wmo']}),寫入時複製成 `{w['Filename']}`。"
    elif not local:
        epw_note = (f"**氣象檔 `{w['Filename']}` 不在本機**(`{dblibrary.WEATHER_DIR}`),不能套用:"
                    "請在 DB 的地點設定裡下載這個氣象檔,或用 epw_path 提供 EPW 檔。")
    dsb = _working_copy(dsb_path)
    want = dict(dblibrary.location_attributes(loc))
    rows, secs = _bridge(dsb, [("sdump", ",".join(LOCATION_KEYS))])
    before = next((dict(c.split("=", 1) for c in r[1:]) for r in rows if r[0] == "site"), {})
    cur = dblibrary.table("LocationTemplates").get(before.get("LocationTemplateId", ""))
    lines = [f"## 套用地點{'(已寫入)' if apply else '(預覽,尚未寫入)'}:{dsb.stem}", "",
             "| | 地點 | ASHRAE 氣候區 | 緯度, 經度 | 冬季設計 °C(99.6%) | 夏季設計 °C(0.4%) | 氣象檔 |",
             "|---|---|---|---|---:|---:|---|",
             f"| 目前 | {cur['LocationName'] if cur else '#' + before.get('LocationTemplateId', '')} | "
             f"{before.get('ClimaticRegion', '')} | {before.get('Latitude', '')}, {before.get('Longitude', '')} | "
             f"{before.get('WinterToaDB996', '')} | {before.get('SummerToaMaxDB04', '')} | "
             f"{dblibrary.table('HourlyWeather').get(before.get('HourlyWeatherData', ''), {}).get('Filename', '')} |",
             f"| 改成 | {loc['LocationName']}(#{loc['Id']},{loc.get('Source', '')}) | {loc.get('ClimaticRegion', '')} | "
             f"{loc['Latitude']}, {loc['Longitude']} | {loc.get('WinterToaDB996', '')} | {loc.get('SummerToaMaxDB04', '')} | "
             f"{w['Filename']} |", "",
             f"寫入地點範本的 {len(want)} 項屬性(含逐月設計條件)。"]
    if epw_note:
        lines.append(epw_note)
    if loc.get("ClimaticRegion"):
        lines.append(f"這個地點的 ASHRAE 氣候區是 {loc['ClimaticRegion']};"
                     f"圍護結構可用 `db_assign_construction(climate_zone=\"{loc['ClimaticRegion']}\")`。")
    if not apply:
        lines.append("確認無誤後,用同樣參數加上 apply=True 寫入。")
        return "\n".join(lines)
    if not local and not epw_path:
        raise ValueError(epw_note.replace("**", ""))
    if not local:
        shutil.copy2(epw_path, dblibrary.WEATHER_DIR / w["Filename"])
    rows, secs2 = _bridge(dsb, [("sset", k, v) for k, v in want.items()] + [("sdump", ",".join(want)), ("save",)])
    after = next((dict(c.split("=", 1) for c in r[1:]) for r in rows if r[0] == "site"), {})
    # 範本裡有些欄位不是 Site 屬性(WMO、Pressure、KoppenClass): DB 讀回 "UNKNOWN", 寫不進去也不影響模擬
    absent = [k for k in want if after.get(k) == "UNKNOWN"]
    mismatch = [f"{k}:應為 {v},讀回 {after.get(k, '(沒讀到)')}" for k, v in want.items()
                if k not in absent and not _same(after.get(k, ""), v)]
    problems = [f"第 {r[1]} 行:{' '.join(r[2:])}" for r in rows if r[0] == "lineerror"]
    saved = any(r[0] == "save" and r[1] == "True" for r in rows)
    lines += ["", f"### 寫入結果({secs + secs2} 秒)", "",
              f"- 寫入 {len(want) - len(absent)} 項 Site 屬性;存檔:{'成功' if saved else '**失敗**'}。",
              f"- 讀回核對:{'全部一致' if not mismatch else f'**{len(mismatch)} 項不一致**'}。"]
    lines += [f"  - {m}" for m in (problems + mismatch)[:20]]
    if absent:
        lines.append(f"- 範本裡這些欄位 DB 的 Site 沒有對應屬性,沒有寫入(只是範本的說明資料):{'、'.join(absent)}。")
    lines.append(f"\n修改後的模型副本:`{dsb}`。下次 `db_simulate` 會用這個地點與氣象檔。")
    return "\n".join(lines)


# ---- 作息: 只從 DB 庫裡選 ----

def _sched_label(sid):
    name = dblibrary.name_of("Schedules", sid)
    return f"{name} (#{sid})" if name else f"#{sid}"


def _sched_spark(sid):
    h = dblibrary.schedule_hours(sid)
    return f"`{dblibrary.spark(h['平日'])}`" if h else "—"


@mcp.tool()
def db_find_schedules(keyword: str, limit: int = 20) -> str:
    """在本機 DB 作息庫裡找作息(名稱包含所有關鍵字, 空白分隔, 不分大小寫), 列出編號、名稱、來源與平日 / 週六 / 週日的
    逐時小圖。給 db_assign_schedule 選用; 不開 DB。例: "office occ"、"retail light"、"24/7"。"""
    rows, total = dblibrary.find_schedules(keyword, limit)
    lines = [f"## 作息「{keyword}」:{total} 筆" + (f",列出前 {limit} 筆" if total > limit else ""), "",
             "| 編號 | 名稱 | 來源 | 平日 | 週六 | 週日 |", "|---:|---|---|---|---|---|"]
    for s in rows:
        h = dblibrary.schedule_hours(s["Id"])
        cells = [f"`{dblibrary.spark(h[d])}`" if h else "—" for d in ("平日", "週六", "週日")]
        lines.append(f"| {s['Id']} | {s['Name']} | {s.get('Source', '')} | " + " | ".join(cells) + " |")
    lines += ["", "小圖每格一小時(0–24 時),越高越滿。名稱重複時請用編號指定。"]
    return "\n".join(lines)


@mcp.tool()
def db_assign_schedule(dsb_path: str, assignments: dict, apply: bool = False) -> str:
    """把熱區的作息換成 DB 作息庫裡既有的作息(不產生新作息)。

    Args:
        dsb_path: .dsb 的完整路徑(操作的是副本, 不改原檔)
        assignments: {熱區選擇器: {種類: 作息編號或完整名稱}}; 選擇器是 IDF 名稱、區塊名稱或 "*";
            種類: 人員、照明、設備(電腦、辦公設備、雜項、餐飲、製程一起換)、熱水。
            例如 {"*": {"照明": 1097}, "Ground floor block": {"人員": "Office_OpenOff_Occ"}}; 作息用 db_find_schedules 找
        apply: False(預設)只產生預覽; 使用者確認後再用 True 寫入、存檔並讀回核對
    """
    dsb = _working_copy(dsb_path)
    plan = {}
    try:
        for sel, kinds in assignments.items():
            for kind, ref in kinds.items():
                if kind not in dblibrary.SCHEDULE_KINDS:
                    raise KeyError(f"不認得的作息種類「{kind}」; 可用的有: {'、'.join(dblibrary.SCHEDULE_KINDS)}")
                plan.setdefault(sel, {})[kind] = dblibrary.resolve_schedule(ref)
    except KeyError as e:
        raise ValueError(str(e).strip("'\""))
    keys = sorted({a for attrs in dblibrary.SCHEDULE_KINDS.values() for a in attrs})
    rows, secs = _bridge(dsb, [("dump", ",".join(keys))])
    zones = (_zones(rows) or [[]])[-1]
    targets = {}  # idf → {種類: 作息 row}
    for sel, kinds in plan.items():
        hit = _resolve(zones, sel)
        if not hit:
            raise ValueError(f"找不到熱區或區塊「{sel}」;可用 db_zones 查看名稱")
        for z in hit:
            targets.setdefault(z["idf"], {}).update(kinds)
    attrs_of = {z["idf"]: z["attrs"] for z in zones}
    lines = [f"## 套用作息{'(已寫入)' if apply else '(預覽,尚未寫入)'}:{dsb.stem}", "",
             "| 熱區 | 種類 | 目前 | 改成 | 改成的平日作息 |", "|---|---|---|---|---|"]
    for idf, kinds in targets.items():
        for kind, s in kinds.items():
            cur = attrs_of[idf].get(dblibrary.SCHEDULE_KINDS[kind][0], "")
            lines.append(f"| `{idf}` | {kind} | {_sched_label(cur)} | {_sched_label(s['Id'])} | {_sched_spark(s['Id'])} |")
    lines += ["", "作息全部取自本機 DB 作息庫;「設備」會同時換電腦、辦公設備、雜項、餐飲、製程五項的作息。"]
    if not apply:
        lines.append("確認無誤後,用同樣參數加上 apply=True 寫入。")
        return "\n".join(lines)

    job, want = [], {}
    for idf, kinds in targets.items():
        want[idf] = {a: s["Id"] for kind, s in kinds.items() for a in dblibrary.SCHEDULE_KINDS[kind]}
        job += [("set", idf, a, v) for a, v in want[idf].items()]
    job += [("dump", ",".join(keys)), ("save",)]
    rows, secs2 = _bridge(dsb, job)
    after = _after_map(rows)
    problems = [f"`{r[1]}` {r[2]}:這個熱區沒有此屬性" if r[0] == "nozone" else f"第 {r[1]} 行:{' '.join(r[2:])}"
                for r in rows if r[0] in ("nozone", "lineerror")]
    mismatch = [f"`{idf}` {k}:應為 {v},讀回 {after.get(idf, {}).get(k, '(沒讀到)')}"
                for idf, w in want.items() for k, v in w.items() if not _same(after.get(idf, {}).get(k, ""), v)]
    saved = any(r[0] == "save" and r[1] == "True" for r in rows)
    lines += ["", f"### 寫入結果({secs + secs2} 秒)", "",
              f"- 寫入 {sum(len(w) for w in want.values())} 項屬性,{len(targets)} 個熱區;存檔:{'成功' if saved else '**失敗**'}。",
              f"- 讀回核對:{'全部一致' if not mismatch else f'**{len(mismatch)} 項不一致**'}。"]
    lines += [f"  - {m}" for m in (problems + mismatch)[:20]]
    lines.append(f"\n修改後的模型副本:`{dsb}`(用 `db_open` 打開檢查)")
    return "\n".join(lines)


# ---- 圍護結構: ASHRAE 90.1-2019 基準, 可選目標 U ----

def _buildings(rows):
    return [dict(c.split("=", 1) for c in r[1:] if "=" in c) for r in rows if r[0] == "building"]


def _records(rows, table):
    return {r[2]: dict(c.split("=", 1) for c in r[3:] if "=" in c) for r in rows if r[0] == "rec" and r[1] == table}


def _cons_label(cid, model_cons):
    """構造編號 → (名稱, U)。模型自己的構造(編號 10000 起)用模型裡讀出的欄位, 其他用範本庫。"""
    row = model_cons.get(str(cid)) or dblibrary.table("Constructions").get(str(cid))
    if not row:
        return f"#{cid}", None
    return row.get("Name", f"#{cid}"), dblibrary.construction_u_row(row)[0]


def _u(u):
    return f"{u:.3f}" if u else "—(F 係數)"


@mcp.tool()
def db_assign_construction(dsb_path: str, climate_zone: str = "4A", building_type: str = "非住宅",
                           target_u: Optional[dict] = None, window_shgc: Optional[float] = None,
                           apply: bool = False) -> str:
    """把整棟建築的構造換成 ASHRAE 90.1-2019 基準(Appendix G)構造:外牆、屋頂、外挑樓板、地坪、外門、外窗玻璃,
    連同範本裡的內部構造與滲風一起寫入(DB 介面換範本會帶入這些, API 只改編號不會)。U 值由材料層計算。
    寫在建築層級, 沒有自己設定的區塊與熱區會跟著繼承; 有自己設定的會列出來, 不強改。

    Args:
        dsb_path: .dsb 的完整路徑(操作的是副本, 不改原檔)
        climate_zone: ASHRAE 氣候區, 例如 4A(預設)、5B、3C
        building_type: 非住宅(預設)、住宅、半空調
        target_u: 選填, {部位: 目標 U (W/m²K)}, 部位限 外牆、屋頂、外挑樓板、外窗。牆、屋頂、樓板會複製基準構造、
            調整保溫層厚度; 外窗複製基準玻璃(簡易定義)改 U 值。都在模型裡新增一筆, 例如 {"外牆": 0.4, "外窗": 1.8}
        window_shgc: 選填, 外窗的日射得熱係數 SHGC(0–1); 不給沿用基準玻璃
        apply: False(預設)只產生預覽; 使用者確認後再用 True 寫入、存檔並讀回核對
    """
    try:
        tmpl, glazing = dblibrary.baseline_envelope(climate_zone, building_type)
    except (KeyError, ValueError) as e:
        raise ValueError(str(e).strip("'\""))
    target_u = {k: float(v) for k, v in (target_u or {}).items()}
    bad = [p for p in target_u if p not in dblibrary.TARGET_U_PARTS + ("外窗",)]
    if bad:
        raise ValueError(f"目標 U 只能給 {'、'.join(dblibrary.TARGET_U_PARTS + ('外窗',))};不能給:{'、'.join(bad)}")
    cons = dblibrary.table("Constructions")
    want = dict(dblibrary.template_construction_attributes(tmpl))
    want["ConstructionTemplateId"] = tmpl["Id"]
    want["GlazingType"] = glazing["Id"]
    # copies: (表, 基準紀錄編號, 要改的欄位); 新紀錄的編號 DB 存檔時才知道, 先用 $NEWk 代替
    copies, notes = [], {}
    for part, attrs in dblibrary.ENVELOPE_PARTS:
        if part not in target_u:
            continue
        base = want[attrs[0]]
        try:
            changes, note = dblibrary.adjust_to_u(cons[base], target_u[part])
        except ValueError as e:
            raise ValueError(f"{part}:{e}")
        copies.append(("Constructions", base, changes))
        notes[part] = note
        for a in attrs:
            want[a] = f"$NEW{len(copies)}"
    if "外窗" in target_u or window_shgc is not None:
        try:
            changes, note = dblibrary.adjust_glazing(glazing, target_u.get("外窗"), window_shgc)
        except ValueError as e:
            raise ValueError(f"外窗:{e}")
        copies.append(("Glazing", glazing["Id"], changes))
        notes["外窗"] = note
        want["GlazingType"] = f"$NEW{len(copies)}"
    # ASHRAE 基準地坪是 F 係數構造, 熱損失按外露周長算; 沒有外露周長的熱區(貼地的核心區)DB 會拒絕計算。
    # 這些熱區改用同一組材料層的一般構造(表面熱阻取範本的一般地坪), 其他熱區維持 F 係數
    slab = cons.get(want.get("CombinedGroundFloorConstr", ""), {})
    slab_layered = None
    if slab.get("DefinitionMethod", "").startswith("6"):
        ref = cons.get(tmpl.get("GroundSlabConstr", ""), {})
        copies.append(("Constructions", slab["Id"], {"DefinitionMethod": "1-Material layers", "FFactor": "0",
                                    "SRO": ref.get("SRO") or "0.04", "SRI": ref.get("SRI") or "0.17",
                                    "Name": f"{slab['Name']} - layers for interior zones (DbMcp)"}))
        slab_layered = f"$NEW{len(copies)}"

    dsb = _working_copy(dsb_path)
    main = [a for _, attrs in dblibrary.ENVELOPE_PARTS for a in attrs] + ["GlazingType"]
    keys = sorted(want)
    job = [("bdump", ",".join(keys)), ("dump", ",".join(main)), ("table", "Constructions", "+10000"),
           ("table", "Glazing", "+10000")]
    if apply:
        job += [("copy", tbl, base, *[f"{k}={v}" for k, v in ch.items()]) for tbl, base, ch in copies]
        job += [("bset", k, v) for k, v in want.items()]
        if slab_layered:
            job.append(("groundfix", slab_layered))
        job += [("bdump", ",".join(keys)), ("dump", ",".join(main))]
        for tbl in ("Constructions", "Glazing"):
            new = [f"$NEW{i + 1}" for i, c in enumerate(copies) if c[0] == tbl]
            if new:
                job.append(("table", tbl, ",".join(new)))
        job.append(("save",))
    rows, secs = _bridge(dsb, job)
    before = (_buildings(rows) or [{}])[0]
    first_add = next((i for i, r in enumerate(rows) if r[0] == "added"), len(rows))
    model_cons = _records(rows[:first_add], "Constructions")  # 寫入前模型自己的構造
    new_ids = [r[2] for r in rows if r[0] == "added"]

    lines = [f"## 套用構造{'(已寫入)' if apply else '(預覽,尚未寫入)'}:{dsb.stem}", "",
             f"基準:**{tmpl['Name']}**(DB 構造範本 #{tmpl['Id']},{tmpl.get('Source', '')})", "",
             "| 部位 | 目前 | U | 改成 | U | 備註 |", "|---|---|---:|---|---:|---|"]
    for part, attrs in dblibrary.ENVELOPE_PARTS:
        cur_name, cur_u = _cons_label(before.get(attrs[0], ""), model_cons)
        base = tmpl[attrs[0]]
        new_name, new_u = _cons_label(base, {})
        if part in notes and part in target_u:
            new_name, new_u = f"{new_name} -> U {target_u[part]:g}", float(target_u[part])
        lines.append(f"| {part} | {cur_name} | {_u(cur_u)} | {new_name} | {_u(new_u)} | {notes.get(part, '')} |")
    cur_g = (_records(rows, "Glazing").get(before.get("GlazingType", "")) or
             dblibrary.table("Glazing").get(before.get("GlazingType", "")) or {})
    g_name, g_u = glazing["Name"], float(glazing["U-Value"])
    if "外窗" in notes:
        g_name, g_u = f"{g_name} -> {notes['外窗']}", target_u.get("外窗", g_u)
    lines.append(f"| 外窗玻璃 | {cur_g.get('Name', '#' + before.get('GlazingType', ''))} | {cur_g.get('U-Value', '')} | "
                 f"{g_name} | {g_u:.3f} | SHGC {window_shgc if window_shgc is not None else glazing['TransSolar']} |")
    internal = [k for k in want if k.endswith("Constr") and k not in main]
    lines += [f"| 滲風 | {before.get('InfiltrationValue', '')} ach | | {tmpl.get('InfiltrationValue', '')} ach | | |", "",
              f"U 值依材料層與 DB 的表面熱阻計算(W/m²K);地坪以 F 係數表示,不算 U。外窗用 2019 基準 {dblibrary.GLAZING_BRACKET} "
              f"一檔:窗牆比 10% 以上各檔數值相同,只有 0–10% 在 CZ5–6 的日射透過率不同。"
              f"另有隔間、樓板、天花等 {len(internal)} 項內部或次要構造照範本一起寫入。"]
    if slab_layered:
        lines.append("地坪的 F 係數按外露周長計算熱損失;貼地但沒有外露周長的熱區(例如核心區)DB 無法計算,"
                     "這些熱區會改用同一組材料層的一般構造,寫入時自動判斷。")
    if not apply:
        lines.append("確認無誤後,用同樣參數加上 apply=True 寫入。")
        return "\n".join(lines)

    resolved = {k: (new_ids[int(v[4:]) - 1] if str(v).startswith("$NEW") and int(v[4:]) <= len(new_ids) else v)
                for k, v in want.items()}
    after_b = (_buildings(rows) or [{}])[-1]
    after_z = (_zones(rows) or [[]])[-1]
    problems = [f"第 {r[1]} 行:{' '.join(r[2:])}" for r in rows if r[0] in ("lineerror", "norec")]
    if len(new_ids) != len(copies):
        problems.append(f"應新增 {len(copies)} 筆紀錄,實際 {len(new_ids)} 筆")
    model_glz = _records(rows[:first_add], "Glazing")
    for k, i in enumerate(new_ids):
        tbl = copies[k][0] if k < len(copies) else "Constructions"
        taken = (set(model_cons) | set(cons)) if tbl == "Constructions" else (set(model_glz) | set(dblibrary.table("Glazing")))
        same_tbl = [n for j, n in enumerate(new_ids) if j < len(copies) and copies[j][0] == tbl]
        if not i.isdigit() or int(i) < 10000 or i in taken or same_tbl.count(i) > 1:
            problems.append(f"新{tbl}的編號 {i} 不合理(應為 10000 以上、沒被用過);請勿使用這份模型,改用備份")
    mismatch = [f"建築 {k}:應為 {v},讀回 {after_b.get(k, '(沒讀到)')}" for k, v in resolved.items()
                if not _same(after_b.get(k, ""), v)]
    fixed = {r[1]: r[2] for r in rows if r[0] == "groundfix"}
    layered_id = new_ids[int(slab_layered[4:]) - 1] if slab_layered and int(slab_layered[4:]) <= len(new_ids) else None
    for zname, got in fixed.items():
        if not _same(got, layered_id or ""):
            problems.append(f"`{zname}` 地坪應改為 #{layered_id},讀回 {got}")
    overrides = [f"`{z['idf']}` {a}:{z['attrs'].get(a)}(應為 {resolved[a]})" for z in after_z for a in main
                 if a in resolved and not _same(z["attrs"].get(a, ""), resolved[a])
                 and not (a == "CombinedGroundFloorConstr" and z["zone"] in fixed)]
    new_u = []
    final_cons, final_glz = _records(rows, "Constructions"), _records(rows, "Glazing")
    for i, (tbl, base, ch) in enumerate(copies):
        nid = new_ids[i] if i < len(new_ids) else "?"
        if tbl == "Glazing":
            rec = final_glz.get(nid, {})
            new_u.append(f"#{nid}「{ch['Name']}」:模型裡讀回 U {rec.get('U-Value', '?')}、SHGC {rec.get('TransSolar', '?')}")
            continue
        rec = final_cons.get(nid)
        u = dblibrary.construction_u_row(rec)[0] if rec else None
        new_u.append(f"#{nid}「{ch['Name']}」:模型裡讀回算得 U {_u(u)}")
    saved = any(r[0] == "save" and r[1] == "True" for r in rows)
    lines += ["", f"### 寫入結果({secs} 秒)", "",
              f"- 建築層級寫入 {len(resolved)} 項屬性" + (f",新增 {len(new_ids)} 筆構造或玻璃" if copies else "") +
              f";存檔:{'成功' if saved else '**失敗**'}。",
              f"- 讀回核對:{'全部一致' if not mismatch else f'**{len(mismatch)} 項不一致**'}。"]
    lines += [f"  - {m}" for m in (problems + mismatch)[:20]]
    lines += [f"- {m}" for m in new_u]
    if slab_layered:
        lines.append(f"- 地坪:{len(fixed)} 個熱區沒有外露周長,改用材料層版本 #{layered_id}" +
                     (f"({'、'.join(sorted(fixed)[:10])})" if fixed else "") + ";其他貼地熱區維持 F 係數。")
    lines.append(f"- 熱區實際生效值:{'全部跟著建築層級' if not overrides else f'{len(overrides)} 項是熱區或區塊自己的設定,沒有改'}。")
    lines += [f"  - {m}" for m in overrides[:20]]
    lines.append(f"\n修改後的模型副本:`{dsb}`(用 `db_open` 打開檢查)")
    return "\n".join(lines)


def _hvac_info(b):
    """建築屬性(讀回)→ 換算燃料用的效率與燃料。"""
    def f(k):
        try:
            return float(b.get(k, ""))
        except ValueError:
            return None
    tmpl = dblibrary.table("HVACTemplates").get(b.get("HVACTemplateId", ""), {})
    return {"template_id": b.get("HVACTemplateId", ""), "template": tmpl.get("Name", ""),
            "heating_on": b.get("HeatingOn") == "1", "cooling_on": b.get("ActiveCoolingOn") == "1",
            "mech_vent_on": b.get("MechanicalVentilationOn") == "1", "nat_vent_on": b.get("NaturalVentilationOn") == "1",
            "heating_fuel": dblibrary.fuel(b.get("BoilerFuel")), "heating_scop": f("SCoPFrac"),
            "cooling_fuel": "電", "cooling_sseer": f("SSEERFrac"),
            "dhw_fuel": dblibrary.fuel(b.get("DHWFuelType"), "dhw"), "dhw_cop": f("DHWCoP")}


def assign_hvac(dsb_path, system=None, apply=False):
    """讀模型目前的空調設定; system 有給且 apply 時改成該範本。回傳 (說明文字, 生效的設定 dict)。"""
    tmpl = dblibrary.hvac_template(system) if system else None
    want = dict(dblibrary.hvac_attributes(tmpl)) if tmpl else {}
    if tmpl:
        want["HVACTemplateId"] = tmpl["Id"]
    keys = sorted(set(want) | set(dblibrary.HVAC_READ))
    dsb = _working_copy(dsb_path)
    job = [("bdump", ",".join(keys))]
    if apply and want:
        job += [("bset", k, v) for k, v in want.items()] + [("bdump", ",".join(keys)), ("save",)]
    rows, secs = _bridge(dsb, job)
    blds = _buildings(rows) or [{}]
    before, after = _hvac_info(blds[0]), _hvac_info(blds[-1])

    def desc(h):
        heat = (f"{h['heating_fuel']},季節效率 {h['heating_scop']:g}" if h["heating_on"] and h["heating_scop"]
                else "關")
        cool = f"電,季節效率 {h['cooling_sseer']:g}" if h["cooling_on"] and h["cooling_sseer"] else "關"
        vent = "機械新風" if h["mech_vent_on"] else ("自然通風" if h["nat_vent_on"] else "無")
        return (f"{h['template'] or '#' + h['template_id']}(#{h['template_id']}):供暖 {heat};供冷 {cool};"
                f"通風 {vent};熱水 {h['dhw_fuel']},效率 {h['dhw_cop'] if h['dhw_cop'] is not None else '?'}")

    lines = [f"## 空調系統{'(已寫入)' if apply and want else ''}:{dsb.stem}", "", f"- 目前:{desc(before)}"]
    if tmpl:
        lines.append(f"- 改成:DB 範本 #{tmpl['Id']}「{tmpl['Name']}」,寫入 {len(want)} 項建築屬性")
    if not (apply and want):
        if tmpl:
            lines.append("確認無誤後,用同樣參數加上 apply=True 寫入。")
        return "\n".join(lines), before
    mismatch = [f"{k}:應為 {v},讀回 {blds[-1].get(k, '(沒讀到)')}" for k, v in want.items()
                if not _same(blds[-1].get(k, ""), v)]
    problems = [f"第 {r[1]} 行:{' '.join(r[2:])}" for r in rows if r[0] in ("lineerror", "norec")]
    saved = any(r[0] == "save" and r[1] == "True" for r in rows)
    lines += ["", f"### 寫入結果({secs} 秒)", "", f"- 現在:{desc(after)}",
              f"- 存檔:{'成功' if saved else '**失敗**'};讀回核對:"
              f"{'全部一致' if not mismatch else f'**{len(mismatch)} 項不一致**'}。"]
    lines += [f"  - {m}" for m in (problems + mismatch)[:20]]
    return "\n".join(lines), after


@mcp.tool()
def db_assign_hvac(dsb_path: str, system: str = "", apply: bool = False) -> str:
    """看或換整棟建築的空調系統(DB 簡易空調範本)。EnergyPlus 以理想負載算冷熱需求, 用範本的季節效率換算成
    電與天然氣(和 DB 結果畫面同一算法); 換範本會一起改新風、自然通風、熱回收等設定, 冷熱需求也可能跟著變。

    Args:
        dsb_path: .dsb 的完整路徑(操作的是副本)
        system: 留空只讀目前設定; 或選: 風機盤管＋冷機＋燃氣鍋爐、VAV 中央空調＋冷機＋燃氣鍋爐、箱型機 Packaged DX(全電)、
            分離式冷氣＋新風(全電)、VRF 多聯機＋新風(全電)、PTHP 壁掛熱泵(全電,無新風)、暖氣片＋燃氣鍋爐(無冷氣,自然通風),
            也可以給 DB 空調範本的完整名稱
        apply: False(預設)只預覽; True 寫入、存檔並讀回核對
    """
    try:
        return assign_hvac(dsb_path, system or None, apply)[0]
    except KeyError as e:
        raise ValueError(str(e).strip("'\""))


# ---- 參數建模: 量體 → dsbXML → 匯入帶 DbMcpBridge 的模板 → 新的 .dsb ----

TEMPLATE_DSB = WORK / "_template" / "template.dsb"
TEMPLATE_NOTE = (f"需要一個裝好 DbMcpBridge 的模型當模板(任何模型都可以, 匯入時整個 Site 會被換掉, 只留腳本)。"
                 f"第一次請用 template_dsb 參數指定, 之後會存一份在 {TEMPLATE_DSB}。")


def _import_xml(xml: Path, out_dir: Path, name: str, template: Path):
    """用模板模型裡的 DbMcpBridge 匯入 dsbXML 並另存成 out_dir/name.dsb。
    匯入會換掉整個 Site, DB 接著計算會當掉, 所以腳本另存後直接結束 DB; DB 另存只寫出未壓縮的 .skh,
    .dsb 就是裝著 "~name.skh" 的 zip, 這裡自己打包。"""
    base = out_dir / "_import_base.dsb"
    shutil.copy2(template, base)
    res = _patch_bridge(base)
    if res:
        res[2].unlink(missing_ok=True)  # 暫用的副本, 不用留更新前的備份
    skh = out_dir / f"{name}.skh"
    skh.unlink(missing_ok=True)
    try:
        rows, secs = _bridge(base, [("importxml", str(xml)), ("saveas", name, str(out_dir) + "\\"), ("quit",)])
    finally:
        # DB 被腳本結束, 留下模板副本與它的鎖定檔
        for f in (base, out_dir / "~_import_base.dsb.lck"):
            f.unlink(missing_ok=True)
    if not skh.exists():
        raise RuntimeError("DB 沒有另存出模型(找不到 .skh)。DbMcpBridge 回應:" + " | ".join("\t".join(r) for r in rows))
    data = skh.read_bytes()
    if BRIDGE_MARKER not in data:
        raise RuntimeError("新模型裡沒有 DbMcpBridge(匯入時沒保留下來), 沒有產生 .dsb。")
    dsb = out_dir / f"{name}.dsb"
    if dsb.exists():
        shutil.copy2(dsb, out_dir / f"{name}_before_{datetime.now():%Y%m%d_%H%M%S}.dsb")
    tmp = dsb.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"~{name}.skh", data)
    tmp.replace(dsb)
    skh.unlink()
    return dsb, secs


@mcp.tool()
def db_build_model(params: dict, template_dsb: str = "", location: str = "") -> str:
    """由量體參數直接建出 DesignBuilder 模型(.dsb), 每層切周邊區與核心區、按樓層套用途, 並產生分區圖。
    建好的模型已裝 DbMcpBridge, 可以接著用 db_assign_construction / db_assign_schedule / db_simulate。

    Args:
        params: name(英文、數字、底線)與量體:
            shape: rect | L | U | T | courtyard | podium_tower
            rect: width, depth(m, x 向東、y 向北); L: 再加 notch_width, notch_depth(東北角挖掉);
            U: notch_width, notch_depth(北側中間開口); T: stem_width, bar_depth(北側橫翼);
            courtyard: court_width, court_depth(中央天井);
            podium_tower: podium {width, depth, stories, floor_height}, tower {width, depth, stories,
                floor_height, offset_x, offset_y}(塔樓預設置中, offset 從置中位置平移)
            其他: stories(預設 1), floor_height(預設 3.6), wwr 窗牆比(預設 0.4),
            wwr_north / wwr_east / wwr_south / wwr_west 各立面窗牆比(選填, 依轉向後的實際朝向),
            rotation 朝向(從正北順時針轉的角度, 預設 0), uses 各層用途 {"1": "大廳", "2-5": "辦公", "6-": "會議"}, default_use(預設 辦公), perimeter_depth(預設 4.5)
            用途只能用 db_zones 列出的中文用途; 數值都由 DB 範本庫帶入
        template_dsb: 裝好 DbMcpBridge 的模型(第一次需要, 之後沿用)
        location: 選填, 地點範本編號(db_find_locations 找); 不給時用匯入範本的地點(舊金山)。氣象檔要已在本機
    """
    uses = {**{"default": params.get("default_use", "辦公")}, **params.get("uses", {})}
    try:
        for use in uses.values():
            dblibrary.resolve_space(use)
    except KeyError as e:
        raise ValueError(str(e).strip("'\""))
    if template_dsb:
        src = _guard(template_dsb)
        if not src.exists():
            raise ValueError(f"找不到模板 {template_dsb}")
        if BRIDGE_MARKER not in zipfile.ZipFile(src).read(zipfile.ZipFile(src).infolist()[0]):
            raise ValueError("模板裡沒有 DbMcpBridge。" + BRIDGE_SETUP)
        TEMPLATE_DSB.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, TEMPLATE_DSB)
    if not TEMPLATE_DSB.exists():
        raise ValueError(TEMPLATE_NOTE)
    if location:  # 先檢查, 免得建了幾分鐘才發現地點不能用
        try:
            loc = dblibrary.resolve_location(location)
        except KeyError as e:
            raise ValueError(str(e).strip("'\""))
        if not dblibrary.weather_of(loc)[1]:
            raise ValueError(f"地點 {loc['LocationName']} 的氣象檔不在本機;先建模型不給 location,"
                             f"再用 db_set_location 搭配 epw_path 設定")
    _check_idle()
    name = str(params.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError(f"name 只能用英文、數字、底線、減號, 收到 {name!r}")
    out_dir = WORK / name
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / "params.json"
    pj.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    r = subprocess.run(stdin=subprocess.DEVNULL, args=[str(MODELER_PY), str(HERE / "massing.py"), str(pj), str(out_dir)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip().splitlines()[-1])
    info = json.loads((out_dir / f"{name}_zones.json").read_text(encoding="utf-8"))
    dsb, secs = _import_xml(Path(info["xml"]), out_dir, name, TEMPLATE_DSB)

    by_floor = {}
    for z in info["zones"]:
        by_floor.setdefault(z["block"], z["use"])
    report = db_assign_activity(str(dsb), by_floor, apply=True)
    if location:
        report += "\n\n---\n\n" + db_set_location(str(dsb), location, apply=True)
    rows, _ = _bridge(dsb, [("dump", "ActivityTemplateId")])
    zones = (_zones(rows) or [[]])[-1]
    area_db, area_plan = sum(z["area"] for z in zones), info["floor_m2"]
    names_missing = sorted({z["name"] for z in info["zones"]} - {z["zone"] for z in zones})
    ok = len(zones) == len(info["zones"]) and not names_missing
    lines = [f"## 建好模型:{name}", "",
             f"- 量體 {params.get('shape', 'rect')},{info['floors']} 層,{len(info['zones'])} 個熱區,"
             f"樓地板 {area_plan:,.0f} m²;" + (f"每層 {info['perimeter_offset']:g} m 周邊區加核心區。" if info["perimeter_offset"]
                                             else "翼寬太窄,每層一區。"),
             f"- 匯入 DB {secs} 秒;DB 讀回 {len(zones)} 個熱區,名稱{'與量體一致' if ok else '**與量體不一致**'};"
             f"DB 的淨樓地板 {area_db:,.0f} m²(扣掉牆厚,比外輪廓面積 {area_plan:,.0f} m² 小是正常的)。"]
    if names_missing:
        lines.append(f"- DB 裡找不到的熱區名稱:{'、'.join(names_missing[:10])}")
    lines += ["- 各層用途:" + "、".join(f"{b} {u}" for b, u in by_floor.items()), "",
              f"模型:`{dsb}`", f"分區圖:`{info['plans']}`", "",
              "下一步可用 `db_assign_construction`(圍護結構)、`db_assign_schedule`(作息)、`db_simulate`(模擬);"
              "細部修改請用 `db_open` 在 DB 打開。", "", "---", "", report]
    return "\n".join(lines)


@mcp.tool()
def db_create_model(params: dict) -> str:
    """由建築參數建一個 DesignBuilder 模型(dsbXML), 參數格式同 tools/modeler/example.json:
    name、width、depth 必填(m); 可選 shape(rect / L, L 要 notch_width、notch_depth)、stories、floor_height、
    wwr、building_type、climate_zone、construction、construction_vintage、hvac。
    產生的 XML 讓使用者在 DB 用 File > Import > Import model from XML 匯入(或用 db_open 打開)。
    """
    name = str(params.get("name", ""))
    out_dir = WORK / (re.sub(r"[^A-Za-z0-9_-]+", "_", name) or "model")
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / "params.json"
    pj.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    r = subprocess.run(stdin=subprocess.DEVNULL, args=[str(MODELER_PY), str(HERE / "params_to_dsbxml.py"), str(pj), str(out_dir / f"{name}.xml")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip().splitlines()[-1])
    return r.stdout.strip().splitlines()[-1]


@mcp.tool()
def db_open(path: str) -> str:
    """在 DesignBuilder 裡打開 .dsb(或 dsbXML)給使用者看。不等它關閉。"""
    _check_idle()
    if not Path(path).exists():
        raise ValueError(f"找不到 {path}")
    _launch(f'"{path}"')
    return "已在 DesignBuilder 打開"


# ---- 背景工作: 開 DB 的工具要 1–5 分鐘, 比 Dify 等工具回應的時間長; 先回工作編號, 再查進度 ----
# DB 一次只能開一個, 所以用一個工作執行緒依序跑; 結果另存 JSON, 伺服器重開後仍查得到

JOB_DIR = WORK / "_jobs"
LONG_TOOLS = ("db_workflow", "db_build_model", "db_simulate", "db_assign_activity", "db_assign_schedule", "db_assign_construction",
              "db_assign_hvac", "db_set_location", "db_zones", "db_inspect", "db_calculate", "db_update_bridge")
_job_queue: "queue.Queue[str]" = queue.Queue()
_job_lock = threading.Lock()
_job_worker = None


def _job_file(job_id):
    return JOB_DIR / f"{job_id}.json"


def _job_save(job):
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _job_file(job["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_job_file(job["id"]))


def _job_run():
    while True:
        job_id = _job_queue.get()
        job = json.loads(_job_file(job_id).read_text(encoding="utf-8"))
        job.update(status="running", started=datetime.now().isoformat(timespec="seconds"))
        _job_save(job)
        try:
            job["result"] = globals()[job["tool"]](**job["arguments"])
            job["status"] = "done"
        except Exception as e:  # 工具的錯誤訊息原樣交給使用者(例如 DB 停在對話框、參數錯誤)
            job.update(status="failed", error=f"{type(e).__name__}: {e}")
        job["finished"] = datetime.now().isoformat(timespec="seconds")
        _job_save(job)


@mcp.tool()
def db_workflow(form: dict) -> dict:
    """完整流程(Dify 表單工作流用, 請經 db_job_start 在背景跑): 新建模型或分析既有模型 → 地點與氣象檔 →
    ASHRAE 2019 基準構造 → 年度模擬 → 節能措施與回本, 回傳結構化結果。新建約 6–8 分鐘, 加節能措施再 5–10 分鐘。

    Args:
        form: 表單欄位(字串): mode(新建模型 / 分析既有模型)、location(地點編號或關鍵字)、run_measures(是 / 否)、
            building_type(非住宅 / 住宅 / 半空調);新建: name, shape(rect / L / U / T / courtyard / podium_tower), width,
            depth, stories, floor_height, wwr, uses(「1:大廳; 2-:辦公」), default_use, 形狀需要的 notch_width / notch_depth /
            stem_width / bar_depth / court_width / court_depth, 裙樓加塔樓另給 tower_width / tower_depth / tower_stories;
            既有: dsb_path, apply_constructions(是 / 否)
    """
    import workflow  # 這裡才載入: workflow 也 import server
    return workflow.run(form)


@mcp.tool()
def db_job_start(tool: str, arguments: dict) -> dict:
    """在背景執行一個要開 DesignBuilder 的工具, 立刻回傳工作編號; 用 db_job_status 查進度與結果。
    Dify 等會限制工具等待時間的環境, 請用這個代替直接呼叫下列工具(每個要 1–5 分鐘):
    db_build_model、db_simulate、db_assign_activity、db_assign_schedule、db_assign_construction、
    db_set_location、db_zones、db_inspect、db_calculate、db_update_bridge。

    Args:
        tool: 工具名稱(上面其中之一)
        arguments: 該工具的參數, 和直接呼叫時一樣, 例如 {"dsb_path": "...", "hourly": true}
    """
    global _job_worker
    if tool not in LONG_TOOLS:
        raise ValueError(f"db_job_start 只接受:{'、'.join(LONG_TOOLS)};其他工具請直接呼叫")
    job_id = f"{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"
    job = {"id": job_id, "tool": tool, "arguments": arguments, "status": "queued",
           "queued": datetime.now().isoformat(timespec="seconds")}
    _job_save(job)
    with _job_lock:
        if _job_worker is None or not _job_worker.is_alive():
            _job_worker = threading.Thread(target=_job_run, daemon=True, name="db-jobs")
            _job_worker.start()
    _job_queue.put(job_id)
    return {"job_id": job_id, "status": "queued", "ahead": _job_queue.qsize() - 1,
            "note": "用 db_job_status 查進度;通常 1–5 分鐘"}


@mcp.tool()
def db_job_status(job_id: str) -> dict:
    """查背景工作的狀態: queued(排隊中)/ running(執行中)/ done(完成, result 是工具的輸出)/
    failed(失敗, error 是原因)。"""
    if not re.fullmatch(r"[0-9_a-f]+", job_id) or not _job_file(job_id).exists():
        raise ValueError(f"找不到工作 {job_id}")
    job = json.loads(_job_file(job_id).read_text(encoding="utf-8"))
    if job["status"] == "running":
        job["elapsed_s"] = round((datetime.now() - datetime.fromisoformat(job["started"])).total_seconds())
    if job["status"] in ("queued", "running"):
        job["note"] = "還沒完成,稍後再查"
    return job


if __name__ == "__main__":
    mcp.run(transport="stdio")
