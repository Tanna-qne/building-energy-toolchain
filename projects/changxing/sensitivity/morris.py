"""Morris 全域敏感度分析(Morris 1991; μ* 依 Campolongo et al. 2007)。

    uv run --with eppy python projects/changxing/sensitivity/morris.py

14 個不參與校準的物理參數, 4 個水準、r = 10 條軌跡, 共 r × (k + 1) = 150 次全年模擬。
校準量(營業排程、分攤負荷、站房支路)固定為定版值, 因此結果回答兩個問題:
1. 哪些文獻參數對全年電量、7 月電量、峰值影響最大(哪些值得花力氣去現場核實);
2. 這些參數偏離文獻值時, 校準吻合度(CV(RMSE))會壞到什麼程度, 即校準對它們有多敏感。
"""
import csv
import json
import random
import sys

from common import BASELINE, HERE, NAMES, PARAMS, Model, ancillary7, metrics, reference, run_many

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LEVELS = 4
DELTA = LEVELS / (2 * (LEVELS - 1))  # 2/3, Morris 建議值
R = 10
SEED = 20261122
OUTPUTS = ["annual_MWh", "july_MWh", "peak_kW", "heating_MWh", "cooling_MWh", "cvrmse_pct", "summer_ratio", "winter_ratio"]


def trajectory(rng, k):
    """單位超立方體中的一條 Morris 軌跡: k+1 個點, 每步只改一個因子 ±Δ。"""
    grid = [i / (LEVELS - 1) for i in range(LEVELS)]
    x = [rng.choice([g for g in grid if g + DELTA <= 1 + 1e-9]) for _ in range(k)]
    order = list(range(k))
    rng.shuffle(order)
    pts, steps = [list(x)], []
    for j in order:
        x = list(x)
        x[j] += DELTA
        pts.append(x)
        steps.append(j)
    return pts, steps


def to_params(u):
    return {name: lo + ui * (hi - lo) for (name, _, lo, hi, _), ui in zip(PARAMS, u)}


def main():
    rng = random.Random(SEED)
    k = len(PARAMS)
    trajs = [trajectory(rng, k) for _ in range(R)]
    work = HERE / "_runs"
    work.mkdir(exist_ok=True)
    model = Model()
    paths, samples = [], []
    for t, (pts, _) in enumerate(trajs):
        for s, u in enumerate(pts):
            p = to_params(u)
            path = work / f"t{t:02d}_s{s:02d}.idf"
            model.save(p, path)
            paths.append(path)
            samples.append(p)
    base_path = work / "baseline.idf"
    model.save(dict(BASELINE), base_path)
    paths.append(base_path)
    print(f"{len(paths)} 次模擬, {k} 個參數", flush=True)

    ref, anc7 = reference(), ancillary7()
    res = [metrics(r, ref, anc7) for r in run_many(paths, work)]
    base = res.pop()

    with open(HERE / "morris_samples.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(NAMES + OUTPUTS)
        for p, m in zip(samples, res):
            w.writerow([round(p[n], 4) for n in NAMES] + [round(m[o], 4) for o in OUTPUTS])

    ee = {o: {n: [] for n in NAMES} for o in OUTPUTS}
    i = 0
    for pts, steps in trajs:
        block = res[i:i + k + 1]
        i += k + 1
        for s, j in enumerate(steps):
            for o in OUTPUTS:
                ee[o][NAMES[j]].append((block[s + 1][o] - block[s][o]) / DELTA)  # 單位: 該輸出 / 參數全範圍的 Δ 比例

    summary = {"baseline": {o: base[o] for o in OUTPUTS}, "levels": LEVELS, "delta": DELTA, "r": R, "seed": SEED, "indices": {}}
    rows = []
    for o in OUTPUTS:
        summary["indices"][o] = {}
        for n, label, lo, hi, _ in PARAMS:
            e = ee[o][n]
            mu = sum(e) / R
            mu_star = sum(abs(x) for x in e) / R
            sigma = (sum((x - mu) ** 2 for x in e) / (R - 1)) ** 0.5
            summary["indices"][o][n] = {"mu": mu, "mu_star": mu_star, "sigma": sigma}
            rows.append([o, n, label, lo, hi, round(mu, 4), round(mu_star, 4), round(sigma, 4)])
    with open(HERE / "morris_indices.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["output", "param", "label", "low", "high", "mu", "mu_star", "sigma"])
        w.writerows(rows)
    (HERE / "morris_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for o in ("annual_MWh", "july_MWh", "cvrmse_pct"):
        top = sorted(summary["indices"][o].items(), key=lambda kv: -kv[1]["mu_star"])[:5]
        print(o, [(n, round(v["mu_star"], 2), round(v["sigma"], 2)) for n, v in top], flush=True)
    print("baseline", {o: round(base[o], 3) for o in OUTPUTS})


if __name__ == "__main__":
    main()
