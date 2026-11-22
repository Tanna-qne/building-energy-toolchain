"""單速 / 變頻兩個校準後模型的分項用電對比圖(hvac_vs/hvac_compare.png)。

    uv run --with matplotlib python projects/changxing/hvac_vs/make_figure.py
"""
import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
HERE = Path(__file__).resolve().parent
INK, MUTED, GRID = "#303133", "#909399", "#ebeef5"
C_SS, C_VS = "#909399", "#2a78d6"


def main():
    d = json.loads((HERE / "compare.json").read_text(encoding="utf-8"))
    ss, vs = d["singlespeed"], d["variable_speed"]
    for m in (ss, vs):
        m["other_MWh"] = m["station_total_MWh"] - m["cooling_MWh"] - m["heating_MWh"] - m["fans_MWh"]
    parts = [("cooling_MWh", "製冷壓縮機"), ("heating_MWh", "供暖(含除霜、電輔熱)"), ("fans_MWh", "風機"),
             ("other_MWh", "照明、設備、廚房等")]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11.5, 4.4), gridspec_kw={"width_ratios": [2.3, 1]})
    y = range(len(parts))
    h = 0.36
    for off, m, c, lab in ((-h / 2, ss, C_SS, "單速 PTHP(原定版)"), (h / 2, vs, C_VS, "變頻 PTHP(新定版)")):
        vals = [m[k] for k, _ in parts]
        a1.barh([i + off for i in y], vals, height=h, color=c, label=lab)
        for i, v in zip(y, vals):
            a1.text(v + 8, i + off, f"{v:,.1f}", va="center", fontsize=8, color=INK)
    a1.set_yticks(list(y))
    a1.set_yticklabels([n for _, n in parts], fontsize=9)
    a1.invert_yaxis()
    a1.set_xlabel("全年用電 MWh", color=INK, fontsize=9)
    a1.set_xlim(0, max(ss["other_MWh"], vs["other_MWh"]) * 1.15)
    a1.set_title("各自校準後:總量相同,分項重新分配", color=INK, fontsize=11)
    a1.legend(frameon=False, fontsize=9, loc="upper right")

    cops = [("seasonal_cop_cooling", "製冷"), ("seasonal_cop_heating", "製熱")]
    x = range(len(cops))
    w = 0.34
    for off, m, c in ((-w / 2, ss, C_SS), (w / 2, vs, C_VS)):
        vals = [m[k] for k, _ in cops]
        a2.bar([i + off for i in x], vals, width=w, color=c)
        for i, v in zip(x, vals):
            a2.text(i + off, v + 0.06, f"{v:.2f}", ha="center", fontsize=8, color=INK)
    a2.set_xticks(list(x))
    a2.set_xticklabels([n for _, n in cops], fontsize=9)
    a2.set_ylim(0, 5)
    a2.set_ylabel("季節平均 COP(盤管供冷熱量 ÷ 壓縮機耗電)", color=INK, fontsize=8)
    a2.set_title("額定 COP 相同(3.5 / 3.3)", color=INK, fontsize=11)
    for ax, grid_axis in ((a1, "x"), (a2, "y")):
        getattr(ax, f"{grid_axis}axis").grid(True, color=GRID)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color(MUTED)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(colors=INK)
    fig.suptitle(f"全站全年 {ss['station_total_MWh']:,.0f} → {vs['station_total_MWh']:,.0f} MWh;"
                 f"CV(RMSE) {ss['cvrmse_pct']:.2f}% → {vs['cvrmse_pct']:.2f}%", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(HERE / "hvac_compare.png", dpi=160, bbox_inches="tight")


if __name__ == "__main__":
    main()
