"""互動結果頁的資料: 長興 2025 年逐時建築用電、充電、光伏、氣溫 → site/data.json。只用標準函式庫。

    python site/build_data.py
"""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CX = HERE.parent / "projects" / "changxing"


def column(path, name, encoding="utf-8-sig"):
    with open(path, encoding=encoding) as f:
        return [float(r[name]) for r in csv.DictReader(f)]


def main():
    building = column(CX / "hourly_2025.csv", "station_total_kWh")
    charging = column(CX / "charging_2025" / "charging_hourly_2025.csv", "charging_kWh")
    pv = column(CX / "pv_2025" / "pv_hourly_2025.csv", "pv_kWh")
    with open(CX / "weather" / "CHN_Changxing_ERA5_31.00N_120.00E_2025.epw", encoding="latin-1") as f:
        temp = [float(r[6]) for r in list(csv.reader(f))[8:]]  # EPW 第 7 欄是乾球溫度, 第 1 小時 = 00:00–01:00
    assert len(building) == len(charging) == len(pv) == len(temp) == 8760
    with open(CX / "ecm" / "ecm_results.csv", encoding="utf-8") as f:
        ecm = [{"label": r["label"], "saving_MWh": round(float(r["saving_kWh"]) / 1000, 1),
                "saving_pct": round(float(r["saving_pct"]), 2), "surplus_h": int(float(r["surplus_hours"]))}
               for r in csv.DictReader(f) if r["key"] not in ("baseline", "lights_film")]
    r1 = lambda xs: [round(x, 1) for x in xs]  # noqa: E731
    data = {"year": 2025, "building": r1(building), "charging": r1(charging), "pv": r1(pv), "temp": r1(temp), "ecm": ecm}
    (HERE / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"site/data.json: 建築 {sum(building) / 1000:,.0f} MWh、充電 {sum(charging) / 1000:,.0f} MWh、"
          f"光伏 {sum(pv) / 1000:,.0f} MWh")


if __name__ == "__main__":
    main()
