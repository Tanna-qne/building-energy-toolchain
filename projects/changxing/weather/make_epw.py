"""Open-Meteo ERA5 逐時 CSV(UTC+8)-> 全年 EPW(2025)。

    python projects/changxing/weather/make_epw.py

時間對齊(EPW 第 h 小時 = 當地標準時間 (h-1):00–h:00):
- 輻射(GHI / DNI / DHI)在 CSV 是「時間戳之前一小時」的平均, 所以 EPW 第 h 小時取 CSV 的 (日期 h:00);
  第 24 小時取隔天 00:00。例: EPW 7/1 第 1 小時(00:00–01:00)= CSV 2025-07-01T01:00;
  EPW 7/31 第 24 小時(23:00–24:00)= CSV 2025-08-01T00:00。
- 溫度、濕度、氣壓、風、雲量在 EPW 是該小時結束時刻的值, 同樣取 CSV 的 (日期 h:00), 規則一致。
- 12/31 第 24 小時需要 2026-01-01T00:00, 來自 source/open-meteo-boundary-2026-01-01T00.csv
  (同一 API 設定取得, 已驗證 2025-07-01..12-31 與原 CSV 完全相同)。
單位: 風速 km/h ÷ 3.6 → m/s; 氣壓 hPa × 100 → Pa; 雲量 % → 十分位(÷10)。
"""
import csv
import math
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "source" / "open-meteo-31.00N120.00E12m.csv"
BOUNDARY = HERE / "source" / "open-meteo-boundary-2026-01-01T00.csv"
OUT = HERE / "CHN_Changxing_ERA5_31.00N_120.00E_2025.epw"
LAT, LON, TZ, ELEV = 31.0, 120.0, 8.0, 12.0


def read(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.startswith("#")]
    start = next(i for i, ln in enumerate(lines) if ln.startswith("time,"))
    for r in csv.reader(lines[start + 1:]):
        rows[r[0]] = [float(x) for x in r[1:]]
    return rows


def main():
    data = read(SRC)
    data.update(read(BOUNDARY))
    out = [
        f"LOCATION,Changxing ERA5 grid 31.00N 120.00E,Zhejiang,CHN,Open-Meteo ECMWF ERA5 reanalysis,999999,"
        f"{LAT:.2f},{LON:.2f},{TZ:.1f},{ELEV:.1f}",
        "DESIGN CONDITIONS,0",
        "TYPICAL/EXTREME PERIODS,0",
        "GROUND TEMPERATURES,0",
        "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
        "COMMENTS 1,Converted from Open-Meteo ERA5 hourly (UTC+8). Radiation = average of the hour ending at the "
        "timestamp. Missing fields use EPW missing codes; horizontal IR computed by EnergyPlus from opaque sky cover",
        "COMMENTS 2,Opaque sky cover set equal to total cloud cover (estimate). Actual year 2025 (not a TMY)",
        "DATA PERIODS,1,1,Data,Wednesday, 1/ 1,12/31",  # 2025-01-01 是星期三
    ]
    t0 = datetime(2025, 1, 1)
    n = 0
    for d in range(365):
        day = t0 + timedelta(days=d)
        for h in range(1, 25):
            stamp = (day + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
            temp, wind_kmh, wind_dir, p_hpa, rh, dew, cloud, precip, ghi, dhi, dni = data[stamp]
            tenths = int(round(cloud / 10))
            fields = [
                day.year, day.month, day.day, h, 60, "?9?9?9?9E0?9?9?9?9?9?9?9?9?9?9?9?9?9?9?9*9*9?9?9?9",
                f"{temp:.1f}", f"{dew:.1f}", int(round(rh)), int(round(p_hpa * 100)),
                9999, 9999, 9999,                      # 地外水平 / 法向輻射、水平紅外: 缺(EnergyPlus 自行計算)
                int(round(ghi)), int(round(dni)), int(round(dhi)),
                999999, 999999, 999999, 9999,          # 照度: 缺
                int(round(wind_dir)) % 360, f"{wind_kmh / 3.6:.1f}",
                tenths, tenths,                        # 總雲量; 不透明雲量 = 總雲量(估)
                9999, 99999, 9, 999999999,             # 能見度、雲高、天氣觀測: 缺
                999, 0.999, 999, 99, 999,              # 可降水量、氣膠、積雪、距上次降雪天數、反照率: 缺
                f"{precip:.1f}", 1.0,                  # 降水量 mm、降水時段 1 h
            ]
            out.append(",".join(str(x) for x in fields))
            n += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out) + "\n", encoding="ascii")
    print(f"{OUT.name}: {n} 小時")


if __name__ == "__main__":
    main()
