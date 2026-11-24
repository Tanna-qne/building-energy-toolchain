"""找 EnergyPlus 的目錄。所有腳本共用, 換電腦不必改程式。

順序: 環境變數 ENERGYPLUS_DIR → PATH 上的 energyplus → 各平台 26.2 的預設安裝位置
→ tools/dbengine/engine(由 DesignBuilder 自帶引擎整理出來的 23.1, 見 tools/dbengine/setup_engine.py)。
都找不到就報錯, 告訴使用者設 ENERGYPLUS_DIR。長興項目的模型是 26.2 版, 要用官方 26.2。
"""
import os
import shutil
import sys
from pathlib import Path

VERSION = "26-2-0"
EXE_NAME = "energyplus.exe" if os.name == "nt" else "energyplus"


def _candidates():
    env = os.environ.get("ENERGYPLUS_DIR")
    if env:
        yield Path(env)
        return
    exe = shutil.which("energyplus")
    if exe:
        yield Path(exe).resolve().parent
    if os.name == "nt":
        yield Path(f"C:/EnergyPlusV{VERSION}")
        local = os.environ.get("LOCALAPPDATA")
        if local:  # 解壓版: %LOCALAPPDATA%/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-<hash>-Windows-x86_64
            base = Path(local) / "Programs" / f"EnergyPlusV{VERSION}"
            yield base
            yield from sorted(base.glob("EnergyPlus-*"))
    elif sys.platform == "darwin":
        yield Path(f"/Applications/EnergyPlus-{VERSION}")
    else:
        yield Path(f"/usr/local/EnergyPlus-{VERSION}")
    yield Path(__file__).resolve().parent / "tools" / "dbengine" / "engine"


def energyplus_dir() -> Path:
    tried = []
    for d in _candidates():
        if (d / EXE_NAME).is_file() and (d / "Energy+.idd").is_file():
            return d
        tried.append(str(d))
    raise FileNotFoundError(
        "找不到 EnergyPlus。請把環境變數 ENERGYPLUS_DIR 設為安裝目錄(內含 "
        f"{EXE_NAME} 與 Energy+.idd)。已找過: " + "; ".join(tried))


def energyplus_exe() -> Path:
    return energyplus_dir() / EXE_NAME
