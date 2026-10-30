"""把 DesignBuilder / EnergyPlus 匯出的 eplusout.csv 匯總成節能方案對比表。

用法:
    python dbcase.py <方案根目錄> --area 120 --out results.csv

方案根目錄下每個子資料夾代表一個方案, 裡面放該次模擬的 eplusout.csv。
子資料夾名稱就是方案名稱; 名稱含 baseline 的當作基準。
"""
import argparse
import csv
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

J_PER_KWH = 3.6e6

# 顯示名稱 -> 表頭關鍵字(依序找第一個命中的欄位)
METRICS = [
    ("總用電", ("Electricity:Facility",)),
    ("天然氣", ("NaturalGas:Facility",)),
    # 這是建築與環境的能量交換量, 不是耗能; 只在沒有電/氣電表時當 EUI 的替代
    ("能量交換", ("EnergyTransfer:Facility",)),
    ("空調用電", ("Electricity:HVAC",)),
    ("供暖能耗", ("Heating:EnergyTransfer",)),
    ("製冷能耗", ("Cooling:EnergyTransfer",)),
    ("照明用電", ("InteriorLights:Electricity",)),
    ("設備用電", ("InteriorEquipment:Electricity",)),
    ("戶外照明", ("ExteriorLights:Electricity",)),
]
# EUI 用總用電 + 天然氣, 都沒有才用能量交換
EUI_LABELS = ("總用電", "能量交換")

# 有 eplusout.csv 就用它, 否則用電錶檔 eplusmtr.csv
CSV_PRIORITY = ("eplusout.csv", "eplusmtr.csv")


def is_unit_row(row):
    """eplusout.csv 第 2 列是單位列如 [J](TimeStep); eplusmtr.csv 沒有這列。"""
    cells = [c.strip() for c in row if c.strip()]
    return bool(cells) and all(c.startswith(("[", "(")) for c in cells)


def read_annual_kwh(csv_path):
    """回傳 {顯示名稱: 全年 kWh}。讀不到的量不回傳。"""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        raise ValueError(f"{csv_path}: 資料不足")

    header = rows[0]
    data_start = 2 if is_unit_row(rows[1]) else 1
    result = {}
    for label, keys in METRICS:
        for key in keys:
            col = next((c for c, name in enumerate(header) if key in name), None)
            if col is not None:
                total_j = 0.0
                for row in rows[data_start:]:
                    if col >= len(row):
                        continue
                    try:
                        total_j += float(row[col])
                    except ValueError:
                        pass
                result[label] = round(total_j / J_PER_KWH, 1)
                break
    # 沒有廠址總表時, 用各分項加總當總量
    if result and not any(lab in result for lab in EUI_LABELS):
        result["能量交換"] = round(sum(result.values()), 1)
    return result


def collect(root):
    cases = {}
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        data = {}
        for p in [sub / n for n in CSV_PRIORITY] + sorted(sub.glob("*.csv")):
            if p.exists():
                data = read_annual_kwh(p)
                if data:            # eplusout.csv 可能只有變數、沒有電錶欄位
                    break
        if data:
            cases[sub.name] = data
    if not cases:
        raise SystemExit(f"{root}: 找不到任何含 csv 的方案子資料夾")
    return cases


def pick_baseline(cases):
    for name in cases:
        if "baseline" in name.lower() or "基準" in name:
            return name
    return next(iter(cases))


def eui_label(cases):
    for lab in EUI_LABELS:
        if any(lab in data for data in cases.values()):
            return lab
    return None


def build_table(cases, area):
    base = pick_baseline(cases)
    labels = [lab for lab, _ in METRICS if any(lab in d for d in cases.values())]
    eui = eui_label(cases)
    table = []
    for name, data in cases.items():
        total = data.get(eui, 0) if eui else 0
        if eui == "總用電":
            total += data.get("天然氣", 0)
        row = {"方案": name, "EUI (kWh/m²)": round(total / area, 1)}
        for lab in labels:
            val = data.get(lab, 0)
            row[lab] = val
            b = cases[base].get(lab, 0)
            row[f"{lab} Δ%"] = round((val - b) / b * 100, 1) if b else None
        table.append(row)
    return base, labels, table


def to_markdown(base, table, labels, area):
    cols = ["方案", "EUI (kWh/m²)"] + labels + [f"{l} Δ%" for l in labels]
    out = [f"基準方案: **{base}**, 樓地板面積假設 {area} m²", ""]
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "---|" * len(cols))
    for row in table:
        cells = []
        for c in cols:
            v = row.get(c)
            cells.append("" if v is None else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="方案根目錄")
    ap.add_argument("--area", type=float, default=100.0, help="樓地板面積 m², 用於 EUI")
    ap.add_argument("--out", type=Path, default=Path("results.csv"))
    args = ap.parse_args()

    cases = collect(args.root)
    base, labels, table = build_table(cases, args.area)

    cols = ["方案", "EUI (kWh/m²)"] + labels + [f"{l} Δ%" for l in labels]
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in table:
            w.writerow({k: row.get(k, "") for k in cols})

    md = to_markdown(base, table, labels, args.area)
    args.out.with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
