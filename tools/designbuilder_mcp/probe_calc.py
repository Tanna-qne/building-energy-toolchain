"""探索用: 跑一個 DB 計算, 每 10 秒記錄 DB 行程狀態與哪些檔案變了, 用來找完成訊號與結果位置。

    <基底 Python 3.12> tools/designbuilder_mcp/probe_calc.py <kind> <模型.dsb> [逾時秒數]

kind 同 db_calculate: heating_cooling_design / daylighting / daylighting_annual / cfd
結果寫在 db_models/dbmcp/_probe_<kind>.log。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from db_process import ProcessChain, Screen, is_running  # noqa: E402

CHAINS = {
    "heating_cooling_design": lambda: ProcessChain().switch_screen(Screen.HEATING_DESIGN).run()
                                                     .switch_screen(Screen.COOLING_DESIGN).run(),
    "daylighting": lambda: ProcessChain().switch_screen(Screen.DAYLIGHTING).run(),
    "daylighting_annual": lambda: ProcessChain().switch_screen(Screen.DAYLIGHTING).run().tab_change(2).run(),
    "cfd": lambda: ProcessChain().switch_screen(Screen.CFD).run(),
}
WATCH = [Path(os.environ["LOCALAPPDATA"]) / "DesignBuilder", Path(os.environ.get("TEMP", "")),
         Path.home() / "Documents" / "DesignBuilder Data"]


def snapshot(roots):
    out = {}
    for r in roots:
        for dp, _, fs in os.walk(r):
            for f in fs:
                p = os.path.join(dp, f)
                try:
                    out[p] = os.stat(p).st_mtime
                except OSError:
                    pass
    return out


def main():
    kind, src = sys.argv[1], sys.argv[2]
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 1800
    dsb = server._working_copy(src)
    roots = WATCH + [dsb.parent]
    log = open(dsb.parents[1] / f"_probe_{kind}.log", "w", encoding="utf-8")

    def say(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    server._check_idle()
    before = snapshot(roots)
    t0 = time.time()
    chain = CHAINS[kind]()
    say(f"啟動: {chain.to_string()}")
    server._launch(f'"{dsb}" {chain.to_string()}')
    seen, last = False, before
    while time.time() - t0 < timeout:
        time.sleep(10)
        running = is_running()
        seen = seen or running
        now = snapshot(roots)
        changed = [p for p, m in now.items() if p not in last or last[p] != m]
        say(f"DB {'執行中' if running else '沒在跑'};變動 {len(changed)} 個檔: "
            + "; ".join(sorted(changed, key=lambda p: now[p])[-8:]))
        last = now
        if seen and not running:
            say("DB 已結束")
            break
    after = snapshot(roots)
    new = sorted((p for p, m in after.items() if p not in before or before[p] != m), key=lambda p: after[p])
    say(f"總共 {len(new)} 個新增或修改的檔案(依時間):")
    for p in new:
        say(f"  {time.strftime('%H:%M:%S', time.localtime(after[p]))} {os.path.getsize(p):>10} {p}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
