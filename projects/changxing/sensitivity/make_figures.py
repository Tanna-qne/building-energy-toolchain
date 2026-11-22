"""敏感度分析與留出驗證的兩張圖(sensitivity/*.png)。

    uv run --with matplotlib python projects/changxing/sensitivity/make_figures.py
"""
import csv
import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INK, MUTED, GRID = "#303133", "#909399", "#ebeef5"
C_ANNUAL, C_JULY = "#409eff", "#e6a23c"


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=INK)


def morris():
    rows = list(csv.DictReader(open(HERE / "morris_indices.csv", encoding="utf-8")))
    base = json.loads((HERE / "morris_summary.json").read_text(encoding="utf-8"))["baseline"]
    get = lambda o: {r["param"]: (r["label"], float(r["mu_star"]), float(r["sigma"])) for r in rows if r["output"] == o}
    annual, july = get("annual_MWh"), get("july_MWh")
    order = sorted(annual, key=lambda n: annual[n][1])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5.2), sharey=True)
    for ax, data, color, title, total in ((a1, annual, C_ANNUAL, "全年電量", base["annual_MWh"]),
                                           (a2, july, C_JULY, "7 月電量", base["july_MWh"])):
        y = range(len(order))
        vals = [100 * data[n][1] / total for n in order]
        ax.barh(y, vals, color=color, height=0.6)
        for yi, v in zip(y, vals):
            ax.text(v + 0.05, yi, f"{v:.1f}%", va="center", fontsize=8, color=INK)
        ax.set_title(f"{title}(定版 {total:,.0f} MWh)", color=INK, fontsize=11)
        ax.set_xlabel("μ*:參數走過全範圍 2/3 時的平均變化,占定版值 %", color=INK, fontsize=9)
        ax.xaxis.grid(True, color=GRID)
        ax.set_axisbelow(True)
        style(ax)
    a1.set_yticks(range(len(order)))
    a1.set_yticklabels([annual[n][0] for n in order], fontsize=9)
    fig.suptitle("Morris 全域敏感度:14 個未校準參數 × 150 次全年模擬", color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(HERE / "morris_mu_star.png", dpi=160, bbox_inches="tight")


def holdout():
    rows = list(csv.DictReader(open(HERE / "holdout_24h.csv", encoding="utf-8")))
    s = json.loads((HERE / "holdout_summary.json").read_text(encoding="utf-8"))
    ref = [float(r["reference_kW"]) for r in csv.DictReader(open(ROOT / "calibration_24h.csv", encoding="utf-8-sig"))]
    h = [int(r["hour_start"]) for r in rows]
    ep = [float(r["energyplus_out_of_sample_kW"]) for r in rows]
    nv = [float(r["naive_out_of_sample_kW"]) for r in rows]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.bar(h, ref, color="#dcdfe6", width=0.8, label="長興典型日(原表)")
    ax.plot(h, ep, "o-", color=C_ANNUAL, lw=2, ms=6,
            label=f"EnergyPlus 7 時段模型,留出預測  CV(RMSE) {s['out_of_sample_energyplus']['cvrmse_pct']:.0f}%")
    ax.plot(h, nv, "s--", color=C_JULY, lw=2, ms=6,
            label=f"對照:同時段訓練點平均  CV(RMSE) {s['out_of_sample_naive_block_mean']['cvrmse_pct']:.0f}%")
    edges = [0, 5, 8, 11, 14, 17, 21, 24]
    for e in edges[1:-1]:
        ax.axvline(e - 0.5, color=MUTED, lw=0.6, ls=":")
    for b in s["blocks"]:
        a, z = (int(x) for x in b["hours"].split("–"))
        ax.text((a + z - 1) / 2, max(ref) * 1.08, b["name"], ha="center", fontsize=8, color=MUTED)
    ax.set_xticks(h)
    ax.set_xlabel("北京時間(時)", color=INK)
    ax.set_ylabel("全年同鐘點平均 kW", color=INK)
    ax.set_ylim(0, max(ref) * 1.15)
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3)
    ax.set_title("4 折留出驗證:每個點都是在沒看過它的情況下預測的", color=INK, fontsize=11)
    style(ax)
    fig.savefig(HERE / "holdout_24h.png", dpi=160, bbox_inches="tight")


if __name__ == "__main__":
    morris()
    holdout()
