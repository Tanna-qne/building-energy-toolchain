# 交接文件:建築能耗模擬工具鏈

最後更新:2026-11-05(DSH 實測後修正「DB 不能模擬」結論,見 §4.1)· 原交接日 2026-10-27

---

## 0. 一句話總結

在 Windows 上把 **EnergyPlus 26.2 + LBNL 官方 MCP server** 接進 DeepSeek Harness,
並用它在 5–11 個範例模型上跑了三輪模擬(批量、措施掃描、成本回收期),產出一份公開的靜態網頁報告。

**DesignBuilder 可以模擬**(2026-11-05 實測成功,入口見 §4.1)。DB 匯出 IDF 給 `db_import.py`
跑「措施效益與回收期」仍是另一條可用路線(§4.2)。

---

## 1. 目前狀態

| 項目 | 狀態 | 備註 |
|---|---|---|
| EnergyPlus 26.2.0 | ✅ 可用 | 可攜版,免管理員 |
| EnergyPlus-MCP(37 工具) | ✅ 可用 | 已用真實模型驗證 |
| MCP 在 DSH 對話內可用 | ✅ 已驗證 | 2026-10-31:patch 改用 `insert:` 後 35 個(現在 37 個) `mcp__energyplus__*` 都出現,`get_server_status` 正常(§5.3) |
| 批量模擬(11 模型) | ✅ 11/11 成功 | 自動補電表,見 §6.1 |
| 措施掃描(5×4) | ✅ 20/20 成功 | 333 秒,含天然氣 |
| 組合 + 回收期(5×5) | ✅ 25/25 成功 | 平行 4 個 MCP,150 秒 |
| 敏感度 + 網頁報告 | ✅ 已上線 | GitHub Pages |
| DesignBuilder 模擬 | ✅ 可用 | 2026-11-05 實測成功;E+ 23.1 DLL 版,入口 Simulation→Update data(§4.1) |
| DB IDF 匯入(`db_import.py`) | ✅ 已驗證 | 2026-10-31 用 DB 2025.1 實際匯出的 IDF(23.1 版、理想負載)跑通,已列入 `tests/smoke.py` |

---

## 2. 環境

| 項目 | 值 |
|---|---|
| OS | Windows |
| 工作區 | `C:\Users\kiri\Documents\deepseek-harness\default-workspace` |
| EnergyPlus | `C:\Users\kiri\AppData\Local\Programs\EnergyPlusV26-2-0\EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64` |
| Python(DSH runtime) | `C:\Users\kiri\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` |
| uv | `C:\Users\kiri\.local\bin\uv.exe`(0.13.0) |
| GitHub | `gh` CLI 已登入(需要 `repo` 權限) |
| 代理 | 不用。前任留下的本機代理已停用,2026-10-30 已移除 git 全域 `http.proxy` / `https.proxy`,直連 GitHub 可 push |
| git 身分 | 本機沒有設全域 `user.name` / `user.email`;`report/` 的 commit 用 `-c user.name=Tanna-qne -c user.email=Tanna-qne@users.noreply.github.com` |

---

## 3. 系統架構(資料流)

```
IDF 模型 ──> EnergyPlus-MCP(stdio JSON-RPC)──> EnergyPlus 26.2 ──> 輸出目錄
                  │                                                  │
                  │ 37 個工具:load / inspect / modify / run          ├─ *.mtr  電表(能耗)
                  │                                                  ├─ *.eio  面積、區域資訊
                  ▼                                                  ├─ *Meter.csv
  驅動腳本(batch_driver / scan / scan2 / db_import)                  └─ *.htm  報表
                  │
                  ▼
         CSV / Markdown ──> sensitivity.py ──> report/index.html ──> GitHub Pages
```

**重點設計**:所有模型修改與模擬都呼叫 MCP 的工具,驅動腳本只做排程與匯總。
不要另外寫一套直接呼叫 EnergyPlus 的邏輯(之前寫過,已刪,見 §7)。
唯一例外是 IDF 版本升級:MCP 沒有這個工具,`db_import.py` 直接呼叫 E+ 自帶的 Transition 程式。

---

## 4. 兩個外部軟體的狀況

### 4.1 DesignBuilder:可以模擬(2026-11-05 實測)

- 版本 **2025.1.1**(`C:\Program Files (x86)\DesignBuilder`)。
- 授權正常:`Licence (Evaluation) for EP` 有效到 **2026-11-23**,授權檔在 `C:\ProgramData\DesignBuilder\Licences`。
- **引擎是 E+ 23.1 的 DLL 版,不是 exe**:`DesignBuilder\EnergyPlus\` 只有 164 KB 的
  `DesignBuilderEnergyPlus.exe`(模擬管理程式),真正引擎在
  `C:\ProgramData\DesignBuilder\JobServer\versions\DLL23.1.0.002-64.zip`(`energyplusapi.dll`、`Energy+.idd`、`RunEpDLL.exe`)。
  用 `DBEPGetVersion.exe` 對 DB 匯出的 IDF 解析會回 `23.1.0.002`,exit 0。之前以為「缺 energyplus.exe = 不能跑」是誤判。
- **模擬入口**:Learning mode 開著時計算標籤會搬到畫面下方,所以上方只剩資料標籤(Layout…CFD)。
  關掉 Learning mode(Tools → Program Options)後標籤變成
  `Edit | Visualise | Heating design | Cooling design | Simulation | CFD | Daylighting | Cost and Carbon`;
  切到 **Simulation → 左上 `Update data`** → 缺氣象檔會問(可線上下載)→ 自動算完出年度曲線。
  2026-11-05 16:37 用 Model Geometry Example + San Francisco 氣象跑通。
- **結果存在 .dsb 內**:跑完 `DBJobServer.db` 的 `job_instances` 仍是 0(DB 內跑不經 JobServer);
  autosave 的 `.dsb` 從 8.14 MB → 8.59 MB。`.dsb` 是 ZIP,內含單一 `~<name>.skh`(自訂二進位,外部讀不動)。
- 需求校正與「DB 助手」路線見 `handover/DB-ASSISTANT.md`。

### 4.2 另一條路:DB 匯出 IDF,交給外部工具做「措施效益與回收期」(投資分析用)

DB 建模 → **匯出 IDF**(工具列的 **Export IDF model** 按鈕;File → Export 選單裡沒有 IDF。不需要引擎,
預設存到 `文件\DesignBuilder Data\`)→ 放進 `db_models\inbox\` →
`python db_import.py`。結果在 `db_models/out/report.md`,中間檔在 `db_models/work/`。

`db_import.py` 依序做:

1. 複製到 `db_models/work/`;非 ASCII 檔名改用 ASCII 工作名(E+ 讀不到中文路徑),報告仍顯示原名。
2. 讀 `Version`,用 `<E+>/PreProcess/IDFVersionUpdater/Transition-*.exe` 一路升到 26.2
   (DB 2025.1 對應 E+ 23.1;9.0 以前的版本沒有升級程式,會跳過並說明)。
3. MCP `modify_simulation_control`:關掉 sizing period 模擬(否則電表 CSV 多出設計日的列,加總會灌水)。
4. MCP `add_output_meters`:補 `Electricity:Facility`、`NaturalGas:Facility`(RunPeriod)。
5. 交給 `scan2.scan_parallel()` 跑基準 + 全部措施。

DB 自帶 20 多個範例模型在 `C:\ProgramData\DesignBuilder\Templates\*.dsb`,不用自己建模就能測。
**已用真正 DB 匯出的檔驗證**(Model Geometry Example 範例,`tests/fixtures/designbuilder_v23_1.idf`):
升級 23.1 → 26.2 共 7 步都成功。DB 檔的三個特點,流程已處理:

- 評估版會輸出「壓縮格式」IDF(檔頭有說明),E+ 可以正常讀。
- 只開了少數報表、而且是英制單位,沒有 SI 的外殼摘要 → 窗戶性質改讀 `.eio` 的 `WindowConstruction`(數值與報表相同,已比對)。
- 空調是 `ZoneHVAC:IdealLoadsAirSystem`,冷 / 熱記在 `DistrictCooling` / `DistrictHeatingWater` 電表 →
  `scan2.run()` 以 COP 3 / 鍋爐 85% 換成電與天然氣,報告開頭會註明。

---

## 5. 安裝與設定(可重建)

### 5.1 EnergyPlus

下載 GitHub release `v26.2.0` 的 `EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64.zip`,
解壓到 `C:\Users\kiri\AppData\Local\Programs\EnergyPlusV26-2-0\`。
驗證:`energyplus.exe --version` → `EnergyPlus, Version 26.2.0-4bd7a1f26f`。

注意:它自帶的 `WeatherData` 只有 5 個美國城市(Chicago / Tampa / San Francisco / Golden / Sterling)。
要別的城市,去 climate.onebuilding.org 下載 EPW 放進 `WeatherData`。

### 5.2 EnergyPlus-MCP

```
git clone --depth 1 https://github.com/LBNL-ETA/EnergyPlus-MCP.git vendor/EnergyPlus-MCP
cd vendor/EnergyPlus-MCP/energyplus-mcp-server
uv sync --extra dev
```

然後套用 Windows 修正(共 3 處,詳見 `vendor/EnergyPlus-MCP/LOCAL-SETUP.md`):

| # | 位置 | 修正 | 原因 |
|---|---|---|---|
| 1 | `config.py` `PathConfig` | `/workspace`、`/tmp` → repo 目錄與 `%TEMP%` | 否則輸出到 `\workspace` 得 `WinError 5` |
| 2 | `config.py` 執行檔 | Windows 補 `.exe` | 否則找不到 `energyplus.exe` |
| 3 | `eppy/runner/run_functions.py` | 4 個 `check_call(...)` 都加 `stdin=subprocess.DEVNULL` | **關鍵**:子程序繼承 MCP 的 stdin,遇 ExpandObjects 會永久卡死 |

`server.py` 沒有改:`expandobjects` 預設本來就是 `True`(舊版文件誤列為修改)。

第 1、2 項的 diff 在 `handover/patches/energyplus-mcp.patch`(UTF-8,可直接 `git apply`)。
patch 另外包含三個小修:版本號從 IDD 讀(原本寫死 26.1.0)、temp 資料夾改到 repo 內的 `temp/`、
`get_server_status` 的 `startup_time` 改成真正的啟動時間(原本是呼叫當下的時間,看不出 server 是不是還在跑舊程式)。
**改了 vendor 的程式後,DSH 要重新載入 MCP(或重開 DSH),舊的 server 程序不會自己更新。**
第 3 項在 `.venv` 裡,`uv sync` 會蓋掉。重建後跑(會用括號配對找出每個 `check_call`,重跑無害):

```
cd vendor/EnergyPlus-MCP/energyplus-mcp-server
.venv/Scripts/python.exe apply_windows_patches.py
```

另外把 `<E+>/DataSets/Window5DataFile.dat` 複製到
`vendor/EnergyPlus-MCP/energyplus-mcp-server/datasets/`(部分模型用相對路徑引用)。

### 5.3 接進 DeepSeek Harness

設定檔:`C:\Users\kiri\.dsh\profiles\desktop\cordis.patch.yml`(**不要改 `cordis.yml` 本體**)。

**新增 MCP 一定要包在 `insert:` 底下。** patch 檔是「以 id 為目標的覆寫層」:直接寫 `- id: mcp-energyplus`
會被當成「修改既有的 mcp-energyplus」,找不到就警告後略過(舊版文件就是這樣寫,導致四個 MCP 全部沒載入)。
可以用 `dsh --dump-config` 檢查:出現 `entry "…" not found` 就是寫錯了。

```yaml
- insert:
    - id: mcp-energyplus
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: energyplus
        transport: stdio
        command: "C:/Users/kiri/Documents/deepseek-harness/default-workspace/vendor/EnergyPlus-MCP/energyplus-mcp-server/.venv/Scripts/python.exe"
        args: [ "-m", "energyplus_mcp_server.server" ]
        cwd: "C:/Users/kiri/Documents/deepseek-harness/default-workspace/vendor/EnergyPlus-MCP/energyplus-mcp-server"
        env:
          EPLUS_IDD_PATH: "C:/Users/kiri/AppData/Local/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64/Energy+.idd"
```

patch 層會熱重載,存檔後不用重開 DSH;工具以 `mcp__energyplus__` 前綴出現(2026-10-31 實測 35 個)。
改之前的版本存在 `cordis.patch.yml.bak-before-insert`。

---

## 6. 已驗證的結果

### 6.1 批量模擬(`batch/`,11 模型 / 156 秒 / 11 成功)

5ZoneAirCooled 43,510 kWh、5ZoneAirCooledConvCoef 43,822、
5ZoneAirCooledDemandLimiting 39,108、HVACTemplate-5ZoneVAVFanPowered 55,255、
HVACTemplate-5ZoneBaseboardHeat 35,865、1ZoneDataCenterCRAC 879,408 等。
原本有 3 個模型(`1ZoneEvapCooler_4Win…`、`1ZoneUncontrolled`、`…FixedRateVentilation`)沒輸出電表,
成功卻讀不到用電;現在先用 MCP `add_output_meters` 補上(副本在 `batch/models/`),11 個都有數字。
**排除**:`5ZoneAirCooledWithSlab`(GroundHeatTransfer 展開後仍缺 `OtherSideCoefficients`,模型特例)。

**2026-10-30 起,所有「省多少」都以總能源(電 + 天然氣)計。** 舊版只看電:範例模型用燃氣鍋爐暖房,
照明與貼膜會讓暖房多燒天然氣,只看電會高估效益;滲風省的是天然氣,只看電會以為沒效果。

### 6.2 措施掃描(`scan/`,5 模型 × 4 情境 / 333 秒)

總能源相對基準(括號內是只看用電):

| 模型 | 貼膜 SHGC 0.30 | 照明 −30% | 滲風 ×0.5 |
|---|---|---|---|
| 5ZoneAirCooled | −0.2%(−2.9%) | −10.0%(−16.9%) | −1.5%(+0.0%) |
| 5ZoneAirCooledConvCoef | −0.0%(−2.0%) | −10.4%(−16.6%) | −0.5%(−0.0%) |
| 5ZoneAirCooledDemandLimiting | −3.1%(−1.0%) | −2.8%(−2.2%) | −0.5%(−0.0%) |
| HVACTemplate-5ZoneVAVFanPowered | −2.0%(−5.0%) | −10.5%(−14.5%) | −0.2%(−0.2%) |
| HVACTemplate-5ZoneBaseboardHeat | +0.1%(−0.3%) | −18.9%(−19.8%) | −0.3%(+0.0%) |

DemandLimiting 照明省得少不是 bug:照明確實改到了(16 → 11.2 W/m²),是模型的需量控制白天本來就會壓低照明。

### 6.3 組合與回收期(`scan2/`,25 次模擬 / 平行 150 秒)

成本假設(集中在 `scan2.py` 的 `COSTS`,`sensitivity.py` 也讀這裡):電價 4.5 TWD/kWh、
天然氣 1.5 TWD/kWh、貼膜 1,200 TWD/m² 玻璃、照明 600 TWD/m² 樓地板。
面積從 `.eio` 自動取;樓地板只算有照明的 Zone(5 區模型 463.6 m²,舊版誤把 plenum 算進去成 927.2 m²)。
貼膜從基準報表讀原窗 U / SHGC / VT,只把 SHGC 降到 0.30、U 值維持;原窗 SHGC ≤ 0.35 就不貼(舊版固定 U = 3.0,會把好窗換成差窗)。

| 模型 | 照明 | 貼膜 | 組合 | 組合回收期 | 滲風 |
|---|---|---|---|---|---|
| 5ZoneAirCooled | −10.0% | +0.5% | −10.7% | 9.8 年 | −1.5% |
| 5ZoneAirCooledConvCoef | −10.4% | +0.2% | −10.1% | 10.3 年 | −0.5% |
| 5ZoneAirCooledDemandLimiting | −2.8% | −2.6% | −5.4% | 45.8 年 | −0.5% |
| HVACTemplate-5ZoneVAVFanPowered | −10.5% | −1.7% | −11.8% | 7.9 年 | −0.2% |
| HVACTemplate-5ZoneBaseboardHeat | −18.9% | +0.2% | −18.6% | 11.0 年 | −0.3% |

### 6.4 敏感度與網頁(`report/`)

電價 2 / 3 / 4.5 / 6 / 8 / 10 / 12 TWD/kWh × 成本倍數 0.5 / 0.75 / 1 / 1.5 / 2(天然氣價固定)。
因為有天然氣項,回收期對電價不再是純雙曲線:電價翻倍時,回收期砍得比一半更多。
最快:VAV 機型貼膜,4.5 TWD 時 7.4 年;最慢:Baseboard 貼膜 396 年。滲風沒有成本假設,不列入。

### 6.5 氣候對照(`db-energy-case/case_study`,5ZoneAirCooled,463.6 m²)

EUI 以電 + 天然氣計。舊版標成「miami」的兩組實際用的是 Tampa 氣象檔,已改名。

| 情境 | EUI kWh/m² | 用電 kWh | 天然氣 kWh |
|---|---|---|---|
| Chicago 基準 | 134.9 | 43,510 | 19,018 |
| Tampa | 122.0 | 51,585 | 4,967 |
| Chicago + 保溫 ×3 | 127.0 | 42,696 | 16,168 |
| Chicago + 冷氣設定 24→25.5°C | 125.4 | 41,105 | 17,017 |

舊版只看電,結論是 Tampa 比 Chicago 耗能;算進天然氣後反過來,Tampa 的 EUI 較低。

### 6.6 GitHub Pages

Repo https://github.com/Tanna-qne/building-energy-sensitivity(public)
站點 https://tanna-qne.github.io/building-energy-sensitivity/
Pages 狀態 `built`,已實測回 200。
舊的自訂網域 `hedonotlikeme.com` 已過期,2026-10-30 已從使用者網站 repo 刪除 CNAME,不再使用。

---

## 7. 程式碼清單

| 檔案 | 用途 |
|---|---|
| `batch_driver.py` | MCP 的最小 stdio 客戶端(`Mcp` 類)+`meter_kwh()` 電表解析;被其他腳本 import。server 的 stderr 寫到 `logs/mcp-N.log`,server 掛掉會立刻報錯 |
| `scan/scan.py` | 措施掃描(照明/貼膜/滲風),含 `lighting_scaled_updates()`;功能已被 `scan2` 涵蓋 |
| `scan2/scan2.py` | 全部措施 + 組合 + 成本/回收期;`scan_parallel()` 平行跑、`areas()` 從 `.eio` 取面積、`apply()` 檢查修改工具是否成功 |
| `db_import.py` | DB 匯出的 IDF → 升版 → 補電表 → `scan_parallel()`(§4.2) |
| `run_building.py` | **一般人用的入口**:`python run_building.py 參數.json` → 呼叫 `build_idf.py` 建模 → `db_import` 跑全部措施 → `db_models/out/report.html` |
| `tools/modeler/build_idf.py` | 參數 JSON(尺寸 / 樓層 / 窗牆比 / 用途 / 空調)→ Ladybug Tools + OpenStudio 建模 → IDF(放進 `db_models/built/`,參數另存同名 `.json`)。用自己的 venv(`tools/modeler/.venv`) |
| `tests/smoke.py` | 端到端檢查,9 項(含多方案比較、DesignBuilder 匯出檔):建模(長方形 / L 形、VAV / PSZ / PTAC)、錯誤提示、DB 匯入升級與中文檔名、scan2 + 敏感度、批量、eppy patch。已知數字(辦公大樓 EUI 150.5、範例 43,510.1 / 19,018.4 kWh)當回歸基準。範例檔在 `tests/fixtures/` |
| `building_report.py` | 把結果寫成白話網頁:一句結論、一年能源與費用、每個措施省多少錢的長條圖、「值得考慮 / 看情況 / 不太划算」判讀。`scan2.write_outputs()` 自動呼叫 |
| `sensitivity.py` | 電價/成本敏感度 → 產生 `report/index.html` 與 `sensitivity.csv` |
| `db-energy-case/dbcase.py` | 早期的獨立分析工具:讀 `eplusout.csv`/`eplusmtr.csv` 算 EUI 與分項(不經 MCP);已被 `db_import.py` 取代 |
| `vendor/EnergyPlus-MCP/apply_windows_patches.py` | 重套 eppy 的 stdin 修正(放在 `energyplus-mcp-server/` 下跑) |
| `report/index.html` | 可托管的單檔報告(內嵌 SVG) |

所有腳本的路徑都以腳本所在位置為基準,從哪個目錄執行都可以。

**已刪除**:`bldg-tool/`(自己手寫的參數化建模 + MCP)。原因:使用者要求優先整合現成專案,
不要自己重造;官方 MCP 已涵蓋載入/修改/模擬。

---

## 8. 踩過的坑(接手時務必知道)

1. **eppy 子程序繼承 stdin** → 遇 ExpandObjects 永久卡死。修法見 §5.2 第 3 項。
2. **MCP 的欄位命名是 eppy 風格**(例:`Lighting_Level`、`Watts_per_Floor_Area`),
   不是 epJSON 的 snake_case。用錯會回 `Invalid field name`。
3. **修改工具會在來源檔旁邊寫新檔**(<`name`>_modified.idf)。若來源在唯讀目錄
   (例如 EnergyPlus 安裝目錄的 ExampleFiles)會 `WinError 13`。要先把模型複製到可寫目錄。
4. **Window5 模型**需要 `<cwd>/datasets/Window5DataFile.dat`,否則 Fatal。
5. **GroundHeatTransfer 模型**必須 `expandobjects=True`;但即使展開,
   `5ZoneAirCooledWithSlab` 仍缺 `OtherSideCoefficients`,屬模型問題。
6. **Windows 主控台是 GBK**:Python 印中文/²/emoji 會 `UnicodeEncodeError`。腳本開頭要
   `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`,PowerShell 端設 `[Console]::OutputEncoding`。
7. **`uv sync` 會蓋掉 eppy patch**,要重跑 `apply_windows_patches.py`。
8. **DSH 新增 MCP 要寫在 `insert:` 底下**,直接寫 `- id:` 會被當成覆寫不存在的條目而略過(§5.3)。patch 會熱重載,不用重開。
9. **`edit` 工具要求先 `read` 過該檔**;檔案被外部改過會要求重讀。
10. **E+ 讀不到非 ASCII 路徑**:中文檔名會 `File not found`,而且 E+ 回傳的亂碼讓 MCP 送不出回應,
    client 會卡到逾時。`db_import.py` 已自動改用 ASCII 工作檔名;工作區路徑本身也要是 ASCII。
11. **傳給 MCP 的路徑要用絕對路徑**:server 的 cwd 是 `energyplus-mcp-server/`,相對路徑會找不到。
12. **plenum 在 `.eio` 裡也標成建築面積的一部分**(E+ 的 Net Conditioned Building Area 也含它),
    算照明投資或 EUI 時要排除,`scan2.areas()` 只算有 Lights 的 Zone。
13. **範例模型用燃氣鍋爐暖房**,只看 `Electricity:Facility` 會算錯省能;一律加上 `NaturalGas:Facility`。
14. **平行跑時每個 MCP server 是獨立程序**,同一模型的措施仍要依序(組合要用照明的成果)。
15. **26.2 的 ExpandObjects 用舊欄位定義**,遇到新版物件(如 40 欄的 `Chiller:Electric:EIR`)會 SEVERE 失敗,
    MCP 只回空白錯誤(看輸出目錄的 `.experr`)。`batch_driver.sim_args()` 只在有 HVACTemplate / GroundHeatTransfer 時才展開。
16. **dragonfly 的 `set_all_room_2d_hvac` 預設 `conditioned_only=True`**,新建的 zone 會全被跳過、模型沒有空調
    (症狀:天然氣 0、只有照明措施有效果)。`build_idf.py` 已傳 `conditioned_only=False`。
16b. **honeybee 的空調 `vintage` 不會改變設備效率**:換年代產生的 IDF 除了物件順序完全一樣(實測)。
    高效率空調改由 `build_idf.upgrade_hvac()` 用 OpenStudio SDK 直接改 COP / 鍋爐 / 風扇效率。
17. **Ladybug Tools 會在 `HOME` / `APPDATA` 建資料夾**,sandbox 下會 `WinError 5`;`build_idf.py` 把兩者指到 `tools/modeler/home/`。
18. **由 `tools/modeler/.venv` 的 Python 帶起的 E+ Transition 程式會當掉**(gfortran backtrace,環境變數與 PATH 完全相同也一樣,
    在 Claude 的 sandbox 下觀察到)。所以 `run_building.py` 用主 Python 當父程序:先呼叫 venv 建模、建完結束,再自己跑 `db_import`。

---

## 9. 待辦(依優先序)

1. ~~用真正 DB 匯出的 IDF 跑 `db_import.py`~~:2026-10-31 完成。
2. ~~DSH 對話內沒有載入任何 MCP~~:2026-10-31 解決,原因是 patch 沒用 `insert:`(§5.3)。
3. ~~修 DB 模擬~~:2026-11-05 解決,DB 本來就能模擬,入口是 Simulation→Update data(§4.1)。
4. **真實空調系統**:目前掃描用的是範例模型自帶系統;若要算電費與碳排,
   應換成 PTAC/VAV 等真實系統並加電價、碳排係數。
5. **版本對齊**(低優先):MCP 上游預設 E+ 26.1,本機是 26.2,目前沒問題但屬未驗證區。
6. **報告自動化**:目前 `sensitivity.py` 手動跑,可考慮 CI(GitHub Actions)自動重建 Pages。
7. **`db_import.py` 加 `--weather`**:目前氣象檔寫死 Chicago(`batch_driver.WEATHER`)。
8. **清理**:`scan/scan.py`、`db-energy-case/dbcase.py` 已被取代,確認後可刪。
9. ~~DesignBuilder 模型的加強隔熱 / 高效率空調~~:2026-11-01 完成,MCP 新增兩個工具(見 §15)。

---

## 10. 給接手者的規矩

- 工作區有 `AGENTS.md`:回覆用繁體中文、先結論後理由、簡短、不要免責聲明。
- **不要改** `~/.dsh/profiles/desktop/cordis.yml`,只改 `cordis.patch.yml`。
- **不要**自行重寫模擬邏輯,優先用 MCP 既有工具;要新增能力先找現成開源專案。
- 動到網路設定(代理/DNS)前先確認,會影響使用者的日常連線。
- 發布到使用者的 GitHub 前要明確確認(公開可見)。

---

## 11. 檔案索引(絕對路徑)

```
C:\Users\kiri\Documents\deepseek-harness\default-workspace\
├─ batch_driver.py
├─ sensitivity.py
├─ db_import.py
├─ scan\scan.py
├─ scan2\scan2.py
├─ db-energy-case\dbcase.py
├─ db-energy-case\case_study\results.md
├─ db_models\inbox\                  (DB 匯出的 IDF 放這裡)
├─ db_models\built\                  (參數建模產生的 IDF 與變體;和 inbox 分開,免得 db_import 不帶參數時全部重跑)
├─ db_models\work\ · out\            (db_import 的中間檔與結果)
├─ logs\mcp-N.log                    (各 MCP server 的 stderr)
├─ batch\report.md · results.csv · models\
├─ scan\report.md · results.csv
├─ scan2\report.md · results.csv
├─ report\index.html · sensitivity.csv · results.csv   (GitHub Pages repo)
└─ vendor\EnergyPlus-MCP\            (upstream clone,改 config.py + .venv 裡的 eppy)
   ├─ LOCAL-SETUP.md
   ├─ apply_windows_patches.py
   └─ energyplus-mcp-server\
      ├─ apply_windows_patches.py
      ├─ datasets\Window5DataFile.dat
      └─ sample_files\ (5ZoneAirCooled.idf、HVACTemplate-5ZoneVAVFanPowered.idf、batch\ 11 個模型)
```
---

## 12. 附錄 A:MCP 的 37 個工具

```
copy_file, load_idf_model, get_model_summary, check_simulation_settings,
inspect_schedules, inspect_people, modify_people, inspect_lights, modify_lights,
inspect_electric_equipment, modify_electric_equipment, modify_simulation_control,
modify_run_period, change_infiltration_by_mult, add_window_film_outside,
add_coating_outside, list_zones, get_surfaces, get_materials, validate_idf,
get_output_variables, get_output_meters, add_output_variables, add_output_meters,
list_available_files, get_server_configuration, get_server_status,
discover_hvac_loops, get_loop_topology, visualize_loop_diagram,
run_energyplus_simulation, create_interactive_plot, get_server_logs,
get_error_logs, clear_logs, upgrade_insulation, upgrade_hvac_efficiency
```

實務上最常用:`load_idf_model`、`inspect_lights`、`modify_lights`、
`change_infiltration_by_mult`、`add_window_film_outside`、`run_energyplus_simulation`。

參數形狀(節錄):

```json
{"idf_path": "...", "weather_file": "...", "annual": true,
 "readvars": true, "expandobjects": true}
```

---

## 13. 附錄 B:常用指令

Python 一律用:
`C:\Users\kiri\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`

```
python batch_driver.py           # 11 個模型批量模擬 -> batch/
python scan/scan.py              # 措施掃描 -> scan/
python scan2/scan2.py            # 全部措施 + 組合與回收期(平行) -> scan2/
python sensitivity.py            # 敏感度 -> report/index.html
python db_import.py              # db_models/inbox/ 裡 DB 匯出的 IDF -> db_models/out/
python handover/md2html.py handover/HANDOVER.md handover/HANDOVER.html   # 文件轉檔
```

PowerShell 端建議先:
```
[Console]::OutputEncoding=[Text.Encoding]::UTF8
```

PDF 產生(舊版 headless 才穩):
```
msedge.exe --headless --disable-gpu --no-sandbox --print-to-pdf="out.pdf" "file:///.../HANDOVER.html"
```

---

## 14. 附錄 C:交接包內容

```
handover/
├─ HANDOVER.md          本文件(純文字版)
├─ HANDOVER.pdf         PDF 版
├─ md2html.py           文件轉檔工具
├─ code/                所有腳本副本
├─ patches/             vendor 專案的 git diff
└─ results/             batch / scan / scan2 的報告與 CSV、網站 index.html
```

---

## 15. 變更紀錄(2026-10-29 ~ 10-30,Claude)

**算錯的地方(會影響結論)**

- plenum 被算進樓地板面積,照明投資與 EUI 分母多一倍 → 只算有 Lights 的 Zone。
- 只算電、沒算天然氣 → 全部改成電 + 天然氣;回收期用電費 + 氣費。
- `scan2` 不檢查修改工具是否成功,失敗時會拿上一輪的舊檔跑 → `apply()` 先刪舊檔、確認成功才模擬;照明失敗時組合也標失敗。
- case study 的「miami」實際是 Tampa,面積用了含 plenum 的 927.2 m² → 改名、改 463.6 m²、EUI 含天然氣。

**會讓流程壞掉的地方**

- `patches/energyplus-mcp.patch` 是 UTF-16,`git apply` 會失敗,還夾帶被改寫的範例 IDF → 重新產生為 UTF-8,只含 `config.py`。
- 天然氣價在 `scan2.py` 與 `sensitivity.py` 各寫一份 → `sensitivity.py` 改讀 `COSTS`;頁面的模型數 / 模擬次數改從結果檔算。
- 所有腳本用相對路徑,不在工作區根目錄執行時會默默跳過 → 改以 `__file__` 為基準。
- MCP server 掛掉要等 900 秒、看不到錯誤 → 偵測程序結束立即報錯,stderr 寫到 `logs/`。
- `batch_driver` 有 3 個模型沒有電表 → 先用 MCP 補電表。
- 中文檔名讓 E+ 失敗、MCP 卡住 → `db_import.py` 改用 ASCII 工作檔名。
- `apply_windows_patches.py` 只認特定寫法、漏掉 `--version` 那處、插 import 的位置會造成語法錯誤 → 改用括號配對,4 處都補。
- `md2html.py` 表格每列都變表頭、清單不會關閉 → 修正。

**新增**

- `db_import.py`:DB 匯出 IDF 的匯入流程(§4.2)。
- `scan2.scan_parallel()`:最多 4 個 MCP server 平行跑,25 次模擬約 150 秒(原本 20 次約 330 秒)。
- `scan2` 加入滲風措施,一次跑完全部措施。
- 移除 git 全域代理設定(前任留下的本機代理已停用)。
- `tools/modeler/build_idf.py`:不用 DB,輸入參數就建模(Ladybug Tools + OpenStudio 3.11,方形 / L 形)。
  實測 3 層 40×25 m 辦公(VAV + 冰水主機 + 鍋爐):EUI 146.9 kWh/m²(之後把熱水算進去變 150.5),5 個情境約 1 分鐘。
- 整個工具鏈放到 private repo `Tanna-qne/building-energy-toolchain`(`report/` 與 `vendor/` 不在裡面)。
- `run_building.py` + `building_report.py`:一個指令從參數到白話網頁報告。
- 貼膜改成依原窗調整(只降 SHGC、U 值不變;原窗已能遮陽就不貼)。範例辦公大樓從「多耗 7.7%」變成「省 0.8%」。
- `batch_driver.sim_args()`:只在需要時才跑 ExpandObjects。
- `tests/smoke.py`:端到端檢查,2026-10-30 全部通過(約 20 分鐘);10-31 加入 DesignBuilder 實際匯出檔的項目。
- DesignBuilder 匯出檔跑通:窗戶性質改讀 `.eio`(DB 報表是英制)、理想負載換算成電與天然氣。
- 新增兩種措施:屋頂反射漆(`add_coating_outside`,原本已淺色就不做)、節能設備 −20%(`modify_electric_equipment`)。
- 修上游 MCP bug:`add_coating_outside` 直接 extend `idf.idfobjects[...]`,把 Material:NoMass 重複寫進模型(Severe)。
- 區域供冷 / 供熱一律換算(包含 honeybee 模型的熱水),辦公大樓 EUI 146.9 → 150.5。
- 多方案比較:`run_building.py` 可一次給多個參數檔,報告最前面並排比較(一年費用、每 m² 費用、EUI、最划算措施)。
- 貨幣與價格集中在根目錄 `costs.json`(預設 USD);結果欄位改成 `saving_cost` / `capex_cost` / `currency`,敏感度網頁的電價改用基準的倍數。
- 加強隔熱、高效率空調(只限參數模型):`build_idf.py` 另建變體(`<name>.variants/`),外牆與屋頂換較新 / 較冷氣候區的構造;
  空調用 OpenStudio SDK 改效率。成本在 `costs.json` 的 `insulation_per_m2_envelope` / `hvac_per_m2_floor`。
- MCP 新增兩個工具(本機,不是上游;37 個工具):`upgrade_insulation`(外牆 / 屋頂構造加一層保溫到目標 U,只換外部表面的構造)、
  `upgrade_hvac_efficiency`(冰水主機 / DX 冷氣 COP ×1.25、鍋爐與瓦斯加熱器 95%、風扇 0.7)。邏輯在 `utils/retrofit.py`,
  參考 NREL BCL 的 Increase R-value / Set COP 措施。DB 與範例模型用它們;理想負載模型的空調改用換算效率。
- MCP `config.py`:版本號從 IDD 讀(原本寫死 26.1.0);temp 資料夾改到 repo 內的 `temp/`(`%TEMP%` 底下在 DSH 與 sandbox 都建不了)。
- DSH:`cordis.patch.yml` 的四個 MCP 改寫在 `insert:` 底下(DSH 端完成),對話內 46 個 MCP 工具都已載入。
- 給 DSH 的交接:`handover/HARNESS.md`(使用者需求 → 指令、參數、怎麼回報),`AGENTS.md` 指向它。
- `build_idf.validate()`:欄位拼錯(原本會被默默忽略)、L 形缺尺寸、型別或範圍錯、非英文 name、JSON 格式錯,都改成一行說明。
- 參數建模的 IDF 改放 `db_models/built/`,`inbox/` 只留 DB 匯出檔(原本 `db_import.py` 不帶參數會把所有建過的模型重跑)。
- 報告:回本超過 50 年改印「超過 50 年」;天然氣標示改成「暖氣、熱水」(電暖器建築的天然氣其實是熱水)。

**驗證方式**:重構後 `scan2` 依序與平行兩種跑法,和修改前逐筆比對結果一致;`db_import` 用 25.1 舊版 IDF
與中文檔名實測,數字和 26.2 範例完全相同。各檔修改前的版本留在旁邊的 `*.bak-20261029`
(`report/` 的在 `_bak-20261029/report/`)。
