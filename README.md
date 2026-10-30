# 建築能耗模擬工具鏈

輸入幾個建築參數(尺寸、樓層、窗牆比、用途、空調系統),自動建好 EnergyPlus 模型,
跑基準與節能措施(照明 −30%、外窗貼膜、滲風 ×0.5、組合),算出省能、省錢與回收期。
不需要會 DesignBuilder 或 EnergyPlus。

## 三步驟

1. 複製 `tools/modeler/example.json`,改成你的建築:

   ```json
   {
     "name": "demo_office",
     "shape": "rect",
     "width": 40, "depth": 25,
     "stories": 3, "floor_height": 3.6,
     "wwr": 0.4,
     "building_type": "MediumOffice",
     "climate_zone": 5,
     "construction": "SteelFramed",
     "hvac": "VAV_Chiller_Boiler"
   }
   ```

   L 形用 `"shape": "L"`,再加 `notch_width`、`notch_depth`(從右上角挖掉的大小)。
   `name`、`width`、`depth` 必填,其他不填就用上面的預設值;填錯會列出可用的選項。

2. 執行(用主 Python,它會自己呼叫建模工具的 venv):

   ```
   python run_building.py 你的檔案.json
   ```

3. 用瀏覽器打開 `db_models/out/report.html`:最上面一句話告訴你哪個措施最划算,
   下面依序是一年用多少能源、每個措施一年省多少錢,以及「值得考慮 / 看情況 / 不太划算」的白話判讀。
   給工程師看的明細在同資料夾的 `report.md` 與 `results.csv`。

一棟 3 層、15 個 zone 的辦公大樓,從建模到跑完 5 個情境約 1–2 分鐘。

## 可選參數

| 參數 | 說明 | 例子 |
|---|---|---|
| `building_type` | DOE 標準建築用途,決定人員、照明、設備負載與作息 | `MediumOffice`、`Retail`、`SmallHotel`、`PrimarySchool` |
| `climate_zone` | ASHRAE 氣候區,決定牆、屋頂、窗的構造 | 1–8;目前氣象檔是 Chicago(5) |
| `construction` | 構造型式 | `SteelFramed`、`WoodFramed`、`Mass`、`Metal Building` |
| `hvac` | 空調系統 | `VAV_Chiller_Boiler`、`PSZAC_GasCoil`、`PTAC_ElectricBaseboard` 等 |
| `vintage` | 空調效率依據的年份 | `ASHRAE_2019`(預設)、`DOE_Ref_1980_2004` |

## 也可以用 DesignBuilder 的模型

DB 裡 File → Export → EnergyPlus → IDF,把檔案放進 `db_models/inbox/`,執行 `python db_import.py`。
舊版 IDF 會自動升級到 E+ 26.2,中文檔名也可以。

## 這套工具怎麼組成

| 元件 | 角色 |
|---|---|
| [Ladybug Tools](https://www.ladybug.tools/)(dragonfly / honeybee-energy / honeybee-openstudio) | 由參數建出量體、負載、構造、空調,轉成 IDF |
| [OpenStudio SDK](https://openstudio.net/) 3.11 | honeybee 轉 IDF 時使用 |
| [LBNL EnergyPlus-MCP](https://github.com/LBNL-ETA/EnergyPlus-MCP) | 所有模型修改與模擬都透過它的工具 |
| EnergyPlus 26.2 | 模擬引擎 |

腳本:`tools/modeler/build_idf.py`(參數 → IDF)、`db_import.py`(升級 → 補電表 → 跑措施)、
`scan2/scan2.py`(措施、組合、回收期,最多 4 個模擬平行)、`sensitivity.py`(電價 / 成本敏感度網頁)。

## 檢查是不是都還能用

```
python tests/smoke.py
```

把所有重要功能實際跑一次(約 15–20 分鐘):參數建模(長方形 / L 形、三種空調)、參數填錯的提示、
舊版 IDF 升級與中文檔名、5 個範例模型的措施與敏感度網頁、11 個模型批量模擬、eppy 修正。
改過程式、`uv sync` 或升級 EnergyPlus 之後跑一次,全部 PASS 才算正常。

## 安裝(新機器)

- EnergyPlus 26.2、EnergyPlus-MCP 與 Windows 修正:見 `handover/HANDOVER.md` §5。
- 建模工具:

  ```
  uv venv tools/modeler/.venv --python 3.12
  uv pip install --python tools/modeler/.venv/Scripts/python.exe -r tools/modeler/requirements.txt
  ```

腳本裡的 EnergyPlus 與 Python 路徑目前寫死成這台機器的位置(`batch_driver.py` 的 `EP`、
`tools/modeler/build_idf.py` 的 `EP` / `DSH_PY`),換機器要改這幾行。

## 限制

- 只支援方形與 L 形;複雜造型請用 DesignBuilder 建模後匯出。
- 氣象檔固定 Chicago;成本(電價、氣價、貼膜、燈具)是假設值,在 `scan2/scan2.py` 的 `COSTS`。
- 照明 −30%、滲風 ×0.5 是固定比例;貼膜會依原窗調整(只降 SHGC、U 值不變,原窗已經能遮陽就不貼)。

## 授權

Ladybug Tools 套件是 AGPL-3.0,OpenStudio 是 BSD-3。本 repo 只透過 pip 使用它們,不含其原始碼。
