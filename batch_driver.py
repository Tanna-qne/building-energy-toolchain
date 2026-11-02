"""用 MCP server 批量跑 EnergyPlus 範例模型, 匯總成功率與能耗。

驅動方式: 直接對 MCP server 說 JSON-RPC (stdio), 不是另寫一套模擬邏輯。
"""
import csv
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE / "vendor/EnergyPlus-MCP/energyplus-mcp-server"
EP = Path(r"C:/Users/kiri/AppData/Local/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64")
# 預設 Chicago; 設環境變數 EPW=<.epw 路徑> 可換氣象檔(只影響模擬, 參數建模的設計日仍是 Chicago)
WEATHER = Path(os.environ.get("EPW") or EP / "WeatherData" / "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw")
MODELS = SERVER_DIR / "sample_files/batch"
OUT = HERE / "batch"
LOGS = HERE / "logs"
J_PER_KWH = 3.6e6
_ids = itertools.count(1)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class Mcp:
    def __init__(self):
        env = dict(os.environ, EPLUS_IDD_PATH=str(EP / "Energy+.idd"), PYTHONUNBUFFERED="1")
        LOGS.mkdir(exist_ok=True)
        # 每個 server 一個 log, 平行跑時才分得清; 崩潰時從這裡取最後幾行
        self.log_path = LOGS / f"mcp-{next(_ids)}.log"
        self.log = open(self.log_path, "w", encoding="utf-8")
        self.p = subprocess.Popen(
            [str(SERVER_DIR / ".venv/Scripts/python.exe"), "-u", "-m", "energyplus_mcp_server.server"],
            cwd=str(SERVER_DIR), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, text=True, encoding="utf-8", bufsize=1, env=env)
        self.replies = {}
        self.n = 0
        threading.Thread(target=self._read, daemon=True).start()
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "batch", "version": "1"}})
        self.p.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.p.stdin.flush()

    def _read(self):
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                self.replies[msg["id"]] = msg

    def call(self, method, params=None, timeout=600):
        self.n += 1
        rid = self.n
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                       "params": params or {}}) + "\n")
        self.p.stdin.flush()
        end = time.time() + timeout
        while time.time() < end:
            if rid in self.replies:
                return self.replies.pop(rid)
            if self.p.poll() is not None:
                raise RuntimeError(f"MCP server 已結束 (exit {self.p.returncode}), {method} 沒有回應:\n"
                                   + self._log_tail())
            time.sleep(0.2)
        raise TimeoutError(f"{method} 超過 {timeout}s\n" + self._log_tail())

    def _log_tail(self, n=15):
        self.log.flush()
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])

    def tool(self, name, args, timeout=600):
        r = self.call("tools/call", {"name": name, "arguments": args}, timeout)
        return r.get("result", {}).get("content", [{}])[0].get("text", "")

    def close(self):
        try:
            self.p.stdin.close()
            self.p.terminate()
        except Exception:
            pass
        self.log.close()


def sim_args(idf: Path):
    """run_energyplus_simulation 的參數。只在有 HVACTemplate / GroundHeatTransfer 時才跑 ExpandObjects:
    MCP 預設一律展開, 但 26.2 的 ExpandObjects 用舊欄位定義, 遇到新版物件(如 40 欄的 Chiller:Electric:EIR)會失敗。"""
    text = idf.read_text(encoding="latin-1")
    expand = bool(re.search(r"^\s*(HVACTemplate|GroundHeatTransfer):", text, re.MULTILINE | re.IGNORECASE))
    return {"idf_path": str(idf.resolve()), "weather_file": str(WEATHER), "expandobjects": expand}


def meter_kwh(output_dir: Path, keywords):
    """從輸出目錄的 *Meter.csv 加總指定電表 (J -> kWh)。"""
    for csv_path in sorted(output_dir.glob("*Meter.csv")):
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if len(rows) < 2:
            continue
        header = rows[0]
        col = next((i for i, h in enumerate(header) if keywords in h), None)
        if col is None:
            continue
        total = 0.0
        for row in rows[1:]:
            if col < len(row):
                try:
                    total += float(row[col])
                except ValueError:
                    pass
        return round(total / J_PER_KWH, 1)
    return None


def main():
    out = OUT
    out.mkdir(exist_ok=True)
    if not any(MODELS.glob("*.idf")):
        raise SystemExit(f"{MODELS} 裡沒有 .idf")
    (out / "models").mkdir(exist_ok=True)
    client = Mcp()
    rows = []
    for src in sorted(MODELS.glob("*.idf")):
        # 有些範例模型沒有輸出電表, 模擬成功卻讀不到用電; 先用 MCP 補上, 補失敗就跑原檔
        idf = out / "models" / src.name
        idf.unlink(missing_ok=True)
        r = client.tool("add_output_meters", {"idf_path": str(src), "validation_level": "lenient",
                                              "meters": [["Electricity:Facility", "RunPeriod"]],
                                              "output_path": str(idf)}, 120)
        try:
            ok = json.loads(r.split("\n", 1)[1]).get("success")
        except (IndexError, json.JSONDecodeError):
            ok = False
        if not (ok and idf.exists()):
            idf = src
        started = time.time()
        text = client.tool("run_energyplus_simulation", sim_args(idf), 900)
        elapsed = round(time.time() - started, 1)
        try:
            payload = json.loads(text.split("\n", 1)[1])
        except (IndexError, json.JSONDecodeError):
            payload = {"success": False, "error": text[:200]}
        od = Path(payload.get("output_directory", "")) if payload.get("output_directory") else None
        elec = meter_kwh(od, "Electricity:Facility") if od and od.exists() else None
        rows.append({
            "model": src.stem,
            "success": payload.get("success"),
            "seconds": elapsed,
            "electricity_kwh": elec,
            "error": (payload.get("error") or "")[:120].replace("\n", " "),
        })
        print(f"{src.stem:55s} success={payload.get('success')} {elapsed:6.1f}s elec={elec}", flush=True)
    client.close()

    with open(out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    ok = sum(1 for r in rows if r["success"])
    lines = [f"# 批量模擬結果", "",
             f"- 模型數: {len(rows)}",
             f"- 成功: {ok} / {len(rows)}",
             f"- 總耗時: {sum(r['seconds'] for r in rows):.0f} 秒", "",
             "| 模型 | 成功 | 秒 | 總用電 kWh |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['model']} | {'OK' if r['success'] else 'FAIL'} | {r['seconds']} | {r['electricity_kwh'] or ('讀不到電表' if r['success'] else '')} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
