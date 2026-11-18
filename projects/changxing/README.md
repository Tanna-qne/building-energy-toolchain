# 長興高速服務區 2025 年基礎側用電

定版模型為 `cx_lowest_max.idf`。全年與 7 月逐時資料來自同一次 EnergyPlus 26.2 全年模擬。時間為北京時間（UTC+8），每列一小時，由 interval_start 起算至 interval_end。

| 用途 | 逐時檔欄位 | 定義 |
|---|---|---|
| 長興原表對照與論文 7 月基礎側序列 | comparison_kWh | 全站原生電表減去假設單獨計量的 CX ancillary 7 |
| 全站容量配置 | station_total_kWh | 全站原生電表 Electricity:Facility，已包含站內供電的 CX ancillary 7 |
| 分表核對 | ancillary7_kWh | 上述 3 kW 支路，08:00–18:00 運轉 |

`hourly_2025.csv` 含全年 8,760 小時，`hourly_2025_07.csv` 為其中連續的 7 月 744 小時。每列 kWh 的數值等於該小時平均 kW。7 月對照口徑合計 163,149.27 kWh，全站合計 164,079.27 kWh。

`calibration_24h.csv` 將全年 365 天的同鐘點平均負荷與原表逐點對照，偏差方向為模擬減原表。最大絕對偏差 1.3685 kW。`schedules.csv` 記錄定版的逐時營業與附屬支路系數。北側商鋪按原溫控獨立製冷，12 kW 附屬站房在 15、16 時分別運轉 4.2、10.2 kW。營業排程及分表邊界為校準假設，7 月缺少獨立實測驗證。
