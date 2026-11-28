# 高速公路服务区风光储容量优化

本目录用于研究高速公路服务区风电、光伏、锂电池和氢储能的容量配置。当前以长兴服务区 2025 年 7 月逐时模拟情景为起点，先计算光伏与电池的能量流，再逐步建立容量优化模型。

## 目前可以运行什么

`matlab/changxing_pv_balance.m` 对同一组逐时负荷和光伏出力计算两个方案：

1. 单光伏，光伏优先供负荷，统计富余电量和供电缺口。
2. 光伏加一套示例电池，富余电量用于充电，新能源不足时电池放电。

储能不从电网充电，暂不考虑上网售电。供电缺口是电网补充前的需求，不能直接当作负荷缺电量。电池容量是试算条件，**不是优化推荐值**。风电、氢储能、多目标寻优和成本模型还在规划中。

## MATLAB 运行方法

将 MATLAB 当前文件夹切换到本目录（`projects/changxing/optimization`），运行：

```matlab
addpath('matlab');
[hourly, summary] = changxing_pv_balance;
```

`hourly` 包含每小时负荷、光伏、充放电、富余量、缺口和电池 SOC，`summary` 包含两个方案的月汇总。程序会生成曲线图；如不需要图形，可调用 `changxing_pv_balance(false)`。需要导出逐时表时可自行运行：

```matlab
writetable(hourly, 'july_balance_hourly.csv');
```

基础计算使用 MATLAB 自带的表格、日期和绘图功能，不需要额外优化工具箱。该版本已在 MATLAB R2026b（26.2.0.3433840）实跑：`test_changxing_pv_balance` 对 744 小时逐项比对通过，最大逐时能量残差 `1.14e-13 kWh`。

## 检查计算

在 MATLAB 的本目录中运行下面的测试，会将 744 小时结果与独立生成的参考数据逐项比较：

```matlab
addpath('tests');
test_changxing_pv_balance;
```

没有 MATLAB 时，可用 Python 标准库验证输入和参考结果：

```bash
python3 tests/verify_reference.py
```

后一项检查参考计算和能量平衡，不代表 MATLAB 脚本已运行。

## 数据与单位

`data/july_load_pv.csv` 包含 2025 年 7 月 744 个连续小时，时间列表示北京时间的区间起点。`load_kWh` 是全站基础负荷与充电负荷之和，`pv_kWh` 是当小时可用光伏电量；**不要再次叠加充电负荷**。每行是该小时电量，单位 kWh，按 1 h 间隔计算时，其数值等于该小时平均功率的 kW 数值。

数据来自长兴项目的[已固定版本](https://github.com/Tanna-qne/building-energy-toolchain/blob/5db61ec6bc6810598c559490e6ccf1de50f29cbc/projects/changxing/pv_2025/july_load_pv.csv)，文件 Git blob 为 `1a0b267d8028519be249df2d3d8e23e22dbf7e69`。负荷与光伏是模拟情景，7 月缺少同口径独立电表验证。

## 试算条件与结果

电池额定能量为 240 kWh，额定充放电功率为 120 kW，SOC 范围为 10%～90%，初始 SOC 为 10%，充电和放电效率各为 90%，暂不计自放电与衰减。容量和功率按[产品系列名称](https://www.lytran.com.cn/static/upload/file/20260404/1775269520355900.pdf)取整，样本参数表列为 241 kWh 和 125 kW。效率参考师瑞峰等《含氢储能的公路交通风、光自洽微网系统优化调度策略研究》，DOI `10.19912/j.0254-0096.tynxb.2022-1066`。SOC 区间参考冀维昆、张巍《考虑调频响应特性的电池储能系统双层多目标优化策略》，DOI `10.16180/j.cnki.issn1007-7820.2025.11.008`。初始 SOC 取下限是当前示例假设。

| 指标 | 单光伏 | 光伏加示例电池 |
|---|---:|---:|
| 月负荷 | 442,092.78 kWh | 442,092.78 kWh |
| 可用光伏 | 138,570.63 kWh | 138,570.63 kWh |
| 光伏富余 | 10,081.50 kWh | 6,404.24 kWh |
| 电网补充前的供电缺口 | 313,603.65 kWh | 310,625.07 kWh |
| 电池充电 / 放电 | — | 3,677.27 / 2,978.58 kWh |

示例的期初和期末 SOC 均为 10%。两组电量差异包含充放电损耗，不包含电池投资或电价。

## 风光储氢逐时运行（新增）

`matlab/changxing_dispatch.m` 在上一节的基础上接入氢能环节，按任务书的运行规则调度：

1. 新能源（光伏，若输入含风电列则含风电）优先供负荷；
2. 富余电量先给锂电池充电；
3. 电池充满后仍富余的电量送入电解槽制氢；
4. 供电不足时电池先放电，再由燃料电池补足，其余由电网补充。

```matlab
addpath('matlab');
[hourly, summary, parameters] = changxing_dispatch;          % 默认读取 data/july_load_pv.csv
[hourly, summary] = changxing_dispatch('data/july_load_pv.csv', false);   % 不画图
```

设备参数集中在 `matlab/changxing_parameters.m`，**全部是用于打通调度逻辑的示例假设**，不是设备手册值、不是文献值、更不是优化结果；电解槽 100 kW／50 kWh·kg⁻¹、储氢罐 200 kg、燃料电池 100 kW／18 kWh·kg⁻¹ 都需要换成实际选型参数后另行计算。

7 月情景（风电列为 0，因为目前没有长兴风电数据）：

| 指标 | 数值 |
|---|---:|
| 负荷 | 442,092.78 kWh |
| 可用新能源 | 138,570.63 kWh |
| 新能源直供负荷 | 128,489.13 kWh |
| 电池放电 | 2,978.58 kWh |
| 燃料电池发电 | 1,021.69 kWh |
| 电网补充 | 309,603.38 kWh |
| 电解槽耗电 / 制氢 | 2,838.04 kWh / 56.76 kg |
| 弃电 | 3,566.20 kWh |
| 新能源供电占比 | 29.97% |

与只有电池的方案相比，电网补充从 310,625.07 降到 309,603.38 kWh，弃电从 6,404.24 降到 3,566.20 kWh —— 但这是示例容量下的调度差异，**不能据此得出储氢容量结论**。接入风电（输入 CSV 增加 `wind_kWh` 列）后才能做风光储氢的对比。

## 目录

- `matlab/`：MATLAB 逐时计算入口，`changxing_pv_balance.m`（光伏＋电池）与 `changxing_dispatch.m`（风光＋电池＋氢储）。设备参数在 `changxing_parameters.m`。
- `data/`：固定版本的模型输入。
- `tests/`：逐时参考结果和核对脚本，`verify_reference.py`／`verify_dispatch.py` 为独立的 Python 复核。
- `HANDOFF.md`：模型边界、待完成工作与交接重点。
- `AGENTS.md`：后续代码协作中需要遵循的项目约定。
