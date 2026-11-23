"""不確定性區間: 14 個文獻參數的拉丁超立方抽樣, 每個樣本各自重新校準, 傳播到全年逐時序列。

    uv run --with eppy python projects/changxing/sensitivity/uncertainty.py

1. 在 Morris 的同一組參數範圍內(均勻分佈, 範圍見 common.PARAMS)拉丁超立方抽 N 個樣本;
2. 第 0 輪: 校準量(營業強度、分攤負荷、站房支路 5)取定版值, 結果即「不重新校準」的先驗區間;
3. 每個樣本用與定版相同的修正公式(hvac_vs/calibrate_vs.py)重調三個校準量, 最多 ROUNDS 輪,
   最大鐘點偏差 ≤ TOL_KW 即停, 結果即「各自校準到原表」後仍剩下的區間;
4. 輸出各樣本的指標、全年 8,760 小時與 7 月光伏消納指標的 5 / 50 / 95 百分位。

需要本機的長興原表(calibration_24h.csv, 不公開); 輸出只含彙總後的區間。
"""
import csv
import json
import random
import sys

from common import HERE, NAMES, PARAMS, ROOT, Model, ancillary7, metrics, reference, run_many

sys.path.insert(0, str(ROOT / "winter_heating"))
import calibrate as cal  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
N = 100
SEED = 20261123
ROUNDS = 3
TOL_KW = 1.0
QS = (5, 50, 95)


def lhs(rng, n, k):
    cols = []
    for _ in range(k):
        c = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(c)
        cols.append(c)
    return [[cols[j][i] for j in range(k)] for i in range(n)]


def pct(xs, q):
    s = sorted(xs)
    x = (len(s) - 1) * q / 100
    i = int(x)
    return s[i] if i + 1 >= len(s) else s[i] + (s[i + 1] - s[i]) * (x - i)


def july_pv(building, base_building, rows):
    """7 月: 總負荷 = 定版總負荷 − 定版建築 + 樣本建築, 再與光伏比較(同 ecm.py)。"""
    load = [float(r["load_kWh"]) - b0 + b for r, b0, b in zip(rows, base_building, building)]
    pv = [float(r["pv_kWh"]) for r in rows]
    net = [a - b for a, b in zip(load, pv)]
    surplus = [-x for x in net if x < 0]
    return {"surplus_hours": len(surplus), "surplus_MWh": sum(surplus) / 1000,
            "pv_self_use_pct": 100 * (sum(pv) - sum(surplus)) / sum(pv), "peak_net_kW": max(net)}


def main():
    rng = random.Random(SEED)
    samples = [{n: lo + u * (hi - lo) for (n, _, lo, hi, _), u in zip(PARAMS, row)}
               for row in lhs(rng, N, len(PARAMS))]
    model = Model()
    i = model.idf
    ops, buses = cal.compact_hours(i, "op_level"), cal.compact_hours(i, "eq_bus")
    op0, bus0, anc0 = cal.read_values(ops), cal.read_values(buses), cal.anc5_values(i)
    state = [{"op": list(op0), "bus": list(bus0), "anc5": list(anc0), "done": False, "log": []} for _ in samples]
    for s, p in zip(state, samples):
        model.apply(p)
        s["d_op"], s["d_bus"] = cal.op_design_kw(i), cal.bus_design_kw(i)
    tgt, anc7 = reference(), ancillary7()
    with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as f:
        hourly = list(csv.DictReader(f))
    base_station = [float(r["station_total_kWh"]) for r in hourly]
    july = [k for k, r in enumerate(hourly) if r["interval_start"][5:7] == "07"]
    with open(ROOT / "pv_2025" / "july_load_pv.csv", encoding="utf-8") as f:
        pv_rows = list(csv.DictReader(f))
    work = HERE / "_runs" / "uq"
    work.mkdir(parents=True, exist_ok=True)

    results = [None] * N
    prior = [None] * N
    for rnd in range(ROUNDS + 1):
        todo = [k for k, s in enumerate(state) if not s["done"]]
        if not todo:
            break
        paths = []
        for k in todo:
            s = state[k]
            model.apply(samples[k])
            cal.write_values(ops, s["op"])
            cal.write_values(buses, s["bus"])
            cal.write_anc5(i, s["anc5"])
            path = work / f"s{k:03d}.idf"
            i.saveas(str(path))
            paths.append(path)
        sims = run_many(paths, work)
        for k, res in zip(todo, sims):
            s = state[k]
            met = metrics(res, tgt, anc7)
            err = [m - t for m, t in zip(met.pop("hourly_mean"), tgt)]
            worst = max(abs(e) for e in err)
            met["worst_kW"] = worst
            met["fans_MWh"] = sum(res["Fans:Electricity"]) / 1000
            station = res["Electricity:Facility"]
            met.update(july_pv([station[h] for h in july], [base_station[h] for h in july], pv_rows))
            if rnd == 0:
                prior[k] = (met, station)
            results[k] = (met, station)
            s["log"].append(round(worst, 3))
            if worst <= TOL_KW or rnd == ROUNDS:
                s["done"] = True
                continue
            for h in range(24):
                x = s["op"][h] - err[h] / s["d_op"] + s["bus"][h] * s["d_bus"] / s["d_op"]
                s["op"][h] = min(max(x, 0.0), 1.0)
                s["bus"][h] = max(x - 1.0, 0.0) * s["d_op"] / s["d_bus"]
                if x < 0:
                    s["anc5"][h] = min(max(s["anc5"][h] + x * s["d_op"] / cal.ANC5_KW, 0.0), 1.0)
        print(f"第 {rnd} 輪: {len(todo)} 個樣本; 未達 {TOL_KW} kW: "
              f"{sum(1 for s in state if s['log'][-1] > TOL_KW)}", flush=True)

    keys = ["annual_MWh", "july_MWh", "peak_kW", "heating_MWh", "cooling_MWh", "fans_MWh", "summer_ratio",
            "winter_ratio", "cvrmse_pct", "worst_kW", "surplus_hours", "surplus_MWh", "pv_self_use_pct", "peak_net_kW"]
    with open(HERE / "uncertainty_samples.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample"] + NAMES + [f"prior_{c}" for c in keys] + [f"cal_{c}" for c in keys] + ["rounds"])
        for k in range(N):
            w.writerow([k] + [round(samples[k][n], 4) for n in NAMES] + [round(prior[k][0][c], 4) for c in keys]
                       + [round(results[k][0][c], 4) for c in keys] + [len(state[k]["log"])])

    def band(which):
        return {c: {str(q): pct([m[0][c] for m in which], q) for q in QS} for c in keys}

    summary = {"n": N, "seed": SEED, "rounds_max": ROUNDS, "tol_kW": TOL_KW,
               "params": [{"name": n, "label": lab, "low": lo, "high": hi} for n, lab, lo, hi, _ in PARAMS],
               "calibrated_within_tol": sum(1 for s in state if s["log"][-1] <= TOL_KW),
               "worst_kW_max": max(s["log"][-1] for s in state),
               "prior": band(prior), "calibrated": band(results)}
    (HERE / "uncertainty_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    with open(HERE / "uncertainty_hourly_bands.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["interval_start", "station_total_kWh"] + [f"p{q}_kWh" for q in QS]
                   + [f"prior_p{q}_kWh" for q in QS])
        for h, r in enumerate(hourly):
            cal_h = [res[1][h] for res in results]
            pri_h = [res[1][h] for res in prior]
            w.writerow([r["interval_start"], f"{base_station[h]:.3f}"] + [f"{pct(cal_h, q):.3f}" for q in QS]
                       + [f"{pct(pri_h, q):.3f}" for q in QS])
    for tag in ("prior", "calibrated"):
        print(tag, {c: [round(v, 2) for v in summary[tag][c].values()] for c in keys})
    print("校準到", TOL_KW, "kW 內:", summary["calibrated_within_tol"], "/", N, "最大偏差", round(summary["worst_kW_max"], 2))


if __name__ == "__main__":
    main()
