"""DesignBuilder MCP server: 讓 agent 透過 DB 官方的命令列(/process=)操作 DesignBuilder。

只是把現成專案接起來, 不自己寫模擬或建模邏輯:
- db-process(DesignBuilder 官方): /process= 指令鏈與找 DesignBuilder.exe
- 匯出的 dsbXML 用標準 XML 讀(格式見 DesignBuilder 官方 db-dsbxml-schema)
- honeybee-designbuilder(Ladybug Tools): 參數 -> DB 模型(params_to_dsbxml.py)
- 節能措施報告沿用工作區的 tools/designbuilder/from_db.py

DB 一次只能開一個; 工具在 DB 開著時會拒絕執行, 不會關掉使用者的視窗。
操作一律用模型的副本(db_models/dbmcp/<模型名>/), 不改使用者原本的 .dsb。

    C:/Users/kiri/AppData/Local/Programs/Python/Python312/python.exe tools/designbuilder_mcp/server.py

用 venv 的「基底」Python 執行, 不要用 .venv/Scripts/python.exe: 在 venv 啟動器底下開的子程序裡,
E+ 的 Transition(版本升級)會當掉(HANDOVER 坑 18), 節能報告就跑不出來。套件從 .venv 載入。
"""
import json
import os
import re
import shutil
import site
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
site.addsitedir(str(HERE / ".venv" / "Lib" / "site-packages"))  # addsitedir 會處理 .pth(pywin32 需要)

from db_process import ProcessChain, Screen, find_designbuilder, is_running, kill_process  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

WS = HERE.parents[1]
WORK = WS / "db_models" / "dbmcp"
DB_EP_DIR = Path(os.environ["LOCALAPPDATA"]) / "DesignBuilder" / "EnergyPlus"
MAIN_PY = Path(r"C:\Users\kiri\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe")
MODELER_PY = WS / "tools" / "modeler" / ".venv" / "Scripts" / "python.exe"
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
        kill_process()
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


def _run_chain(dsb: Path, chain: ProcessChain, timeout_s: int, finished=None):
    """跑一串 /process= 指令。finished(): 結果檔出現就回傳 True(DB 不一定會自己關, 不能只等它結束)。
    沒給 finished 時等 DB 結束。回傳秒數。"""
    _check_idle()
    start = time.time()
    _launch(f'"{dsb}" {chain.to_string()}')
    seen = False  # 提權後 DB 要一陣子才出現在行程列表; 看到它跑過, 「不在了」才代表它結束
    while time.time() - start < timeout_s:
        if finished and finished():
            time.sleep(5)  # 讓 DB 把檔案寫完
            break
        running = is_running()
        seen = seen or running
        if seen and not running:
            break
        time.sleep(5)
    else:
        raise RuntimeError(f"DesignBuilder 超過 {timeout_s} 秒還沒做完(可能停在對話框); 請使用者看一下 DB 畫面")
    closed = _close_db()
    return round(time.time() - start), closed


CLOSE_NOTE = ("DesignBuilder 做完後沒有自己關掉, 而且它以系統管理員身分執行, 這邊關不掉: "
              "請使用者關閉 DB 視窗(不用存檔), 下一個 DB 工具才能執行。")


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


def _site_energy(htm: Path):
    """eplustbl.htm 的 Total Site Energy 與面積(DB 報表常是英制, 一律換成 kWh / m²)。"""
    text = htm.read_text(encoding="latin-1")
    m = re.search(r"Total Site Energy</td>\s*<td[^>]*>\s*([\d.,]+)", text)
    unit = re.search(r"<td align=\"right\">Total Energy \[(\w+)\]", text)
    area = re.search(r"Total Building Area</td>\s*<td[^>]*>\s*([\d.,]+)", text)
    area_unit = re.search(r"Area \[(m2|ft2)\]", text)
    if not m:
        return None
    energy = float(m.group(1).replace(",", ""))
    u = unit.group(1) if unit else "GJ"
    kwh = energy * {"GJ": 277.7778, "kBtu": 0.293071, "MWh": 1000, "kWh": 1}.get(u, 277.7778)
    out = {"site_energy_kwh": round(kwh)}
    if area:
        a = float(area.group(1).replace(",", ""))
        a = a * 0.09290304 if area_unit and area_unit.group(1) == "ft2" else a
        out.update(floor_m2=round(a, 1), eui_kwh_m2=round(kwh / a, 1) if a else None)
    return out


@mcp.tool()
def db_status() -> str:
    """DesignBuilder 有沒有在執行、執行檔在哪裡。其他工具都需要 DB 沒開著。"""
    return json.dumps({"running": is_running(), "exe": str(find_designbuilder())}, ensure_ascii=False)


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
    out = dsb.parent / datetime.now().strftime("sim_%Y%m%d_%H%M%S")
    out.mkdir()
    # in.epw 是 DB 從氣象庫複製的, 保留原檔的舊時間, 不能用時間判斷; 只要這次有新的 in.idf 就一起收
    fresh = [f for f in EP_OUTPUTS if (DB_EP_DIR / f).exists() and (DB_EP_DIR / f).stat().st_mtime >= started - 5]
    if "in.idf" in fresh and (DB_EP_DIR / "in.epw").exists():
        fresh.append("in.epw")
    for f in fresh:
        shutil.copyfile(DB_EP_DIR / f, out / f)
    err = (out / "eplusout.err").read_text(encoding="latin-1") if (out / "eplusout.err").exists() else ""
    ok = "EnergyPlus Completed Successfully" in err
    result = {"output_dir": str(out), "success": ok, "seconds": secs, "files": fresh,
              "err_summary": err.strip().splitlines()[-1] if err.strip() else "沒有產生 eplusout.err(模擬沒有執行)"}
    if not closed:
        result["note"] = CLOSE_NOTE
    if ok and (out / "eplustbl.htm").exists():
        result.update(_site_energy(out / "eplustbl.htm") or {})
    return json.dumps(result, ensure_ascii=False)


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
    return json.dumps({"report": str(d / "report.html"), "log": str(d / "run.log")}, ensure_ascii=False)


@mcp.tool()
def db_report_status(output_dir: str) -> str:
    """查 db_measures_report 的進度。完成時回傳每個措施的省能 %、年省金額與回本年數。"""
    d = Path(output_dir)
    log = (d / "run.log").read_text(encoding="utf-8", errors="replace") if (d / "run.log").exists() else ""
    if "完成" not in log:
        failed = "Traceback" in log
        return json.dumps({"done": failed, "failed": failed, "log_tail": log.strip().splitlines()[-5:]},
                          ensure_ascii=False)
    import csv
    with open(d / "results.csv", encoding="utf-8-sig") as f:
        rows = [{k: r[k] for k in ("case", "success", "saving_pct", "saving_cost", "capex_cost", "payback_years",
                                   "currency", "error")} for r in csv.DictReader(f)]
    return json.dumps({"done": True, "report": str(d / "report.html"), "measures": rows}, ensure_ascii=False)


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
    return json.dumps({"model_copy": str(dsb), "seconds": secs, **({} if closed else {"note": CLOSE_NOTE})},
                      ensure_ascii=False)


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
