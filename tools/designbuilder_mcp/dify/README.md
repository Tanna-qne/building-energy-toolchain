# Dify 工作流:建築能耗分析(DesignBuilder + DeepSeek)

在 Dify 填一張表,DesignBuilder(DB)在你的電腦上建模、模擬、跑節能措施,DeepSeek 把結果寫成白話報告。

```
Dify(雲端)── 表單 → HTTP ──→ 通道(https)──→ 你的電腦:http_server.py ──→ DesignBuilder / EnergyPlus
         ←── DeepSeek 寫報告 ←── 結構化結果(JSON)←──────────────────────┘
```

- 數值全部來自 DB 範本庫與 EnergyPlus 模擬;DeepSeek 只寫報告,提示詞要求它只能用結果裡的數字。
- 兩種用法:**新建模型**(填量體、各層用途、地點)或 **分析既有模型**(填 `.dsb` 路徑)。
- 時間:新建模型約 6–8 分鐘,加節能措施再 5–10 分鐘。Dify 端每 45 秒問一次進度,最多等約 34 分鐘。

## 1. 在 DB 電腦上啟動服務

在這個倉庫的資料夾裡執行(放在 building-energy-toolchain 裡時是 `tools\designbuilder_mcp`):

```powershell
python http_server.py --port 8765 --public-host <你的通道網址,例如 db.example.com>
```

- 只聽 `127.0.0.1`,區網和網際網路都連不到,要經過下一步的通道。
- **工作資料夾**:放在 building-energy-toolchain 裡時是 `db_models\dbmcp\`,單獨使用時是這裡的 `work\`(或用環境變數 `DBMCP_WORK` 指定)。下面用 `<工作資料夾>` 代表。
- 第一次啟動會產生 token,存在 `<工作資料夾>\_http_token.txt`(不會進 git)。Dify 要用這個 token。
- 既有模型要放在工作資料夾底下,或啟動前把資料夾加進 `DBMCP_ALLOWED_DIRS`(分號分隔),例如:

```powershell
$env:DBMCP_ALLOWED_DIRS = "D:\Projects\models"; python http_server.py --port 8765
```

- DB 一次只能開一個;服務跑的時候不要自己開 DB 做別的事,工作會排隊依序執行。

## 2. 建 https 通道(Dify Cloud 連不到你的電腦)

最簡單是 Cloudflare 的臨時通道(不用帳號,但每次啟動網址都不一樣):

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

畫面會印出一個 `https://xxxx.trycloudflare.com`,這就是 Dify 要填的網址。`cloudflared` 要自己從 Cloudflare 官網下載安裝。

**要固定網址(建議)**:買一個網域,把網域的 Nameserver 改成 Cloudflare 的,在 Cloudflare 後台建「具名通道」(Networks → Tunnels),
照它給的指令 `cloudflared.exe service install <通道金鑰>` 裝成 Windows 服務(開機自動啟動),再在通道的 Public Hostname
新增一筆:子網域(例如 `db`)、你的網域、Service = `HTTP`、URL = `127.0.0.1:8765`。不需要另外買伺服器,DB 電腦就是伺服器。
通道金鑰等同密碼,不要貼到任何地方;外洩了就在後台按 Refresh token 換掉。

通道等於把服務放上網際網路:token 是唯一的門鎖,不要貼到公開的地方;不用時關掉通道。

## 3. 在 Dify 匯入工作流

1. Dify → 外掛(Plugins)→ 安裝 **DeepSeek** 模型供應商,填你的 DeepSeek API key。
2. 工作室 → 匯入 DSL 檔 → 選 `dify/designbuilder-workflow.yml`。
3. 打開工作流 → 右上角「環境變數」:
   - `DBMCP_URL`:通道網址,例如 `https://xxxx.trycloudflare.com`(不要加 `/api`)
   - `DBMCP_TOKEN`:`_http_token.txt` 的內容
4. 點「DeepSeek 寫報告」節點,確認模型是 `deepseek-chat`(匯入後若顯示模型無效,重新選一次)。
5. 先按「執行」試跑:做什麼 = 新建模型、地點 = `6541`(芝加哥 O'Hare)、量體 = rect、40 × 25 m、3 層、各層用途 `1:大廳; 2-:辦公`。

Dify 各版本的 DSL 格式不完全一樣,匯入失敗時照第 5 節手動建,或把錯誤訊息貼給維護者調整 DSL。

## 4. 表單怎麼填

| 欄位 | 新建模型 | 分析既有模型 |
|---|---|---|
| 地點 | 必填。DB 地點編號(最準)或英文城市名;對到多個地點時會列出編號請你選 | 選填,填了會換地點 |
| 量體 | rect / L / U / T / courtyard / podium_tower,加上該形狀要的尺寸 | 不用 |
| 各層用途 | `1:大廳; 2-5:辦公; 6-:會議`,沒寫到的樓層用辦公 | 不用 |
| 模型路徑 | 不用 | 必填,`.dsb` |
| 空調系統 | 選填,7 種 DB 簡易空調範本;不選沿用模型設定 | 同左;模型若用 DB 詳細空調,直接模擬模型裡的系統 |
| 外殼 U 值 / 外窗 SHGC | 選填,有實際數據才填;沒填的部位用 ASHRAE 90.1-2019 附錄 G 基準 | 要同時選「換成 ASHRAE 基準構造」才會套用 |
| 朝向、各立面窗牆比 | 選填;朝向是從正北順時針轉的角度 | 不用 |
| 節能措施 | 是 / 否(需要 building-energy-toolchain) | 是 / 否 |

- 可用的用途:辦公、會議、大廳、接待、走廊、樓梯、茶水間、儲藏、廁所、機房、商店、超市、商場公共區、餐廳、快餐、廚房、教室、圖書館、實驗室、客房、住宅、倉庫、候車、健身房、診間。
- 地點的氣象檔要已經在 DB 電腦上(`C:\ProgramData\DesignBuilder\Weather Data`)。哪些地點可以直接用,在 DB 電腦上查:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/options -Headers @{Authorization = "Bearer $(Get-Content <工作資料夾>\_http_token.txt)"}
```
- 填錯(用途打錯字、地點不明確、缺尺寸)會在開 DB 之前就回報原因,不會跑了幾分鐘才失敗。

## 5. 手動建工作流(匯入失敗時)

| # | 節點 | 設定 |
|---|---|---|
| 1 | 開始 | 欄位同第 4 節(變數名:mode, location, building_type, hvac, run_measures, name, shape, width, depth, stories, uses, floor_height, wwr, rotation, wwr_north, wwr_east, wwr_south, wwr_west, wall_u, roof_u, floor_u, window_u, window_shgc, notch_width, notch_depth, stem_width, bar_depth, court_width, court_depth, tower_width, tower_depth, tower_stories, dsb_path, apply_constructions) |
| 2 | 程式碼「整理表單」 | 把所有欄位轉成字串、空的給 `""`,輸出 `body = json.dumps(表單, ensure_ascii=False)` |
| 3 | HTTP「送出分析」 | POST `{{DBMCP_URL}}/api/workflow`,授權 Bearer `{{DBMCP_TOKEN}}`,Body = JSON `{{body}}` |
| 4 | 程式碼「讀工作編號」 | 解析回應:`ok` 為 true 時取 `job_id`,否則取 `error` |
| 5 | 條件分支 | ok 不是 true → 結束(輸出 error) |
| 6 | 迴圈(最多 45 次) | 迴圈變數 `finished`(預設 false)、`status_json`;`finished` 為 true 時停止 |
| 6a | 迴圈內 HTTP「查進度」 | GET `{{DBMCP_URL}}/api/jobs/{{job_id}}?wait=45`,讀取逾時 60 秒 |
| 6b | 迴圈內程式碼 | 回應的 `finished` → 迴圈變數 `finished`,整個回應 → `status_json` |
| 7 | 程式碼「整理結果」 | `status` 是 done 時,去掉 `result.steps`,輸出 JSON 文字 `data` |
| 8 | LLM(deepseek-chat) | 系統提示詞見 DSL 檔「DeepSeek 寫報告」節點;使用者訊息 = `data` |
| 9 | 結束 | 輸出報告文字與 `data` |

## 6. 回傳的資料

`GET /api/jobs/{job_id}` 完成時的 `result`:

- `model`:模型路徑、分區圖(HTML)、層數、熱區數、各層用途、朝向、各立面窗牆比
- `location`:地點、ASHRAE 氣候區、經緯度、氣象檔
- `constructions`:套用的 ASHRAE 2019 基準構造範本與外窗,以及使用者填的 U 值 / SHGC
- `hvac`:空調系統(簡易空調的效率與燃料,或詳細空調接了幾個熱區)
- `simulation`:全年冷熱需求、單位面積能耗(EUI)、各項用能與比例、尖峰用電與時間、溫度不達標時數、模擬實際的各立面窗牆比
- `delivered`:簡易空調時,把冷熱需求用空調效率換算成實際的電與天然氣(報告以這個為主)
- `measures`:8 項節能措施的判讀、省能 %、每年省多少、投資、回本年數(金額是 `costs.json` 的假設值;用和 `delivered` 同一組空調效率)
- `warnings`:有項目沒通過核對時的提醒
- `steps`:每一步的工具原始輸出(追查用)

模型、分區圖與報告都在 DB 電腦上(`<工作資料夾>\<模型名稱>\`),可以用 DB 打開模型繼續精修。

## 也可以當 MCP 用

同一個服務在 `/mcp` 提供完整的 MCP 工具(建模、套用途、構造、作息、地點、模擬、逐時結果),要開 DB 的工具請經 `db_job_start` / `db_job_status` 在背景跑。給 Agent 用時要在 `http_server.py` 加 `--public-host <通道主機名>`,MCP 端點才接受通道轉來的請求;表單工作流用的 `/api/*` 不需要。
