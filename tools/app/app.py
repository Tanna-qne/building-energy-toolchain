"""建築能耗工具: 選模型(或填幾個建築參數)→ 按「開始計算」→ 自動模擬、跑 8 種節能措施、打開報告。

由「建築能耗.bat」啟動(安裝.bat 會產生它)。可以把 .dsb / .idf 檔拖到「建築能耗.bat」上直接打開。
只用標準函式庫(tkinter); 計算交給 run_building.py、db_import.py、tools/designbuilder_mcp/cli.py,
引擎一律用 DesignBuilder 自帶的 EnergyPlus(tools/dbengine/engine)。
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

WS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WS))
from building_report import BUILDING_NAMES  # noqa: E402

LOCAL = json.loads((WS / "toolchain.local.json").read_text(encoding="utf-8")) if (WS / "toolchain.local.json").exists() else {}
PY = LOCAL.get("python") or sys.executable.replace("pythonw.exe", "python.exe")
ENGINE = Path(LOCAL.get("energyplus_dir") or WS / "tools" / "dbengine" / "engine")
TYPES = ["MediumOffice", "SmallOffice", "LargeOffice", "Retail", "StripMall", "SmallHotel", "PrimarySchool",
         "SecondarySchool", "Hospital", "Outpatient", "QuickServiceRestaurant", "FullServiceRestaurant", "Warehouse"]
HVACS = {"中央空調(VAV,鍋爐熱水)": "VAV_Chiller_Boiler", "箱型冷氣 + 瓦斯加熱": "PSZAC_GasCoil",
         "窗型 / 分離式冷氣 + 電暖器": "PTAC_ElectricBaseboard"}
FIELDS = [("name", "名稱(英文、數字、底線)", "my_building"), ("width", "寬 m", "40"), ("depth", "深 m", "25"),
          ("stories", "層數", "3"), ("floor_height", "樓高 m", "3.6"), ("wwr", "窗牆比(0–1)", "0.4")]


class App(tk.Tk):
    def __init__(self, preset=None):
        super().__init__()
        self.title("建築能耗工具")
        self.geometry("860x720")
        self.minsize(720, 560)
        self.q = queue.Queue()
        self.proc = None
        self.report = None
        self.option_add("*Font", ("Microsoft JhengHei UI", 10))
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        src = ttk.LabelFrame(root, text="1. 建築從哪裡來", padding=10)
        src.pack(fill="x")
        self.mode = tk.StringVar(value="file" if preset else "params")
        ttk.Radiobutton(src, text="我有模型檔(DesignBuilder 的 .dsb,或匯出的 .idf)", variable=self.mode, value="file",
                        command=self.toggle).grid(row=0, column=0, columnspan=3, sticky="w")
        self.file = tk.StringVar(value=preset or "")
        self.file_entry = ttk.Entry(src, textvariable=self.file, width=70)
        self.file_entry.grid(row=1, column=0, columnspan=2, sticky="we", padx=(22, 6), pady=4)
        self.file_btn = ttk.Button(src, text="選擇檔案…", command=self.pick)
        self.file_btn.grid(row=1, column=2, sticky="w")
        ttk.Radiobutton(src, text="用幾個參數建一棟", variable=self.mode, value="params",
                        command=self.toggle).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.params = ttk.Frame(src)
        self.params.grid(row=3, column=0, columnspan=3, sticky="we", padx=(22, 0))
        self.vars = {}
        for i, (key, label, default) in enumerate(FIELDS):
            ttk.Label(self.params, text=label).grid(row=i // 3 * 2, column=i % 3, sticky="w", padx=4)
            v = tk.StringVar(value=default)
            ttk.Entry(self.params, textvariable=v, width=22).grid(row=i // 3 * 2 + 1, column=i % 3, sticky="w", padx=4, pady=(0, 6))
            self.vars[key] = v
        ttk.Label(self.params, text="用途").grid(row=4, column=0, sticky="w", padx=4)
        self.btype = ttk.Combobox(self.params, state="readonly", width=20,
                                  values=[f"{BUILDING_NAMES.get(t, t)}({t})" for t in TYPES])
        self.btype.current(0)
        self.btype.grid(row=5, column=0, sticky="w", padx=4)
        ttk.Label(self.params, text="空調").grid(row=4, column=1, sticky="w", padx=4)
        self.hvac = ttk.Combobox(self.params, state="readonly", width=30, values=list(HVACS))
        self.hvac.current(0)
        self.hvac.grid(row=5, column=1, columnspan=2, sticky="w", padx=4)
        src.columnconfigure(0, weight=1)

        wx = ttk.LabelFrame(root, text="2. 氣象檔(DesignBuilder 的模型沿用 DB 裡設定的氣象,這裡不影響它)", padding=10)
        wx.pack(fill="x", pady=10)
        self.epws = {p.stem: p for p in sorted((ENGINE / "WeatherData").glob("*.epw"))}
        self.epw = ttk.Combobox(wx, state="readonly", width=60, values=list(self.epws))
        chicago = [i for i, n in enumerate(self.epws) if "Chicago" in n]
        if self.epws:
            self.epw.current(chicago[0] if chicago else 0)
        self.epw.pack(side="left")
        ttk.Button(wx, text="其他氣象檔…", command=self.pick_epw).pack(side="left", padx=8)

        bar = ttk.Frame(root)
        bar.pack(fill="x")
        self.go = ttk.Button(bar, text="3. 開始計算", command=self.start)
        self.go.pack(side="left")
        self.open_btn = ttk.Button(bar, text="打開報告", command=self.open_report, state="disabled")
        self.open_btn.pack(side="left", padx=8)
        self.status = ttk.Label(bar, text="準備好了。一棟建築約 3–10 分鐘。")
        self.status.pack(side="left", padx=8)

        self.log = tk.Text(root, height=18, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(10, 0))
        self.toggle()
        if not (ENGINE / "energyplus.exe").exists():
            self.write("還沒安裝好:找不到 DesignBuilder 的引擎。請先執行「安裝.bat」。\n")
            self.go.state(["disabled"])
        self.after(200, self.drain)

    def toggle(self):
        file_mode = self.mode.get() == "file"
        for w in (self.file_entry, self.file_btn):
            w.state(["!disabled"] if file_mode else ["disabled"])
        for w in self.params.winfo_children():
            if isinstance(w, (ttk.Entry, ttk.Combobox)):
                w.state(["!disabled", "readonly"] if isinstance(w, ttk.Combobox) and not file_mode else
                        ["!disabled"] if not file_mode else ["disabled"])

    def pick(self):
        f = filedialog.askopenfilename(filetypes=[("建築模型", "*.dsb *.idf"), ("所有檔案", "*.*")])
        if f:
            self.file.set(f)
            self.mode.set("file")
            self.toggle()

    def pick_epw(self):
        f = filedialog.askopenfilename(filetypes=[("EnergyPlus 氣象檔", "*.epw")])
        if f:
            p = Path(f)
            self.epws[p.stem] = p
            self.epw["values"] = list(self.epws)
            self.epw.set(p.stem)

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def command(self):
        """回傳 (命令, 報告路徑); 參數有誤時拋 ValueError。"""
        if self.mode.get() == "file":
            f = Path(self.file.get().strip('" '))
            if not f.is_file():
                raise ValueError("請先選擇模型檔")
            if f.suffix.lower() == ".dsb":
                return [PY, WS / "tools" / "designbuilder_mcp" / "cli.py", f], None
            if f.suffix.lower() == ".idf":
                return [PY, WS / "db_import.py", f], WS / "db_models" / "out" / "report.html"
            raise ValueError("只支援 .dsb 或 .idf")
        p = {}
        for key, label, _ in FIELDS:
            raw = self.vars[key].get().strip()
            if key == "name":
                p[key] = raw
                continue
            try:
                p[key] = int(raw) if key == "stories" else float(raw)
            except ValueError:
                raise ValueError(f"「{label}」要填數字")
        p["building_type"] = re.search(r"\((\w+)\)$", self.btype.get()).group(1)
        p["hvac"] = HVACS[self.hvac.get()]
        d = WS / "db_models" / "app"
        d.mkdir(parents=True, exist_ok=True)
        pj = d / f"{p['name'] or 'building'}.json"
        pj.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        return [PY, WS / "run_building.py", pj], WS / "db_models" / "out" / "report.html"

    def start(self):
        try:
            cmd, self.report = self.command()
        except ValueError as e:
            messagebox.showwarning("建築能耗工具", str(e))
            return
        env = dict(os.environ, ENERGYPLUS_DIR=str(ENGINE), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        if self.epw.get() in self.epws:
            env["EPW"] = str(self.epws[self.epw.get()])
        self.go.state(["disabled"])
        self.open_btn.state(["disabled"])
        self.status.configure(text="計算中…(視窗可以縮小,算完會自動打開報告)")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        def work():
            try:
                self.proc = subprocess.Popen([str(c) for c in cmd], cwd=WS, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                             encoding="utf-8", errors="replace",
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                for line in self.proc.stdout:
                    self.q.put(("line", line))
                self.q.put(("done", self.proc.wait()))
            except OSError as e:
                self.q.put(("line", f"無法啟動:{e}\n"))
                self.q.put(("done", 1))

        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        try:
            while True:
                kind, value = self.q.get_nowait()
                if kind == "line":
                    self.write(value)
                    m = re.search(r"報告:(.+report\.html)", value)
                    if m:
                        self.report = Path(m.group(1).strip())
                else:
                    self.go.state(["!disabled"])
                    if value == 0 and self.report and self.report.exists():
                        self.status.configure(text="完成。")
                        self.open_btn.state(["!disabled"])
                        self.open_report()
                    else:
                        self.status.configure(text="沒有完成,原因在下方紀錄的最後幾行。")
        except queue.Empty:
            pass
        self.after(200, self.drain)

    def open_report(self):
        if self.report and self.report.exists():
            os.startfile(self.report)


if __name__ == "__main__":
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
