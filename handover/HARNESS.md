# 給 HARNESS(DSH)的交接

你接手的是一套建築能耗模擬工具鏈。使用者不懂 DesignBuilder 和 EnergyPlus。
他給你建築的描述或 DB 匯出的檔案,你負責跑出報告,再用白話告訴他結果。
程式都已經寫好,也測過了。**你的工作是執行和解讀,不是重寫。**

工作區:`C:\Users\kiri\Documents\deepseek-harness\default-workspace`(以下路徑都相對於這裡)

## 1. 先記住這幾件事

- **Python 一律用主 runtime**:`C:\Users\kiri\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`。
  不要用 `tools/modeler/.venv` 或 MCP 的 venv 去跑根目錄的腳本。原因是由 venv 啟動的 E+ Transition 會當掉。
- **PowerShell 先執行** `[Console]::OutputEncoding=[Text.Encoding]::UTF8`,不然中文輸出會變亂碼。
- **模擬很慢**:一棟建築約 3–10 分鐘,多方案會更久,`tests/smoke.py` 約 45 分鐘。
  指令要放到背景執行,或把逾時設長一點。看到沒有輸出,不代表卡住。
- **結果會覆蓋**:每次執行都會覆寫 `db_models/out/`,要保留舊結果就先複製出去。

## 2. 使用者說什麼 → 你做什麼

| 使用者說 | 你做 |
|---|---|
| 「幫我算一棟 40×25、3 層的辦公室」 | 寫一個參數 json(§3)→ `python run_building.py 那個.json` |
| 「比較 VAV 和箱型冷氣」「3 層和 5 層哪個好」 | 每個方案寫一個 json,`name` 不同 → `python run_building.py A.json B.json` |
| 「這是 DB 匯出的檔案」 | 放到一個**新的空資料夾**,然後執行 `python db_import.py 那個資料夾`(見 §5 坑 1) |
| 「改成新台幣」「電價是 0.2」 | 改根目錄的 `costs.json`,再重跑。README 有新台幣的範例值 |
| 「電價漲了還划算嗎」 | 報告裡已經有電價漲 30% 和 80% 的回本年數;要畫整條曲線,就跑 `python sensitivity.py`(只吃範例模型) |
| 「檢查一下還能不能用」 | `python tests/smoke.py`,要全部 PASS。只想測一項,就加上名稱片段,例如 `python tests/smoke.py modeler` |

跑完後,打開 `db_models/out/report.html`(給使用者看)和 `db_models/out/results.csv`(給你讀數字)。

## 3. 參數 json

最少要填 `name`、`width`、`depth`(單位都是 m),其他欄位都有預設值:

```json
{"name": "demo_office", "shape": "rect", "width": 40, "depth": 25, "stories": 3, "floor_height": 3.6,
 "wwr": 0.4, "building_type": "MediumOffice", "climate_zone": 5, "construction": "SteelFramed",
 "hvac": "VAV_Chiller_Boiler", "construction_vintage": "2019"}
```

- **L 形**:用 `"shape": "L"`,再加上 `notch_width`、`notch_depth`(從右上角挖掉的大小)。
- **舊建築改造**:`construction_vintage` 填 `"pre_1980"` 或 `"1980_2004"`。
- **name**:用英文和底線。這個名稱會變成檔名和報告標題。
- **填錯沒關係**:程式會印出一行「參數錯誤: … 要是其中之一: …」。照著改就好,不要去讀原始碼猜選項。
- **常用的 hvac**:
  - `VAV_Chiller_Boiler`:中央空調
  - `PSZAC_GasCoil`:箱型冷氣加瓦斯
  - `PTAC_ElectricBaseboard`:窗型冷氣加電暖器
- **常用的 building_type**:`MediumOffice`、`SmallOffice`、`Retail`、`SmallHotel`、`PrimarySchool`。
- **氣象檔固定是 Chicago**:`climate_zone` 只影響構造的保溫規範,不會換掉天氣。使用者提到別的城市時,要先跟他說明這一點。

## 4. 怎麼向使用者回報

使用者看不懂 EUI 或 COP,回報時照這個順序講:

1. **一句結論**:哪個措施最划算,一年省多少錢,幾年回本。直接用 `report.html` 最上面那句話。
2. **其他值得做的措施**:判讀是「值得考慮」的列出來;「不太划算」的帶過就好。
3. **多方案時**:報告最前面「方案比較」寫的是哪個方案一年能源費最低。
4. **說明假設**:金額是 `costs.json` 裡的假設值,不是報價。DB 的理想負載模型,空調效率也是假設的。

`results.csv` 欄位:

- `case`:措施代號。
  - `baseline`:現況
  - `lights_-30%`:換燈具
  - `film_shgc0.30`:窗戶貼膜
  - `lights+film`:燈具加貼膜
  - `infil_x0.5`:加強氣密
  - `coolroof`:屋頂反射漆
  - `equip_-20%`:換節能設備
  - `insulation`:加強隔熱
  - `hvac_eff`:高效率空調
- `saving_pct`:總能源省幾 %。
- `saving_cost` / `capex_cost` / `payback_years`:一年省多少錢、要花多少錢、幾年回本。
- `error`:**不一定是錯誤**,通常是說明,例如「U 值維持 …」「原窗已經能遮陽,不貼」。
  `success=False` 而且 `error` 寫「不適用」或「幫助不大」,是正常略過。

## 5. 踩過的坑

1. **`python db_import.py` 不帶參數時,會把 `db_models/inbox/` 裡的每個 IDF 都跑一遍**。
   參數建模的結果放在 `db_models/built/`,不會混進 inbox。但 inbox 裡留著的舊 DB 檔還是會重跑,所以跑 DB 檔時一律指定檔案或資料夾。
2. **中文檔名可以**,程式會自動改用 ASCII 工作檔名。但工作區路徑本身不能有中文。
3. **直接呼叫 MCP 工具**(`mcp__energyplus__*`,共 37 個)時:
   - 路徑要用絕對路徑。
   - 欄位名是 eppy 風格,例如 `Watts_per_Floor_Area`。
   - 修改工具會在原檔旁邊另存 `_modified.idf`,所以原檔要放在可寫的目錄。
   - 一般需求不需要直接呼叫 MCP,用上面的腳本就好,腳本內部會呼叫 MCP。
4. **模擬失敗時**:
   - 先看 `logs/mcp-N.log`。
   - 再看輸出目錄裡的 `.err`、`.experr`。
   - MCP 回空白錯誤時,原因通常在 `.experr`。
5. **改過 MCP 的 venv(`uv sync`)之後**,要重跑 `vendor/EnergyPlus-MCP/energyplus-mcp-server` 底下的 `apply_windows_patches.py`。

## 6. 規矩

- **不要改** `~/.dsh/profiles/desktop/cordis.yml`;要改設定只改 `cordis.patch.yml`,新增 MCP 要寫在 `insert:` 底下。
- **不要自己重寫模擬邏輯**。措施、換算、報告都已經有了。少了功能,先問使用者,再找現成的開源專案。
- **push 到 GitHub 前要先問使用者**。`report/` 是公開的 GitHub Pages;工具鏈本身是 private repo。
  commit 時要明確列出檔案,不要 `git add -A`(之前因此把付費素材推上去過)。
- 代理和 DNS 不要動。
- 想進一步了解細節:`README.md`(使用說明)、`handover/HANDOVER.md`(架構、安裝、完整踩坑紀錄)。
