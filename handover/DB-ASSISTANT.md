# 給 Claude：使用者要的是「待在 DesignBuilder 裡的助手」

最後更新：2026-11-05（DSH 實測後重寫）· 對象：Claude · 性質：需求校正 + 事實清單

---

## 0. 一句話

**使用者不是要一套取代 DesignBuilder 的 EnergyPlus 工具鏈。他要一個能待在 DB 裡、替他操作與解讀的助手。**

---

## 1. 使用者原話（需求本體）

- 「我一開始的設想是用 DB 啊，我只是想做個 MCP 方便我用而已」
- 「我的設想就是建模出圖然後在後台跑，不用我自己學怎麼調參數」
- 「你給我輸出文檔嗎，還是說你自己就可以建那些稍微複雜一點的」
- 需要 **CFD、HVAC** 這些 DB 才有的模組，不是只要能耗數字

翻成規格：

| 他要 | 他不要 |
|---|---|
| 在 DB 裡建模、出圖 | 學 EnergyPlus、學參數檔 |
| 模擬在背景跑，不用手動調參數 | 另開一套建模器取代 DB |
| 結果用人話解讀（省多少、幾年回本） | 一堆 CSV 要自己看 |
| CFD、HVAC 設計這些 DB 功能可用 | DB 被降級成「匯出 IDF 的工具」 |

---

## 2. 你（Claude）理解錯的地方

1. **把「MCP 方便操作 DB」做成「MCP 取代 DB」。** 重心搬到 `build_idf.py` / `db_import.py` / `scan2.py`，DB 只剩匯出。
2. **判定「DB 不能模擬」，但沒實測過。** 依據只有「`EnergyPlus\` 資料夾沒有 `energyplus.exe`」，沒有實際按過 Simulation。
3. **因此整份 HANDOVER 的結論與待辦都偏了**（§4.1「DB 的 Simulation 按鈕大概率無法用」要改）。
4. **把「用外部 E+ 跑」當成等價方案。** 對能耗有效，但 CFD、HVAC 設計、DB 的結果頁全部拿不到，正是他要的部分。

---

## 3. 2026-11-05 實測到的事實（都可重現）

**3.1 安裝包裡真的沒有 EnergyPlus**
`DesignBuilder-2025.1.1.011.msi` 的 Feature 表只有：Data、DesignBuilder、JobServer、LicenceServer、VCRedist、SimulationManager。沒有任何 EnergyPlus 元件，重裝也裝不出 `energyplus.exe`。

**3.2 但 DB 用的是 E+ 23.1 的 DLL 版，那個有**
- `C:\ProgramData\DesignBuilder\JobServer\versions\DLL23.1.0.002-64.zip`（17.6 MB）內含 `energyplusapi.dll`、`Energy+.idd`、`RunEpDLL.exe`
- 用 `DBEPGetVersion.exe` 對 DB 匯出的 IDF 解析 → 回 `23.1.0.002`、指向該 zip，exit 0
- 所以「引擎缺失」是誤判：缺的只是 exe 形式，DB 2025 不走那條路

**3.3 DB 內跑模擬不經過 JobServer**
`DBJobServer.db` 的 `job_instances` 在成功跑完一次年度模擬後依然是 **0 筆**。JobServer／Simulation Manager 只服務批次／遠端佇列。

**3.4 模擬結果是「塞進 .dsb 裡」**
跑完一次模擬後，autosave 的 `.dsb` 從 8,142,291 → 8,592,819 bytes。`.dsb` 其實是 ZIP，內含單一 `~<name>.skh`（40.5 MB，自訂二進位格式、非 SQLite）。→ **想直接讀模型／結果，只能靠 DB 自己的 API，硬解不可行。**

**3.5 模擬入口找到了（關鍵）**
- 原因：**Learning mode 開著時，計算標籤會搬到畫面下方**，上方只剩資料標籤（Layout…CFD）
- 關掉 Learning mode（Tools → Program Options）後，標籤變成：
  `Edit | Visualise | Heating design | Cooling design | Simulation | CFD | Daylighting | Cost and Carbon`
- 操作路徑：**Simulation 標籤 → 左上 `Update data` →（缺氣象檔會問，可按確定線上下載）→ 自動算完顯示年度曲線**
- 2026-11-05 16:37 實測成功：下載 `USA_CA_SAN FRANCISCO INTL AP_TMY3.epw`（1,565,352 bytes）後算完，出現「Temperatures, Heat Gains and Energy Consumption」年度圖

**3.6 授權沒問題**
`ProgramData\DesignBuilder\Licences\E59L2828-EP.lic`：`designbuilder_ep`、uncounted、到期 **23-nov-2026**；`DBSystemSettings.txt` 的啟用代碼含 CD, CO, DY, EM, EP, HV, LE, OP, SB, VI（HV=HVAC、DY=Daylighting、OP=Optimisation…）。

---

## 4. 助手能做什麼、不能做什麼（現況）

| 層面 | 現況 |
|---|---|
| 讀 DB 的設定／授權／氣象／引擎快取／JobServer DB | ✅ 可以（純檔案） |
| 讀 `.dsb` 內容（模型與模擬結果） | ❌ 自訂二進位，讀不動 |
| 驅動 DB GUI（點標籤、按 Update data、處理對話框） | ⚠️ 可以（UI Automation + 滑鼠鍵盤），今天就是這樣跑起來的；但脆弱、會動到使用者滑鼠，**必須先取得同意** |
| 監看 DB 跑完沒有 | ⚠️ 只能看畫面／檔案變化，沒有事件可訂閱 |

---

## 5. 建議路線（優先序）

1. **先驗證 DB 的 Scripting / Extensibility 介面。** DB 工具列有 `Scripts` 按鈕，`Lib\` 下有 `DB.Api.dll`、`CScriptLibrary.dll`、`DB.Extensibility.Contracts/Interop/Util.dll`。若它支援「開檔 → 讀資料 → 觸發模擬 → 取結果」，**這才是「DB 助手」的正解**，比 UI 自動化穩得多。這是目前最高價值、也最未被驗證的一條。
2. 沒有可用 API，才退回 UI 自動化（要有明確授權，且做成可重播的腳本，不要每次現找按鈕）。
3. 檔案層能讀的就先讀（授權、氣象、引擎版本、資料夾狀態），至少能回答「為什麼跑不起來」。

---

## 5b. API 調查結果(2026-11-05,Claude;只讀 metadata,沒動 DB)

**結論:DB 的 API 只能在 DB 內部執行(scripts / plugins),沒有外部遙控介面。**
「助手從外面叫 DB 開檔、跑模擬」做不到;「使用者按下模擬後,助手自動接手處理」可以做到。

- `DB.Api.dll`:可以讀寫整個模型(Site → Building → Block → Zone → Surface、HVAC loops / zones / components、CFD 狀態、資料表)。
  `Environment` 有 `DsbFilePath`、`EnergyPlusInputIdfPath`、`EnergyPlusFolder`、`ScriptsFolder`、`PluginsFolder`。
  但是**沒有**「執行模擬」的方法,`Initialise()` 要傳入 DB 自己的 COM 物件(`SKETCHLib.Sketch`)。
- `DB.Extensibility.Contracts`:`ScriptBase` / `PluginBase2` 提供的掛點:
  - 模擬相關:`BeforeEnergyIdfGeneration`、`BeforeEnergySimulation`、`AfterEnergySimulation`,
    Heating / Cooling design、Daylight、CFD、Optimisation、Cost and Carbon 也各有前後兩個掛點。
  - 其他:`ModelLoaded`、`ScreenChanged`、`AtCommandLine`。
- 官方說明([Extensibility](https://designbuilder.co.uk/helpv2025.1/Content/Extensibility.htm)、[Scripting](https://designbuilder.co.uk/scripting)):
  - Script 用 C# 或 Python 寫,用 Tools → Scripts 開啟管理。Python 透過 eppy 改 IDF。
  - Plugin 只能用 C# 寫(編好的 DLL),可以在 Plugins 選單加子選單。
  - 官方範例在 GitHub:DesignBuilderSoftware / db-scripts。
  - 文件沒有提到命令列或無 GUI 的批次模擬。
- 安裝目錄有 `Python34\`,推測 Python script 用的是這個 3.4,**未驗證**。
- 筆誤更正:§5 的 `CScriptLibrary.dll` 實際檔名是 `CSScriptLibrary.dll`。

**可行的「DB 助手」形態**:
1. 寫一支 `AfterEnergySimulation` script:每次在 DB 按 Update data 跑完,自動把 IDF 和 E+ 結果複製到工作區。
   接著跑現有的措施 / 回收期分析,產生白話報告。使用者只要照常在 DB 按一次模擬。
2. 寫一支 `BeforeEnergyIdfGeneration` / `BeforeEnergySimulation` script:在 DB 內直接套用節能措施,結果就留在 DB 的結果頁。
3. 寫一個 C# plugin,在選單加「產生節能報告」按鈕。
4. 一定要「助手自己按模擬」時,才退回 UI 自動化。

**風險**:§3.6 寫「授權沒問題」不完整。HANDOVER §4.1 記載這是 **Evaluation** 授權,**2026-11-23 到期**,剩不到三週。
到期後上面這些路線都會停,要先確認使用者會不會買正式授權。

## 6. 現有工具鏈的定位（不要拆，也別當替代品）

`run_building.py` / `db_import.py` / `scan2` / `sensitivity.py` 這套做的事——**措施組合、省多少錢、幾年回本**——DB 本身沒有，仍然有價值。

正確分工：
- **DB**：建模、出圖、模擬、CFD、HVAC 設計與選型 → 使用者操作，助手在旁邊幫
- **現有工具鏈**：拿 IDF 做「措施效益、回收期、敏感度」這種投資分析

不要再把「匯出 IDF 給外部 E+ 跑」講成「DB 的替代方案」。

---

## 7. 未解問題（交給下一輪）

1. `Scripts` / `DB.Api.dll` 到底能不能自動化：開檔、讀 zone/構造/HVAC、觸發模擬、取逐時結果？
2. DB 內跑出的結果除了圖，能不能匯出 CSV/HTML（給外部核對）？
3. 醫院專案只有官方 IDF、沒有 `.dsb`；要不要在 DB 裡重建模型？
4. Learning mode 以外的介面差異（Standard/Expert 模式標籤會不會不同）。

---

## 8. 使用者版操作備忘（三行）

1. 上方標籤切到 **Simulation**
2. 左上按 **Update data**
3. 第一次會要氣象檔 → 按確定下載；算完自動出年度曲線
