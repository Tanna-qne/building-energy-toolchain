"""節能措施分項節電圖(ecm/ecm_savings.png)。

    uv run --with matplotlib python projects/changxing/ecm/make_figure.py
"""
import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
HERE = Path(__file__).resolve().parent
INK, MUTED, GRID = "#303133", "#909399", "#ebeef5"
PARTS = [("direct", "照明 / 設備本身", "#2f6fdb"), ("d_cooling_kWh", "製冷", "#e0892b"),
         ("d_fans_kWh", "風機", "#7a52c7"), ("d_heating_kWh", "供暖", "#c2410c")]


def main():
    d = json.loads((HERE / "ecm_summary.json").read_text(encoding="utf-8"))
    ms = [m for m in d["measures"] if m["key"] != "lights_film"]
    for m in ms:
        m["direct"] = m["saving_kWh"] - m["d_cooling_kWh"] - m["d_heating_kWh"] - m["d_fans_kWh"]
    ms.sort(key=lambda m: m["saving_kWh"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5), sharey=True, gridspec_kw={"width_ratios": [2.2, 1]})
    y = range(len(ms))
    for key, label, color in PARTS:
        pos = [0.0] * len(ms)
        neg = [0.0] * len(ms)
        for k, m in enumerate(ms):
            prev = [m[p[0]] / 1000 for p in PARTS[:[p[0] for p in PARTS].index(key)]]
            pos[k] = sum(v for v in prev if v > 0)
            neg[k] = sum(v for v in prev if v < 0)
        vals = [m[key] / 1000 for m in ms]
        left = [p if v >= 0 else n for v, p, n in zip(vals, pos, neg)]
        a1.barh(y, vals, left=left, color=color, height=0.6, label=label, edgecolor="white", linewidth=1)
    for k, m in enumerate(ms):
        right = sum(max(m[p[0]], 0) for p in PARTS) / 1000
        a1.text(right + 2, k, f"{m['saving_kWh'] / 1000:,.1f} MWh({m['saving_pct']:.1f}%)", va="center", fontsize=8, color=INK)
    a1.axvline(0, color=MUTED, lw=0.8)
    a1.set_yticks(list(y))
    a1.set_yticklabels([m["label"].replace("−", "-") for m in ms], fontsize=9)
    a1.set_xlabel("全年節電 MWh(負值 = 該分項用電增加)", color=INK, fontsize=9)
    a1.set_title(f"全年節電與分項(基準 {d['baseline']['annual_kWh'] / 1000:,.0f} MWh)", color=INK, fontsize=11)
    a1.legend(frameon=False, fontsize=8, loc="lower right")
    a1.set_xlim(right=max(sum(max(m[p[0]], 0) for p in PARTS) for m in ms) / 1000 * 1.25)

    base = d["baseline"]["surplus_kWh"] / 1000
    vals = [m["surplus_kWh"] / 1000 for m in ms]
    a2.axvline(base, color=MUTED, lw=1, ls="--")
    a2.text(base + 0.05, -0.55, f"基準 {base:.1f} MWh / {d['baseline']['surplus_hours']} h", fontsize=8, color=MUTED)
    a2.hlines(list(y), base, vals, color=GRID, lw=2)
    a2.plot(vals, list(y), "o", color="#2f6fdb", ms=8)
    for k, (m, v) in enumerate(zip(ms, vals)):
        a2.text(v + 0.12, k, f"{v:.1f}({m['surplus_hours']} h)", va="center", fontsize=8, color=INK)
    a2.set_xlim(base - 0.4, max(vals) + 1.0)
    a2.set_ylim(-0.9, len(ms) - 0.5)
    a2.set_xlabel("7 月光伏餘電 MWh(括號為餘電小時數)", color=INK, fontsize=9)
    a2.set_title("對光儲的影響:負荷降低 → 餘電增加", color=INK, fontsize=11)
    for ax in (a1, a2):
        ax.xaxis.grid(True, color=GRID)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color(MUTED)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(colors=INK)
    fig.suptitle("長興服務區節能措施:9 次全年 EnergyPlus 模擬(措施定義與工具鏈相同)", color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(HERE / "ecm_savings.png", dpi=160, bbox_inches="tight")


if __name__ == "__main__":
    main()
