"""一鍵安裝: 在一台裝了 DesignBuilder 的 Windows 電腦上, 把整套工具準備好。由根目錄的「安裝.bat」執行。

    uv run --no-project --python 3.12 python tools/setup/install.py [--yes] [--no-register]

做的事(可重跑, 已完成的步驟會略過):
1. 檢查 DesignBuilder; 用它自帶的 EnergyPlus 整理出 tools/dbengine/engine(不另外下載 EnergyPlus);
2. 建模環境 tools/modeler/.venv231(Python 3.10 + OpenStudio 3.6.1, 對應 DB 的 EnergyPlus 23.1);
3. EnergyPlus-MCP: 取得 LBNL 原始碼(固定版本)→ 套用本機修正 → 建環境;
4. DesignBuilder MCP 的環境 tools/designbuilder_mcp/.venv;
5. 寫 toolchain.local.json(本機路徑), 並詢問要不要把兩個 MCP server 註冊到 Claude Desktop、Claude Code、DeepSeek Harness;
6. 最後自我檢查。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
ENGINE = WS / "tools" / "dbengine" / "engine"
MODELER = WS / "tools" / "modeler"
VENDOR = WS / "vendor" / "EnergyPlus-MCP"
EPMCP = VENDOR / "energyplus-mcp-server"
EPMCP_COMMIT = "5a7d3bb1d2e537ba329d3412c8b79d22cedd7c70"
DBMCP = WS / "tools" / "designbuilder_mcp"
LOCAL = WS / "toolchain.local.json"
# Ladybug Tools 會在 HOME / APPDATA 底下寫檔; 和 build_idf.py 一樣指到工作區內
MODELER_ENV = dict(os.environ, HOME=str(MODELER / "home"), APPDATA=str(MODELER / "home"))
DB_EXE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "DesignBuilder" / "DesignBuilder.exe"
ARGS = set(sys.argv[1:])


def step(title):
    print(f"\n== {title}", flush=True)


def run(cmd, **kw):
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL, **kw)
    if r.returncode:
        raise SystemExit(f"\n失敗(代碼 {r.returncode}):{' '.join(str(c) for c in cmd)}")
    return r


def ask(question):
    if "--yes" in ARGS:
        return True
    try:
        return input(f"   {question} [Y/n] ").strip().lower() in ("", "y", "yes", "是")
    except EOFError:
        return False


def uv():
    exe = shutil.which("uv")
    if not exe:
        raise SystemExit("找不到 uv。請先依 https://docs.astral.sh/uv/getting-started/installation/ 安裝, 再重新執行安裝.bat")
    return exe


def python_path(version):
    r = subprocess.run([uv(), "python", "find", version], capture_output=True, text=True)
    if r.returncode:
        run([uv(), "python", "install", version])
        r = subprocess.run([uv(), "python", "find", version], capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


def venv_python(d: Path):
    return d / "Scripts" / "python.exe"


def engine():
    step("1/5 DesignBuilder 與它自帶的 EnergyPlus")
    if not DB_EXE.exists():
        print(f"   沒找到 DesignBuilder({DB_EXE})。引擎可以從 DB 的資料夾取得, 若也找不到會在下一步失敗。")
    if (ENGINE / "energyplus.exe").exists() and (ENGINE / "energyplusapi.dll").exists():
        print("   已存在, 略過")
        return
    run([sys.executable, WS / "tools" / "dbengine" / "setup_engine.py"])


def modeler():
    step("2/5 建模環境(OpenStudio 3.6.1, Python 3.10)")
    env = MODELER / ".venv231"
    py = venv_python(env)
    if py.exists() and subprocess.run([py, "-c", "import openstudio, honeybee_openstudio"], capture_output=True,
                                      env=MODELER_ENV).returncode == 0:
        print("   已存在, 略過")
        return
    run([uv(), "venv", env, "--python", "3.10", "--allow-existing"])
    run([uv(), "pip", "install", "--python", py, "-r", MODELER / "requirements-231.txt"])
    run([uv(), "pip", "install", "--python", py, "--no-deps", "honeybee-openstudio==0.10.0", "openstudio==3.6.1",
         "openstudio-backporter==0.2.0"])


def energyplus_mcp():
    step("3/5 EnergyPlus-MCP(LBNL)")
    if not (EPMCP / "pyproject.toml").exists():
        if not shutil.which("git"):
            raise SystemExit("需要 git 才能取得 EnergyPlus-MCP 原始碼; 請先安裝 Git for Windows")
        run(["git", "clone", "https://github.com/LBNL-ETA/EnergyPlus-MCP.git", VENDOR])
        run(["git", "-C", VENDOR, "checkout", EPMCP_COMMIT])
        run(["git", "-C", VENDOR, "apply", "--whitespace=nowarn", WS / "tools" / "energyplus_mcp" / "energyplus-mcp.patch"])
    else:
        print("   原始碼已存在(不重新套用修正)")
    py = venv_python(EPMCP / ".venv")
    if not py.exists():
        run([uv(), "sync", "--extra", "dev"], cwd=EPMCP)
    run([py, EPMCP / "apply_windows_patches.py"], cwd=EPMCP)  # uv sync 會蓋掉 .venv 裡的 eppy 修正, 每次都補


def designbuilder_mcp():
    step("4/5 DesignBuilder MCP")
    env = DBMCP / ".venv"
    if venv_python(env).exists():
        print("   已存在, 略過")
        return
    run([uv(), "venv", env, "--python", "3.12", "--allow-existing"])
    run([uv(), "pip", "install", "--python", venv_python(env), "-r", DBMCP / "requirements.txt"])


def servers(base_py: Path):
    """兩個 MCP server 的啟動設定(Claude Desktop / Claude Code 的 mcpServers 格式)。"""
    idd = ENGINE / "Energy+.idd"
    return {
        "energyplus": {"command": str(venv_python(EPMCP / ".venv")), "args": ["-m", "energyplus_mcp_server.server"],
                       "cwd": str(EPMCP), "env": {"EPLUS_IDD_PATH": str(idd)}},
        # DB MCP 要用基底 Python(見 server.py 開頭); 節能報告的子程序用 DB 的引擎
        "designbuilder": {"command": str(base_py), "args": [str(DBMCP / "server.py")], "cwd": str(WS),
                          "env": {"ENERGYPLUS_DIR": str(ENGINE), "REPORT_PYTHON": str(base_py)}},
    }


def merge_json(path: Path, entries, key="mcpServers"):
    data = {}
    if path.exists() and path.read_text(encoding="utf-8-sig").strip():
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{time.strftime('%Y%m%d%H%M%S')}"))
    data.setdefault(key, {}).update(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def dsh_block(entries):
    out = ["", "# 由 tools/setup/install.py 加入", "- insert:"]
    for name, s in entries.items():
        out += [f"    - id: mcp-{name}", '      name: "@deepseek-ai/dsh-mcp-client"', "      config:",
                f"        serverName: {name}", "        transport: stdio",
                f"        command: {json.dumps(s['command'].replace(chr(92), '/'))}",
                f"        args: {json.dumps([a.replace(chr(92), '/') for a in s['args']], ensure_ascii=False)}",
                f"        cwd: {json.dumps(s['cwd'].replace(chr(92), '/'))}", "        env:"]
        out += [f"          {k}: {json.dumps(v.replace(chr(92), '/'))}" for k, v in s["env"].items()]
    return "\n".join(out) + "\n"


def register(entries):
    step("5/5 註冊 MCP server")
    if "--no-register" in ARGS:
        print("   依參數略過註冊。設定內容:")
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    desktop = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    if desktop.parent.exists() and ask(f"加到 Claude Desktop({desktop})?"):
        merge_json(desktop, entries)
        print("   已加入(原檔已備份)。重開 Claude Desktop 後生效。")
    if ask(f"寫 Claude Code 的專案設定 {WS / '.mcp.json'}?"):
        merge_json(WS / ".mcp.json", {k: {kk: vv for kk, vv in v.items() if kk != "cwd"} for k, v in entries.items()})
        print("   已寫入。在這個資料夾開 Claude Code 就會看到。")
    patch = Path.home() / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    if patch.exists():
        text = patch.read_text(encoding="utf-8")
        new = {k: v for k, v in entries.items() if f"serverName: {k}" not in text}
        for k in entries:
            if k not in new:
                print(f"   DeepSeek Harness 已經有 {k},不修改(要改用 DB 的引擎,把它的 EPLUS_IDD_PATH 改成 {ENGINE / 'Energy+.idd'})")
        if new and ask(f"加到 DeepSeek Harness({patch})?"):
            shutil.copy2(patch, patch.with_suffix(f".yml.bak-{time.strftime('%Y%m%d%H%M%S')}"))
            patch.write_text(text.rstrip("\n") + "\n" + dsh_block(new), encoding="utf-8")
            print("   已加入(原檔已備份)。")


def check(base_py: Path):
    step("自我檢查")
    ok = True

    def item(label, cmd, **kw):
        nonlocal ok
        kw["env"] = dict(kw.get("env") or os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, **kw)
        good = r.returncode == 0
        ok &= good
        tail = (r.stdout + r.stderr).strip().splitlines()
        print(f"   [{'OK' if good else '失敗'}] {label}" + (f":{tail[-1]}" if tail else ""))

    item("EnergyPlus(DB 自帶)", [ENGINE / "energyplus.exe", "--version"])
    item("建模環境", [venv_python(MODELER / ".venv231"), "-c",
                   "import openstudio, honeybee_openstudio; print('OpenStudio', openstudio.openStudioVersion())"], env=MODELER_ENV)
    item("EnergyPlus-MCP", [venv_python(EPMCP / ".venv"), "-c", "import energyplus_mcp_server.server; print('可載入')"], cwd=EPMCP)
    item("DesignBuilder MCP", [base_py, "-c", "import sys; sys.argv=['x']; sys.path.insert(0, r'%s'); import server; "
                               "print(server.db_status().splitlines()[-1].strip())" % DBMCP], cwd=DBMCP)
    print("\n全部完成。雙擊「建築能耗.bat」開始使用。" if ok else "\n有項目失敗, 請把上面的訊息給維護的人看。")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name != "nt":
        raise SystemExit("這個安裝程式只支援 Windows(DesignBuilder 只有 Windows 版)")
    print(f"安裝到 {WS}")
    base_py = python_path("3.12")
    engine()
    modeler()
    energyplus_mcp()
    designbuilder_mcp()
    LOCAL.write_text(json.dumps({"python": str(base_py), "energyplus_dir": str(ENGINE),
                                 "modeler_python": str(venv_python(MODELER / ".venv231"))},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    launcher = WS / "建築能耗.bat"  # 只放 ASCII 內容, 不受 cmd 字碼頁影響; 可以把 .dsb / .idf 拖到它上面
    launcher.write_text(f'@echo off\r\nstart "" "{base_py.with_name("pythonw.exe")}" "{WS / "tools" / "app" / "app.py"}" %*\r\n',
                        encoding="ascii")
    print(f"\n已寫入 {LOCAL.name}、{launcher.name}")
    register(servers(base_py))
    check(base_py)


if __name__ == "__main__":
    main()
