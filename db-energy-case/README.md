# DesignBuilder 節能方案對比

把每個方案的模擬輸出整理成 EUI、分項能耗和相對基準的變化,輸出一張表和一份 Markdown 報告。

## 用法

1. 在 DesignBuilder 建好基準模型,複製成幾個變體(外牆保溫、玻璃、空調設定溫度/時程等),各跑一次模擬。
2. 把每次模擬的輸出各放進一個子資料夾(名稱就是方案名):

```
方案根目錄/
├─ baseline/eplusmtr.csv
├─ insulation/eplusmtr.csv
└─ insulation_glazing/eplusmtr.csv
```

3. 執行:

```
python dbcase.py runs --area 120 --out results.csv
```

`--area` 是樓地板面積(m²),只影響 EUI。名稱含 `baseline`(或「基準」)的子資料夾自動當基準。結果寫成 `results.csv` 和 `results.md`。

## 讀什麼檔

子資料夾裡依序找 `eplusout.csv`、`eplusmtr.csv`,取第一個讀得到電錶欄位的。`eplusout.csv` 常常只有變數欄位(溫度之類),真正的能耗在 `eplusmtr.csv`,兩者都支援。數值單位是 J,自動換成 kWh。

## EnergyPlus 引擎

已安裝可攜版 **EnergyPlus 26.2.0**:

```
C:\Users\kiri\AppData\Local\Programs\EnergyPlusV26-2-0\EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64\energyplus.exe
```

範例模型 + 氣象檔直接跑:

```
energyplus.exe -w WeatherData\<城市>.epw -d <輸出目錄> -r ExampleFiles\<模型>.idf
```

`-r` 一定要加,否則不會產生 CSV。`Energy+.idd` 同目錄。

## 目錄

- `runs/` — 真實 EnergyPlus 執行結果。目前是同一個 EvapCooler 範例模型跑 Chicago 與 Tampa 兩個氣象檔,用來說明流程;不是 DesignBuilder 的模型。
- `demo/` — **手造假資料**,只驗證程式邏輯,數字無意義。

## DesignBuilder 的引擎狀況

- 授權沒問題:診斷日誌顯示 `Licence (Evaluation) for EP` 有效到 2026-11-23(EP = EnergyPlus)。
- 但安裝目錄 `DesignBuilder\EnergyPlus\` 只有一個 `DesignBuilderEnergyPlus.exe`(模擬管理程式),沒有 `energyplus.exe`,也沒有 `Energy+.idd`;整機、登錄表、`Lib\`、ProgramData 都找不到引擎。
- 程式內寫死路徑 `...\Lib\EnergyPlus\DesignBuilderEnergyPlus.exe`,所以引擎本來就該放在安裝目錄裡。

結論:DB 內建模擬大概率跑不起來,要嘛重裝 DB 時把引擎一起裝上,要嘛走下面的替代路線。

## 替代路線(現在就能用)

DB 建模 → 匯出 IDF(匯出不需要引擎)→ 用上面那份 EnergyPlus 26.2 跑 → `dbcase.py` 匯總。整條路不依賴 DB 的模擬功能,只依賴它的建模和匯出。
