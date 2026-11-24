"""把 DesignBuilder 自帶的 EnergyPlus 引擎整理成一個標準的 EnergyPlus 資料夾 → tools/dbengine/engine/。

    python tools/dbengine/setup_engine.py

DesignBuilder 只附 energyplusapi.dll(沒有 energyplus.exe、ReadVarsESO), 所以:
1. 從 DB 複製引擎: 先找 %LOCALAPPDATA%\\DesignBuilder\\EnergyPlus(DB 跑過一次模擬就會有),
   沒有就解壓 C:\\ProgramData\\DesignBuilder\\JobServer\\versions\\DLL*.zip(取最新版);
2. 用 Windows 內建的 C# 編譯器把 energyplus_shim.cs 編成 engine/energyplus.exe(呼叫那個 DLL, 並代替 ReadVarsESO 輸出 CSV);
3. 把 DB 氣象庫的 EPW 複製到 engine/WeatherData/, Chicago 另存成 EnergyPlus 官方檔名(工具鏈的預設氣象)。
之後把環境變數 ENERGYPLUS_DIR 指到 engine/(安裝程式會做), eplus_paths.py 與 EnergyPlus-MCP 就會用它。
不下載任何東西; 只用本機已裝的 DesignBuilder。
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "engine"
DB_EP = Path(os.environ.get("LOCALAPPDATA", "")) / "DesignBuilder" / "EnergyPlus"
PROGRAMDATA = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "DesignBuilder"
ENGINE_FILES = ("energyplusapi.dll", "Energy+.idd", "ExpandObjects.exe", "msvcp140.dll", "vcruntime140.dll",
                "vcruntime140_1.dll", "python38.dll")
CHICAGO = "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"


def csc():
    root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64"
    found = sorted(root.glob("v4*/csc.exe"))
    if not found:
        raise SystemExit("找不到 Windows 內建的 C# 編譯器(.NET Framework 4 的 csc.exe)")
    return found[-1]


def copy_engine():
    if (DB_EP / "energyplusapi.dll").exists():
        for f in ENGINE_FILES:
            if (DB_EP / f).exists():
                shutil.copy2(DB_EP / f, ENGINE / f)
        return f"DesignBuilder 的引擎資料夾 {DB_EP}"
    zips = sorted((PROGRAMDATA / "JobServer" / "versions").glob("DLL*.zip"))
    if not zips:
        raise SystemExit("找不到 DesignBuilder 的 EnergyPlus: 請確認已安裝 DesignBuilder, 或在 DB 裡跑過一次模擬")
    with zipfile.ZipFile(zips[-1]) as z:
        for n in z.namelist():
            if Path(n).name in ENGINE_FILES:
                (ENGINE / Path(n).name).write_bytes(z.read(n))
    return f"DesignBuilder 的引擎壓縮檔 {zips[-1].name}"


def copy_weather():
    wd = ENGINE / "WeatherData"
    wd.mkdir(exist_ok=True)
    n = 0
    for epw in (PROGRAMDATA / "Weather Data").glob("*.epw"):
        name = CHICAGO if "CHICAGO-OHARE" in epw.name.upper() else epw.name.replace(" ", "_")
        shutil.copy2(epw, wd / name)
        n += 1
    return n


def version():
    r = subprocess.run([str(ENGINE / "energyplus.exe"), "--version"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    m = re.search(r"Version\s+([\d.]+)", r.stdout + r.stderr)
    return m.group(1) if m else (r.stdout + r.stderr).strip()


def main():
    if os.name != "nt":
        raise SystemExit("DesignBuilder 只有 Windows 版")
    ENGINE.mkdir(exist_ok=True)
    src = copy_engine()
    r = subprocess.run([str(csc()), "/nologo", "/platform:x64", "/optimize", f"/out:{ENGINE / 'energyplus.exe'}",
                        str(HERE / "energyplus_shim.cs")], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("編譯 energyplus.exe 失敗:\n" + r.stdout + r.stderr)
    n = copy_weather()
    print(f"引擎來源: {src}")
    print(f"已建立 {ENGINE}(EnergyPlus {version()}),氣象檔 {n} 個")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
