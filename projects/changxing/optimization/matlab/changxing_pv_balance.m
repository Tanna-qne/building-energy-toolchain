function [hourly, summary] = changxing_pv_balance(showPlot)
%CHANGXING_PV_BALANCE July 2025 PV-only and PV-plus-battery comparison.
%
% From the repository root, add the matlab folder to the path and run:
%   addpath('matlab');
%   [hourly, summary] = changxing_pv_balance;
%   writetable(hourly, 'july_balance_hourly.csv');
%
% The input load ALREADY includes base load and EV charging. Each value is
% the energy in one hourly interval. Missing PV supply is the demand gap
% BEFORE grid supply; it must not be described as an electricity outage.
%
% Illustrative battery: 240 kWh, 120 kW, SOC 10%%-90%%, initial SOC 10%%,
% charge and discharge efficiencies 90%% each. No export, grid charging,
% self-discharge or battery degradation is modelled. The battery is an
% example for checking the dispatch rules, not an optimized configuration.
%
% Data: projects/changxing/pv_2025/july_load_pv.csv at GitHub commit
% 5db61ec6bc6810598c559490e6ccf1de50f29cbc. Data are simulated scenarios.
% Size reference: Lytran 240 kWh/120 kW series label. The product table
% lists 241 kWh and 125 kW. The rounded series values are used here.
% https://www.lytran.com.cn/static/upload/file/20260404/1775269520355900.pdf
% Charge/discharge efficiency reference: Shi et al., Acta Energiae Solaris
% Sinica, 2023, 44(11): 513-521, doi:10.19912/j.0254-0096.tynxb.2022-1066.
% SOC interval example: Ji & Zhang, Electronic Science and Technology,
% 2025, 38(11):61-69, doi:10.16180/j.cnki.issn1007-7820.2025.11.008.
% Initial SOC at the lower bound is an initial-state assumption.

if nargin < 1
    showPlot = true;
end

repoRoot = fileparts(fileparts(mfilename('fullpath')));
inputPath = fullfile(repoRoot, 'data', 'july_load_pv.csv');
if ~isfile(inputPath)
    error('Cannot find data/july_load_pv.csv in the repository.');
end
input = readtable(inputPath);
required = {'interval_start_CST', 'load_kWh', 'pv_kWh', ...
    'load_minus_pv_kWh'};
if ~all(ismember(required, input.Properties.VariableNames))
    error('The input CSV does not contain the expected columns.');
end

if isdatetime(input.interval_start_CST)
    time = input.interval_start_CST;
else
    time = datetime(string(input.interval_start_CST), ...
        'InputFormat', 'yyyy-MM-dd HH:mm:ss');
end
if height(input) ~= 744 || any(isnat(time)) || ...
        time(1) ~= datetime(2025, 7, 1, 0, 0, 0) || ...
        time(end) ~= datetime(2025, 7, 31, 23, 0, 0) || ...
        any(hours(diff(time)) ~= 1)
    error('Expected consecutive hourly data for July 2025.');
end

loadEnergy = input.load_kWh;
pvEnergy = input.pv_kWh;
if any(~isfinite(loadEnergy)) || any(~isfinite(pvEnergy)) || ...
        any(loadEnergy < 0) || any(pvEnergy < 0)
    error('Load and PV energy must be finite nonnegative values.');
end
if max(abs(input.load_minus_pv_kWh - (loadEnergy - pvEnergy))) > 2e-6
    error('The input net-load column is inconsistent with load minus PV.');
end

% All intervals are one hour, so a kW limit applies to one hour in kWh.
numberOfHours = height(input);
dt = 1;
batteryCapacity = 240;  % kWh, illustrative nominal energy
batteryPower = 120;     % kW, illustrative charge/discharge power limit
socMin = 0.10;
socMax = 0.90;
socInitial = socMin;
etaCharge = 0.90;
etaDischarge = 0.90;
energyMin = socMin * batteryCapacity;
energyMax = socMax * batteryCapacity;

directUse = min(loadEnergy, pvEnergy);
surplusBefore = max(pvEnergy - loadEnergy, 0);
gapBefore = max(loadEnergy - pvEnergy, 0);
charge = zeros(numberOfHours, 1);
discharge = zeros(numberOfHours, 1);
surplusAfter = zeros(numberOfHours, 1);
gapAfter = zeros(numberOfHours, 1);
stored = zeros(numberOfHours + 1, 1);
stored(1) = socInitial * batteryCapacity;

for hourIndex = 1:numberOfHours
    remainingSpace = max(energyMax - stored(hourIndex), 0);
    usableEnergy = max(stored(hourIndex) - energyMin, 0);
    charge(hourIndex) = min([surplusBefore(hourIndex), ...
        batteryPower * dt, remainingSpace / etaCharge]);
    discharge(hourIndex) = min([gapBefore(hourIndex), ...
        batteryPower * dt, usableEnergy * etaDischarge]);
    stored(hourIndex + 1) = stored(hourIndex) + ...
        etaCharge * charge(hourIndex) - discharge(hourIndex) / etaDischarge;
    surplusAfter(hourIndex) = surplusBefore(hourIndex) - charge(hourIndex);
    gapAfter(hourIndex) = gapBefore(hourIndex) - discharge(hourIndex);
end
socEnd = stored(2:end) / batteryCapacity;

% Verify power balance, battery state and charge/discharge exclusivity.
balanceError = max(abs(pvEnergy + discharge + gapAfter - ...
    loadEnergy - charge - surplusAfter));
energyError = abs(stored(end) - stored(1) - ...
    (etaCharge * sum(charge) - sum(discharge) / etaDischarge));
if balanceError > 1e-8 || energyError > 1e-7 || ...
        any(stored < energyMin - 1e-8) || any(stored > energyMax + 1e-8) || ...
        any(charge > 1e-8 & discharge > 1e-8)
    error('Hourly balance or battery constraints failed verification.');
end

hourly = table(time, loadEnergy, pvEnergy, directUse, surplusBefore, ...
    gapBefore, charge, discharge, surplusAfter, gapAfter, socEnd, ...
    'VariableNames', {'IntervalStart', 'Load_kWh', 'PV_kWh', ...
    'PVDirectUse_kWh', 'PVSurplusBefore_kWh', 'DemandGapBefore_kWh', ...
    'BatteryCharge_kWh', 'BatteryDischarge_kWh', ...
    'PVSurplusAfter_kWh', 'DemandGapAfter_kWh', 'BatterySOC_End'});

scenario = ["PV only"; "PV plus example battery"];
loadTotal = repmat(sum(loadEnergy), 2, 1);
pvTotal = repmat(sum(pvEnergy), 2, 1);
directTotal = repmat(sum(directUse), 2, 1);
chargeTotal = [0; sum(charge)];
dischargeTotal = [0; sum(discharge)];
surplusTotal = [sum(surplusBefore); sum(surplusAfter)];
gapTotal = [sum(gapBefore); sum(gapAfter)];
surplusRate = 100 * surplusTotal ./ pvTotal;
loadCovered = 100 * (directTotal + dischargeTotal) ./ loadTotal;
summary = table(scenario, loadTotal, pvTotal, directTotal, chargeTotal, ...
    dischargeTotal, surplusTotal, gapTotal, surplusRate, loadCovered, ...
    'VariableNames', {'Scenario', 'Load_kWh', 'PV_kWh', ...
    'DirectUse_kWh', 'BatteryCharge_kWh', 'BatteryDischarge_kWh', ...
    'PVSurplus_kWh', 'DemandGapBeforeGrid_kWh', 'PVSurplus_pct', ...
    'LoadCoveredByPVandBattery_pct'});

disp('July simulated scenario comparison:');
disp(summary);
fprintf('Battery initial SOC: %.1f%%; final SOC: %.1f%%.\n', ...
    100 * socInitial, 100 * socEnd(end));
fprintf('Max hourly energy balance residual: %.3g kWh.\n', balanceError);

if showPlot
    figure('Name', 'Changxing July PV and battery comparison', 'Color', 'w');
    subplot(3, 1, 1);
    plot(time, loadEnergy, 'LineWidth', 0.8);
    hold on; plot(time, pvEnergy, 'LineWidth', 0.8); hold off;
    ylabel('kWh per hour');
    legend('Load incl. EV', 'Available PV', 'Location', 'best');
    title('Hourly load and available PV'); grid on;

    subplot(3, 1, 2);
    plot(time, surplusBefore, 'LineWidth', 0.8);
    hold on; plot(time, surplusAfter, 'LineWidth', 0.8); hold off;
    ylabel('kWh per hour');
    legend('Before battery', 'After battery', 'Location', 'best');
    title('PV surplus'); grid on;

    subplot(3, 1, 3);
    plot(time, 100 * socEnd, 'LineWidth', 0.8);
    ylim([0, 100]); ylabel('SOC (%)'); xlabel('Interval start, Beijing time');
    title('Battery state at the end of each hour'); grid on;
end
end
