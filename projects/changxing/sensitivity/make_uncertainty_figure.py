"""不確定性區間圖(sensitivity/uncertainty.png)。

    uv run --with matplotlib python projects/changxing/sensitivity/make_uncertainty_figure.py
"""
import csv
import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
HERE = Path(__file__).resolve().parent
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#ecebe6"
C_CAL, C_PRIOR = "#2a78d6", "#c3c2b7"
OUTS = [("annual_MWh", "全年電量"), ("july_MWh", "7 月電量"), ("peak_kW", "全年峰值"), ("cooling_MWh", "製冷"),
        ("heating_MWh", "供暖"), ("fans_MWh", "風機"), ("surplus_MWh", "7 月光伏餘電"), ("peak_net_kW", "7 月淨負荷峰值")]
WEEK = ("2025-07-14", 7)


def main():
    s = json.loads((HERE / "uncertainty_summary.json").read_text(encoding="utf-8"))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1, 1.5]})
    for i, (k, name) in enumerate(OUTS):
        mid = s["calibrated"][k]["50"]
        for off, tag, c, lw in ((-0.17, "prior", C_PRIOR, 7), (0.17, "calibrated", C_CAL, 7)):
            lo, hi = (100 * (s[tag][k][q] / mid - 1) for q in ("5", "95"))
            a1.plot([lo, hi], [i + off] * 2, color=c, lw=lw, solid_capstyle="round")
        lo, hi = (100 * (s["calibrated"][k][q] / mid - 1) for q in ("5", "95"))
        a1.text(max(hi, 100 * (s["prior"][k]["95"] / mid - 1)) + 0.6, i, f"±{(hi - lo) / 2:.1f}%", va="center",
                fontsize=8, color=INK2)
    a1.set_yticks(range(len(OUTS)))
    a1.set_yticklabels([n for _, n in OUTS], fontsize=9)
    a1.invert_yaxis()
    a1.axvline(0, color=MUTED, lw=0.8)
    a1.set_xlim(right=a1.get_xlim()[1] + 12)
    a1.set_xlabel("相對各自校準後中位數 %(5–95% 區間)", fontsize=9, color=INK)
    a1.plot([], [], color=C_PRIOR, lw=7, label="不重新校準")
    a1.plot([], [], color=C_CAL, lw=7, label="每組重新校準")
    a1.legend(frameon=False, fontsize=9, loc="lower right")
    a1.set_title(f"{s['n']} 組參數的輸出區間", fontsize=11, color=INK)

    with open(HERE / "uncertainty_hourly_bands.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    k0 = next(k for k, r in enumerate(rows) if r["interval_start"].startswith(WEEK[0]))
    w = rows[k0:k0 + 24 * WEEK[1]]
    x = range(len(w))
    g = lambda c: [float(r[c]) for r in w]  # noqa: E731
    a2.fill_between(x, g("prior_p5_kWh"), g("prior_p95_kWh"), color=C_PRIOR, alpha=0.7, lw=0, label="不重新校準 5–95%")
    a2.fill_between(x, g("p5_kWh"), g("p95_kWh"), color=C_CAL, alpha=0.45, lw=0, label="重新校準 5–95%", zorder=2)
    a2.plot(x, g("station_total_kWh"), color=C_CAL, lw=1.0, label="定版模型", zorder=1)
    a2.set_xticks(range(0, len(w), 24))
    a2.set_xticklabels([f"7/{14 + d}" for d in range(WEEK[1])], fontsize=9)
    a2.set_ylabel("建築逐時用電 kWh", fontsize=9, color=INK)
    a2.set_ylim(0, 700)
    a2.legend(frameon=False, fontsize=9, loc="upper left", ncol=3)
    a2.set_title("7 月一週的逐時區間", fontsize=11, color=INK)
    for ax, axis in ((a1, "x"), (a2, "y")):
        getattr(ax, f"{axis}axis").grid(True, color=GRID)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(MUTED)
        ax.tick_params(colors=INK)
    fig.tight_layout()
    fig.savefig(HERE / "uncertainty.png", dpi=160, bbox_inches="tight")


if __name__ == "__main__":
    main()
