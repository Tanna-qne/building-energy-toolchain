"""留出驗證(hold-out cross-validation)。

    uv run --with eppy python projects/changxing/sensitivity/holdout.py

定版校準每個鐘點各有一個營業強度, 24 個自由度對 24 個點, 留出的鐘點沒有任何資訊可用, 所以無法做留出檢驗。
這裡改用低自由度模型: 營業強度只按 7 個營業時段各取一個值(分攤負荷 eq_bus 取 0、站房支路 5 取設計值 1),
超過 1 的部分照定版做法轉給等功率的分攤負荷。讓留出的鐘點必須靠同時段的其他鐘點加上 EnergyPlus 的物理計算(空調隨鐘點的氣溫、日照變化)來預測。

步驟:
1. 兩次全年模擬(營業強度全 0、全 1)得到每個鐘點的線性代理: 均值(h) = A(h) + B(h)·θ(h);
2. 4 折交叉驗證: 第 f 折留出 h mod 4 == f 的 6 個鐘點, 以其餘 18 點在各時段內最小平方擬合 θ, 預測留出點;
3. 每折用擬合出的排程實際再跑一次 EnergyPlus, 以真實模擬(不是代理)計算留出點誤差;
4. 對照組: 不用物理模型, 以同時段訓練點的平均值預測留出點。
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "winter_heating"))
from calibrate import ANC5, bus_design_kw, compact_hours, op_design_kw, write_anc5, write_values  # noqa: E402
from common import HERE, Model, ancillary7, reference, run_many  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BLOCKS = [(0, 5, "夜間"), (5, 8, "清晨"), (8, 11, "上午"), (11, 14, "午餐"), (14, 17, "午後"), (17, 21, "晚間"), (21, 24, "深夜")]
BLOCK_OF = [next(b for b, (s, e, _) in enumerate(BLOCKS) if s <= h < e) for h in range(24)]
FOLDS = 4
THETA_MAX = 2.0


def write_schedule(model, theta, path):
    """θ ≤ 1 寫入營業強度; 超過 1 的部分照定版做法換算成等功率的分攤負荷 eq_bus(op_level 上限為 1)。"""
    idf = model.idf
    d_op, d_bus = op_design_kw(idf), bus_design_kw(idf)
    write_values(compact_hours(idf, "op_level"), [min(t, 1.0) for t in theta])
    write_values(compact_hours(idf, "eq_bus"), [max(t - 1.0, 0.0) * d_op / d_bus for t in theta])
    write_anc5(idf, [1.0] * 24)
    idf.saveas(str(path))


def hourly_mean(res, anc7):
    comp = [a - b for a, b in zip(res["Electricity:Facility"], anc7)]
    return [sum(comp[h::24]) / 365 for h in range(24)]


def fit(A, B, ref, train):
    theta = []
    for b in range(len(BLOCKS)):
        hs = [h for h in train if BLOCK_OF[h] == b]
        num = sum(B[h] * (ref[h] - A[h]) for h in hs)
        den = sum(B[h] ** 2 for h in hs)
        theta.append(min(max(num / den, 0.0), THETA_MAX))
    return theta


def stats(pred, ref, hours):
    e = [pred[h] - ref[h] for h in hours]
    rbar = sum(ref[h] for h in hours) / len(hours)
    n = len(hours)
    return {"cvrmse_pct": 100 * (sum(x * x for x in e) / (n - 1)) ** 0.5 / rbar,
            "nmbe_pct": 100 * sum(e) / (n - 1) / rbar, "max_abs_kW": max(abs(x) for x in e)}


def main():
    ref, anc7 = reference(), ancillary7()
    work = HERE / "_holdout"
    work.mkdir(exist_ok=True)
    model = Model()
    assert model.idf.getobject("SCHEDULE:COMPACT", ANC5)
    p0, p1 = work / "op0.idf", work / "op1.idf"
    write_schedule(model, [0.0] * 24, p0)
    write_schedule(model, [1.0] * 24, p1)
    r0, r1 = run_many([p0, p1], work)
    A = hourly_mean(r0, anc7)
    B = [x - a for x, a in zip(hourly_mean(r1, anc7), A)]

    folds, preds, paths = [], {}, []
    for f in range(FOLDS):
        test = [h for h in range(24) if h % FOLDS == f]
        train = [h for h in range(24) if h % FOLDS != f]
        theta = fit(A, B, ref, train)
        op = [theta[BLOCK_OF[h]] for h in range(24)]
        path = work / f"fold{f}.idf"
        write_schedule(model, op, path)
        paths.append(path)
        naive = {h: sum(ref[t] for t in train if BLOCK_OF[t] == BLOCK_OF[h]) /
                 sum(1 for t in train if BLOCK_OF[t] == BLOCK_OF[h]) for h in test}
        folds.append({"fold": f, "test_hours": test, "theta": theta, "naive": naive})
    theta_all = fit(A, B, ref, list(range(24)))
    full = work / "full.idf"
    write_schedule(model, [theta_all[BLOCK_OF[h]] for h in range(24)], full)
    sims = run_many(paths + [full], work)

    sim_test, naive_test, rows = [0.0] * 24, [0.0] * 24, []
    for fd, res in zip(folds, sims[:FOLDS]):
        mean = hourly_mean(res, anc7)
        for h in fd["test_hours"]:
            sim_test[h], naive_test[h] = mean[h], fd["naive"][h]
    full_mean = hourly_mean(sims[-1], anc7)
    hours = list(range(24))
    out = {
        "blocks": [{"hours": f"{s:02d}–{e:02d}", "name": n, "theta_all": round(t, 4)} for (s, e, n), t in zip(BLOCKS, theta_all)],
        "folds": [{"fold": fd["fold"], "test_hours": fd["test_hours"], "theta": [round(t, 4) for t in fd["theta"]]} for fd in folds],
        "out_of_sample_energyplus": stats(sim_test, ref, hours),
        "out_of_sample_naive_block_mean": stats(naive_test, ref, hours),
        "in_sample_7_param": stats(full_mean, ref, hours),
        "linear_surrogate_check_max_kW": max(abs(full_mean[h] - (A[h] + B[h] * theta_all[BLOCK_OF[h]])) for h in hours),
    }
    with open(HERE / "holdout_24h.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hour_start", "block", "fold", "energyplus_out_of_sample_kW", "naive_out_of_sample_kW", "in_sample_7param_kW"])
        for h in hours:
            w.writerow([h, BLOCKS[BLOCK_OF[h]][2], h % FOLDS, round(sim_test[h], 3), round(naive_test[h], 3), round(full_mean[h], 3)])
    (HERE / "holdout_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "folds"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
