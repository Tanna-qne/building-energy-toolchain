# 本機 Windows 設定(相對 upstream 的改動)

上游以 Linux/Docker 為主,在 Windows 直接跑要處理三件事。都已改好並實測。

## 1. 路徑與執行檔 — `energyplus-mcp-server/energyplus_mcp_server/config.py`

- `PathConfig` 的 `/workspace/...`、`/tmp` 改成 repo 目錄與 `%TEMP%\energyplus-mcp`,否則輸出目錄落到 `\workspace` 會 `WinError 5`。
- `executable_path` 在 Windows 補 `.exe`。
- `default_installation` 在 Windows 指向本機 EnergyPlus 26.2(可用 `EPLUS_HOME` 覆寫)。

## 2. 模擬卡死 — eppy 的 stdin 問題

`idf.run()` 會用 `subprocess.check_call` 起 EnergyPlus,子程序繼承了 MCP 的 stdin 管線;
遇到 HVACTemplate(要先跑 ExpandObjects)就會永遠卡住。

修法:在 `eppy/runner/run_functions.py` 的 `check_call` 加上 `stdin=subprocess.DEVNULL`。

`uv sync` 會重建 .venv 蓋掉這個修正,重跑一次:

```
cd energyplus-mcp-server
.venv/Scripts/python.exe apply_windows_patches.py
```

## 3. 工具預設值 — `energyplus_mcp_server/server.py`

`expandobjects` 維持 upstream 的 `True`(stdin 修好後可安全強制展開):
GroundHeatTransfer 這類物件不像 HVACTemplate 會被 eppy 自動偵測, 不強制就會失敗。
`readvars` 維持 `True`。

## 4. Window5 資料集

部分模型引用 `..\datasets\Window5DataFile.dat`。已把 EnergyPlus 的
`DataSets/Window5DataFile.dat` 複製到 `energyplus-mcp-server/datasets/`,
伺服器的工作目錄就是該處, 所以相對路徑能解析。

## 驗證

兩個模型 + Chicago 氣象,透過 MCP 的 `run_energyplus_simulation` 呼叫:

| 模型 | 結果 |
|---|---|
| 5ZoneAirCooled | success,7.3 秒 |
| HVACTemplate-5ZoneVAVFanPowered(觸發 ExpandObjects) | success |

輸出含 `.mtr`、`.eio`、`eplusout.csv`、各區 CSV、表格 HTML。

## EnergyPlus 本體

`C:/Users/kiri/AppData/Local/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64`
(可攜版,免安裝、不需管理員)