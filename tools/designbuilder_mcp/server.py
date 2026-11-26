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
import re
import shutil
import site
import subprocess
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
EP_OUTPUTS = ("in.idf", "eplusout.err", "eplustbl.htm", "eplusout.eso")

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


def _working_copy(dsb_path: str) -> Path:
    src = Path(dsb_path)
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
                timeout_s: int = 1800) -> str:
    """用 DesignBuilder 本身跑 EnergyPlus 年度模擬(在模型副本上, 不改原檔), 收集 in.idf / eplustbl.htm 等結果。

    Args:
        dsb_path: .dsb 的完整路徑
        attributes: 模擬前要改的建築層屬性, 例如 {"InfiltrationValue": 0.35}; 名稱用 db_inspect 查。
            數值用 SI 單位; 布林用 1/0; 資料表選項用數字 Id。
        start_date / end_date: "日/月", 例如 "1/1"、"31/12"; 留空用模型原本的設定
        timeout_s: 最多等幾秒
    Returns: 輸出資料夾、EnergyPlus 是否成功、總能耗與 EUI。要算節能措施與回本, 接著呼叫 db_measures_report。
    """
    dsb = _working_copy(dsb_path)
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
    d = Path(output_dir)
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
    d = Path(output_dir)
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
             "ActiveCoolingDesignSetPointTemperature", "MinFreshAir", "OccupancySchedule")


def _bridge(dsb: Path, lines, timeout_s=900):
    """把工作清單交給模型裡的 DbMcpBridge 執行。DB 只在開始計算時載入腳本, 所以順便跑一次暖房設計(約 1 分鐘)。
    回傳 (輸出各行的欄位清單, 秒數)。"""
    job = dsb.parent / f"_job_{datetime.now():%Y%m%d_%H%M%S}.txt"
    job.write_text("\n".join("\t".join(map(str, ln)) for ln in lines) + "\n", encoding="ascii")
    os.environ["DBMCP_JOB"] = str(job)
    try:
        secs, _ = _run_chain(dsb, ProcessChain().switch_screen(Screen.HEATING_DESIGN).run(), timeout_s)
    finally:
        os.environ.pop("DBMCP_JOB", None)
    out = Path(str(job) + ".out")
    if not out.exists():
        raise RuntimeError(BRIDGE_SETUP)
    rows = [ln.split("\t") for ln in out.read_text(encoding="utf-8", errors="replace").splitlines() if ln]
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
            cur.append({"block": r[2], "zone": r[3], "idf": r[4], "area": float(r[5]),
                        "attrs": dict(c.split("=", 1) for c in r[6:] if "=" in c)})
        else:
            cur = None
    return dumps


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
        plan_uses = {sel: dblibrary.resolve_space(use) for sel, use in assignments.items()}
    except KeyError as e:
        raise ValueError(str(e).strip("'\""))
    rows, secs = _bridge(dsb, [("dump", ",".join(ZONE_KEYS))])
    zones = (_zones(rows) or [[]])[-1]
    targets = {}
    for sel, tpl in plan_uses.items():
        hit = _resolve(zones, sel)
        if not hit:
            raise ValueError(f"找不到熱區或區塊「{sel}」;可用 db_zones 查看名稱")
        for z in hit:
            targets[z["idf"]] = (z, tpl)
    lines = [f"## 套用用途{'(已寫入)' if apply else '(預覽,尚未寫入)'}:{dsb.stem}", "",
             "| 熱區 | 目前用途 | 改成 | 人員 人/m² | 暖 / 冷設定 °C | 新風 L/s·人 | 在室作息 |", "|---|---|---|---|---|---|---|"]
    for idf, (z, tpl) in targets.items():
        a, s = z["attrs"], dblibrary.activity_summary(tpl)
        sch = s["occupancy_hours"]
        lines.append(f"| `{idf}` | {dblibrary.activity_label(a.get('ActivityTemplateId', ''))} | "
                     f"{dblibrary.activity_label(tpl['Id'])} | {a.get('OccupancyValue', '')} → {tpl.get('OccupancyValue', '')} | "
                     f"{a.get('HeatingDesignSetPointTemperature', '')}/{a.get('ActiveCoolingDesignSetPointTemperature', '')} → "
                     f"{tpl.get('HeatingDesignSetPointTemperature', '')}/{tpl.get('ActiveCoolingDesignSetPointTemperature', '')} | "
                     f"{a.get('MinFreshAir', '')} → {tpl.get('MinFreshAir', '')} | "
                     f"`{dblibrary.spark(sch['平日']) if sch else '—'}` |")
    lines += ["", f"數值來源:本機 DB 範本庫(活動範本編號見「改成」欄),每個熱區寫入範本編號加 "
                  f"{len(dblibrary.ACTIVITY_ATTRIBUTES)} 項以內的屬性(範本裡空白的不寫)。"]
    if not apply:
        lines.append("確認無誤後,用同樣參數加上 apply=True 寫入。")
        return "\n".join(lines)

    job, want = [], {}
    for idf, (z, tpl) in targets.items():
        attrs = [("ActivityTemplateId", tpl["Id"])] + dblibrary.activity_attributes(tpl)
        want[idf] = dict(attrs)
        job += [("set", idf, k, v) for k, v in attrs]
    keys = sorted({k for w in want.values() for k in w})
    job += [("dump", ",".join(keys)), ("save",)]
    rows, secs2 = _bridge(dsb, job)
    after = {z["idf"]: z["attrs"] for z in (_zones(rows) or [[]])[-1]}
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
