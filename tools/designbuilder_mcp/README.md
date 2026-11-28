# designbuilder-mcp

讓 AI 助理（透過 MCP）或 Dify 表單直接操作 **DesignBuilder 2025.1**：由參數建模、套用途與作息、套構造與空調、換地點、跑 EnergyPlus 年度模擬、讀逐時結果，再寫成白話報告。DesignBuilder 仍然是建模工具，這個專案只是把重複的步驟自動化，模型（`.dsb`）可以隨時用 DB 打開精修。

*English summary: an MCP server and HTTP service that drive DesignBuilder 2025.1 headlessly (parametric massing, activities, schedules, ASHRAE 90.1-2019 baseline constructions, simple HVAC, locations, annual simulation, hourly results), plus a Dify workflow on top. Technical details, findings and limitations: [docs/designbuilder-integration.md](docs/designbuilder-integration.md).*

> 這不是 DesignBuilder 官方專案。需要一台裝好 DesignBuilder 2025.1（含有效授權）的 Windows 電腦。

## 能做什麼

| 類別 | 工具 | 說明 |
|---|---|---|
| 建模 | `db_build_model` | 矩形、L、U、T、回字、裙樓加塔樓；每層切 4.5 m 周邊區加核心區；可給朝向、各立面窗牆比；附分區平面圖網頁 |
| 用途與作息 | `db_assign_activity`、`db_find_schedules`、`db_assign_schedule` | 25 種中文用途對應 DB 的 ASHRAE 90.1 / 62.1-2019 範本；照明功率用 90.1-2019 Table G3.7 |
| 外殼 | `db_assign_construction` | 依氣候區套 ASHRAE 90.1-2019 附錄 G 基準構造；有實際數據時給外牆、屋頂、外挑樓板、外窗的 U 值與外窗 SHGC |
| 空調 | `db_assign_hvac` | 7 種 DB 簡易空調範本；冷熱需求用範本的季節效率換算成實際的電與天然氣。詳細空調請在 DB 裡載入（見下方） |
| 地點 | `db_find_locations`、`db_set_location` | DB 的 6,368 個地點；氣象檔要在本機 |
| 模擬與結果 | `db_simulate`、`db_results` | 年度模擬、全年摘要；逐時、逐熱區結果輸出 CSV |
| 節能措施 | `db_measures_report`、`db_report_status` | 8 種措施的省能與回本（需要 [building-energy-toolchain](https://github.com/Tanna-qne/building-energy-toolchain)） |
| 完整流程 | `db_workflow`、`db_job_start`、`db_job_status` | 一次跑完：建模 → 地點 → 構造 → 空調 → 模擬 → 節能措施，回傳結構化結果 |
| 其他 | `db_status`、`db_inspect`、`db_zones`、`db_calculate`、`db_open`、`db_update_bridge`、`db_create_model` | 檢查授權、讀模型、列熱區、打開模型、更新模型裡的腳本 |

所有數值都來自 DB 的範本庫或 EnergyPlus 的模擬結果；工具每次寫入後都會讀回核對，對不上就在結果裡標出來。

## 安裝

1. Windows、DesignBuilder 2025.1、Python 3.12。
2. 在這個資料夾建環境、裝套件：

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

3. 參數建模（`db_build_model`）另外需要一個裝 dragonfly 與 honeybee-designbuilder 的 Python，用環境變數 `DBMCP_MODELER_PYTHON` 指過去：

```powershell
uv venv .modeler --python 3.12
uv pip install --python .modeler\Scripts\python.exe -r requirements-modeler.txt
$env:DBMCP_MODELER_PYTHON = "$PWD\.modeler\Scripts\python.exe"
```

4. 註冊成 MCP server（以 Claude Code 為例）。要用 Python 的「基底」執行檔，不要用 `.venv\Scripts\python.exe`；套件會自動從 `.venv` 載入：

```powershell
claude mcp add designbuilder -- "C:\Path\To\Python312\python.exe" "C:\Path\To\designbuilder-mcp\server.py"
```

5. **第一次使用要裝一次 DB 腳本**：在 DB 打開任一個模型 → Scripts → Script Manager，把 [DbMcpBridge.cs](DbMcpBridge.cs) 加進 CS-Script 庫、Enable program 設為 Yes、用綠色勾勾選進左邊的 Script 欄、勾選 Enable scripts，存檔後關掉 DB。第一次呼叫 `db_build_model` 時用 `template_dsb` 參數指定這個模型當模板，之後建的新模型都會自動帶上腳本。腳本改版時用 `db_update_bridge` 更新，不用再開 DB。

放在 building-energy-toolchain 的 `tools/designbuilder_mcp` 時，一鍵安裝會處理以上步驟，節能措施也能用。

## 設定（環境變數，都是選填）

| 變數 | 用途 | 預設 |
|---|---|---|
| `DBMCP_WORK` | 模型副本與模擬結果的資料夾 | 在 toolchain 裡是 `db_models\dbmcp`，單獨使用是這裡的 `work\` |
| `DBMCP_TOOLCHAIN` | building-energy-toolchain 的位置（節能措施用） | 上兩層資料夾 |
| `DBMCP_MODELER_PYTHON` | 參數建模用的 Python | toolchain 的 `tools\modeler\.venv` |
| `DBMCP_ALLOWED_DIRS` | HTTP 模式下允許讀寫的其他資料夾（分號分隔） | 只有工作資料夾 |

## Dify 表單應用

`http_server.py` 把同一套工具開成 HTTP 服務，配合 [dify/designbuilder-workflow.yml](dify/designbuilder-workflow.yml)：在 Dify 填表，DB 電腦建模、模擬，DeepSeek 寫白話報告。設定步驟（通道、token、匯入）見 [dify/README.md](dify/README.md)。

## 使用時要知道的事

- **操作的都是副本**：工具把模型複製到工作資料夾再改，不動你原本的 `.dsb`。
- **DB 一次只能開一個**：工具在背景開 DB，執行時不要自己開 DB；工作會排隊依序跑。每次約 1 分鐘起，新建模型的完整流程約 6–8 分鐘。
- **簡易空調**：EnergyPlus 算的是冷熱需求（理想負載），報告用空調範本的季節效率換算成電與天然氣，和 DB 結果畫面同一算法，不含風機與水泵。
- **詳細空調**：在 DB 的 HVAC 頁用 *Load HVAC template* 載入（例如 ASHRAE 附錄 G 的 System No. 3 PSZ-AC），精靈會接好所有熱區；之後的模擬、結果、報告都可以交給工具。API 目前無法自動接熱區，原因見說明頁。
- **外殼數據**：有實際項目數據就填 U 值和 SHGC；沒填的部位用 ASHRAE 附錄 G 基準，不是規範的最低要求值。
- **窗戶**：DB 匯入模型時可能重排窗戶，流程會讀出模擬實際的各立面窗牆比，和指定值差超過 3 個百分點會警告。
- **氣象檔**：地點的氣象檔要已經在 DB 電腦上（在 DB 的地點設定下載），或提供 EPW 檔。

## 授權與致謝

建立在 DesignBuilder 官方的 [db-process](https://github.com/DesignBuilderSoftware/db-process)、Ladybug Tools 的 [dragonfly](https://github.com/ladybug-tools/dragonfly-core) 與 [honeybee-designbuilder](https://github.com/ladybug-tools/honeybee-designbuilder)、[Model Context Protocol](https://modelcontextprotocol.io) 之上。DesignBuilder 範本庫的資料屬於 DesignBuilder Software，這個倉庫不包含任何範本資料，全部在執行時從你本機的 DB 讀取。
