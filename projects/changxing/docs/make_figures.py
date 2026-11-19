"""產生模型說明用的三張圖(docs/*.png), 只讀 repo 裡的定版檔案。

    uv run --with matplotlib python projects/changxing/docs/make_figures.py
"""
import csv
import re
from datetime import datetime
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IDF = ROOT / "cx_lowest_max.idf"
EPW = ROOT / "weather" / "CHN_Changxing_ERA5_31.00N_120.00E_2025.epw"
USE = {"Supermarket": ("超市/商鋪", "#e6a23c"), "Shop": ("超市/商鋪", "#e6a23c"), "Lobby": ("大廳", "#67c23a"),
       "StairW": ("大廳", "#67c23a"), "Dining": ("餐飲", "#f56c6c"), "Kitchen": ("廚房", "#c45656"),
       "Store": ("食庫", "#909399"), "WC": ("公廁", "#79bbff"), "Office": ("辦公", "#a0cfff"),
       "ServiceE": ("辦公", "#a0cfff"), "Dorm": ("宿舍", "#b37feb")}


def idf_objects(cls):
    text = IDF.read_text(encoding="latin-1")
    text = re.sub(r"!.*", "", text)
    for m in re.finditer(rf"^\s*{re.escape(cls)},(.*?);", text, re.S | re.M | re.I):
        yield [f.strip() for f in m.group(1).split(",")]


def vertices(fields, start):
    v = [float(x) for x in fields[start:] if x]
    return [tuple(v[i:i + 3]) for i in range(0, len(v) - 2, 3)]


def use_of(zone):
    return next(v for k, v in USE.items() if k in zone)


def model_3d():
    fig = plt.figure(figsize=(11, 6))
    ax = fig.add_subplot(projection="3d")
    seen = {}
    for f in idf_objects("BuildingSurface:Detailed"):
        kind, zone = f[1].lower(), f[3]
        pts = vertices(f, 11)  # 欄位 0–10: 名稱 … 頂點數, 之後是頂點座標
        label, color = use_of(zone)
        if kind == "floor":
            poly = Poly3DCollection([pts], facecolor=color, edgecolor="k", linewidth=0.3, alpha=0.9)
            ax.add_collection3d(poly)
            seen[label] = color
        elif kind == "wall" and f[5].lower() == "outdoors":
            ax.add_collection3d(Poly3DCollection([pts], facecolor="#dcdfe6", edgecolor="#606266", linewidth=0.2, alpha=0.15))
    for f in idf_objects("FenestrationSurface:Detailed"):
        pts = vertices(f, 9)  # 欄位 0–8: 名稱 … 頂點數
        ax.add_collection3d(Poly3DCollection([pts], facecolor="#409eff", edgecolor="none", alpha=0.6))
    ax.set_xlim(0, 110); ax.set_ylim(-10, 45); ax.set_zlim(0, 12)
    ax.set_box_aspect((110, 55, 12 * 2.5))
    ax.view_init(elev=28, azim=-60)
    ax.set_axis_off()
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in seen.values()] + [plt.Rectangle((0, 0), 1, 1, color="#409eff")]
    ax.legend(handles, list(seen) + ["外窗"], loc="upper left", ncol=4, frameon=False)
    ax.set_title("長興服務區綜合樓 EnergyPlus 模型(18 個熱區,5,048 m²;樓板依用途著色)", pad=2)
    fig.savefig(HERE / "model_3d.png", dpi=160, bbox_inches="tight")


def calibration():
    rows = list(csv.DictReader(open(ROOT / "calibration_24h.csv", encoding="utf-8-sig")))
    h = [int(r["hour_start"]) for r in rows]
    ref = [float(r["reference_kW"]) for r in rows]
    sim = [float(r["simulation_kW"]) for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(h, ref, color="#c0c4cc", label=f"長興典型日(原表){sum(ref):,.0f} kWh/日")
    ax.plot(h, sim, "o-", color="#e6a23c", label=f"模型:2025 年 365 天同鐘點平均 {sum(sim):,.0f} kWh/日")
    ax.set_xticks(h); ax.set_xlabel("北京時間(時)"); ax.set_ylabel("基礎用電 kW")
    ax.set_title("校準對照:24 個鐘點最大偏差 {:.2f} kW".format(max(abs(a - b) for a, b in zip(ref, sim))))
    ax.legend(frameon=False)
    fig.savefig(HERE / "calibration_24h.png", dpi=160, bbox_inches="tight")


def weather_days():
    temp = {}
    for line in EPW.read_text().splitlines()[8:]:
        f = line.split(",")
        temp[(int(f[1]), int(f[2]), int(f[3]) - 1)] = float(f[6])
    load = {}
    for r in csv.DictReader(open(ROOT / "hourly_2025.csv", encoding="utf-8-sig")):
        t = datetime.strptime(r["interval_start"], "%Y-%m-%d %H:%M:%S")
        load[(t.month, t.day, t.hour)] = float(r["comparison_kWh"])
    days = sorted({(m, d) for m, d, _ in temp})
    mean_t = {md: sum(temp[(*md, h)] for h in range(24)) / 24 for md in days}
    hot = max(days, key=lambda md: mean_t[md])
    mild = min(days, key=lambda md: abs(mean_t[md] - 18))
    cold = min(days, key=lambda md: mean_t[md])
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True)
    for md, c in ((hot, "#f56c6c"), (mild, "#67c23a"), (cold, "#409eff")):
        lab = f"2025-{md[0]:02d}-{md[1]:02d}(日均 {mean_t[md]:.1f}°C)"
        a1.plot(range(24), [temp[(*md, h)] for h in range(24)], color=c, label=lab)
        a2.plot(range(24), [load[(*md, h)] for h in range(24)], color=c,
                label=f"{lab}:{sum(load[(*md, h)] for h in range(24)):,.0f} kWh")
    ref = [float(r["reference_kW"]) for r in csv.DictReader(open(ROOT / "calibration_24h.csv", encoding="utf-8-sig"))]
    a2.plot(range(24), ref, "k--", lw=1, label=f"長興典型日(原表):{sum(ref):,.0f} kWh")
    a1.set_ylabel("室外氣溫 °C(ERA5)"); a1.legend(frameon=False, fontsize=8)
    a2.set_ylabel("模擬基礎用電 kW"); a2.set_xlabel("北京時間(時)"); a2.legend(frameon=False, fontsize=8)
    a2.set_xticks(range(24))
    a1.set_title("同一個模型、不同天氣:最熱日、溫和日、最冷日")
    fig.savefig(HERE / "weather_days.png", dpi=160, bbox_inches="tight")
    return hot, mild, cold, {md: sum(load[(*md, h)] for h in range(24)) for md in (hot, mild, cold)}, mean_t


if __name__ == "__main__":
    model_3d()
    calibration()
    print(weather_days()[:4])
