function [hourly, summary, parameters] = changxing_dispatch(inputPath, showPlot)
%CHANGXING_DISPATCH July 2025 hourly dispatch: renewables + battery + hydrogen.
%
%   [hourly, summary, parameters] = changxing_dispatch
%   [hourly, summary, parameters] = changxing_dispatch('data/july_load_pv.csv', false)
%
% This entry point extends the verified PV/battery balance with the hydrogen
% subsystem required by the thesis (electrolyser, hydrogen tank, fuel cell)
% and accepts an optional wind column, so the same dispatch can later run on
% a wind-and-solar scenario without code changes.
%
% DISPATCH RULES (from the project brief):
%   1. Renewable output serves the load first.
%   2. Surplus charges the battery.
%   3. Any surplus the battery cannot take is converted to hydrogen.
%   4. On a deficit the battery discharges first, the fuel cell second, and
%      the grid covers whatever remains.
% No grid charging, no export and no hydrogen venting are modelled.
%
% IMPORTANT - parameter status:
%   The equipment figures in changxing_parameters.m are ILLUSTRATIVE values
%   used to exercise the dispatch logic. They are NOT optimisation results and
%   must be replaced with datasheet or literature values before any capacity
%   conclusion is drawn. The battery figures keep the 240 kWh / 120 kW example
%   already used by changxing_pv_balance.
%
% WIND:
%   The July dataset has no measured or modelled wind series for Changxing,
%   so wind defaults to zero and the run is effectively PV + battery + H2.
%   Add a `wind_kWh` column to the input CSV to run a wind-and-solar case.
%
% Outputs:
%   hourly     - table, one row per hour, all energies in kWh (SOC and tank
%                state as fractions).
%   summary    - table, one row per scenario.
%   parameters - struct actually used, with source notes.

if nargin < 1 || isempty(inputPath)
    repoRoot = fileparts(fileparts(mfilename('fullpath')));
    inputPath = fullfile(repoRoot, 'data', 'july_load_pv.csv');
end
if nargin < 2
    showPlot = true;
end

parameters = changxing_parameters();
p = parameters;

input = readtable(inputPath);
names = string(input.Properties.VariableNames);
required = ["interval_start_CST", "load_kWh", "pv_kWh"];
if ~all(ismember(required, names))
    error('Input CSV needs interval_start_CST, load_kWh and pv_kWh.');
end

if isdatetime(input.interval_start_CST)
    time = input.interval_start_CST;
else
    time = datetime(string(input.interval_start_CST), ...
        'InputFormat', 'yyyy-MM-dd HH:mm:ss');
end

loadEnergy = double(input.load_kWh);
pvEnergy = double(input.pv_kWh);
if any(ismember("wind_kWh", names))
    windEnergy = double(input.wind_kWh);
else
    windEnergy = zeros(size(pvEnergy));
end

n = height(input);
if any(~isfinite([loadEnergy; pvEnergy; windEnergy])) || ...
        any([loadEnergy; pvEnergy; windEnergy] < 0)
    error('Load, PV and wind energy must be finite nonnegative values.');
end

%% State initialisation
soc = p.socInitial * p.batteryCapacity;          % kWh
tank = p.tankInitialFraction * p.tankCapacity;   % kg
energyMin = p.socMin * p.batteryCapacity;
energyMax = p.socMax * p.batteryCapacity;
tankMin = p.tankMinFraction * p.tankCapacity;
tankMax = p.tankMaxFraction * p.tankCapacity;

renewable = pvEnergy + windEnergy;

directUse = min(loadEnergy, renewable);
charge = zeros(n, 1);
discharge = zeros(n, 1);
electrolyser = zeros(n, 1);      % kWh electricity consumed
h2Produced = zeros(n, 1);        % kg
h2Consumed = zeros(n, 1);        % kg
fuelCell = zeros(n, 1);          % kWh electricity delivered
gridImport = zeros(n, 1);
curtailment = zeros(n, 1);
socEnd = zeros(n, 1);
tankEnd = zeros(n, 1);

for k = 1:n
    if renewable(k) >= loadEnergy(k)
        surplus = renewable(k) - loadEnergy(k);

        roomAC = max(energyMax - soc, 0) / p.etaCharge;
        charge(k) = min([surplus, p.batteryPower, roomAC]);
        soc = soc + p.etaCharge * charge(k);
        surplus = surplus - charge(k);

        tankRoom = max(tankMax - tank, 0);
        electrolyser(k) = min([surplus, p.electrolyserPower, ...
            tankRoom * p.electrolyserSpecificEnergy]);
        h2Produced(k) = electrolyser(k) / p.electrolyserSpecificEnergy;
        tank = tank + h2Produced(k);
        surplus = surplus - electrolyser(k);

        curtailment(k) = surplus;
    else
        deficit = loadEnergy(k) - renewable(k);

        availableAC = max(soc - energyMin, 0) * p.etaDischarge;
        discharge(k) = min([deficit, p.batteryPower, availableAC]);
        soc = soc - discharge(k) / p.etaDischarge;
        deficit = deficit - discharge(k);

        tankUsable = max(tank - tankMin, 0) * p.fuelCellSpecificYield;
        fuelCell(k) = min([deficit, p.fuelCellPower, tankUsable]);
        h2Consumed(k) = fuelCell(k) / p.fuelCellSpecificYield;
        tank = tank - h2Consumed(k);
        deficit = deficit - fuelCell(k);

        gridImport(k) = deficit;
    end

    socEnd(k) = soc / p.batteryCapacity;
    tankEnd(k) = tank / p.tankCapacity;
end

%% Internal consistency checks
balanceResidual = renewable + discharge + fuelCell + gridImport ...
    - loadEnergy - charge - electrolyser - curtailment;
if max(abs(balanceResidual)) > 1e-8
    error('Hourly energy balance failed (max residual %.3g kWh).', ...
        max(abs(balanceResidual)));
end
if any(socEnd < p.socMin - 1e-9) || any(socEnd > p.socMax + 1e-9)
    error('Battery SOC left the allowed range.');
end
if any(tankEnd < p.tankMinFraction - 1e-9) || ...
        any(tankEnd > p.tankMaxFraction + 1e-9)
    error('Hydrogen tank level left the allowed range.');
end
if any(charge > 1e-9 & discharge > 1e-9)
    error('Battery charged and discharged in the same hour.');
end
if any(electrolyser > 1e-9 & fuelCell > 1e-9)
    error('Electrolyser and fuel cell ran in the same hour.');
end

%% Output tables
hourly = table(time, loadEnergy, pvEnergy, windEnergy, directUse, charge, ...
    discharge, socEnd, electrolyser, h2Produced, fuelCell, h2Consumed, ...
    gridImport, curtailment, tankEnd, ...
    'VariableNames', {'IntervalStart', 'Load_kWh', 'PV_kWh', 'Wind_kWh', ...
    'DirectUse_kWh', 'BatteryCharge_kWh', 'BatteryDischarge_kWh', ...
    'BatterySOC_End', 'Electrolyser_kWh', 'H2Produced_kg', ...
    'FuelCell_kWh', 'H2Consumed_kg', 'GridImport_kWh', 'Curtailment_kWh', ...
    'HydrogenTank_End'});

scenarioNames = ["Renewables + battery + H2" ];
loadTotal = sum(loadEnergy);
renewableTotal = sum(renewable);
directTotal = sum(directUse);
supplied = directTotal + sum(discharge) + sum(fuelCell);
summary = table(scenarioNames, loadTotal, renewableTotal, directTotal, ...
    sum(charge), sum(discharge), sum(electrolyser), sum(h2Produced), ...
    sum(fuelCell), sum(gridImport), sum(curtailment), ...
    100 * supplied / loadTotal, 100 * sum(curtailment) / renewableTotal, ...
    'VariableNames', {'Scenario', 'Load_kWh', 'Renewable_kWh', ...
    'DirectUse_kWh', 'BatteryCharge_kWh', 'BatteryDischarge_kWh', ...
    'Electrolyser_kWh', 'H2Produced_kg', 'FuelCell_kWh', 'GridImport_kWh', ...
    'Curtailment_kWh', 'LoadCoveredByRenewables_pct', 'Curtailment_pct'});

fprintf('Scenario: %s\n', scenarioNames);
fprintf(['Renewable %.2f kWh | direct %.2f | battery %.2f | fuel cell %.2f ' ...
    '| grid %.2f | curtailed %.2f | H2 %.2f kg\n'], renewableTotal, ...
    directTotal, sum(discharge), sum(fuelCell), sum(gridImport), ...
    sum(curtailment), sum(h2Produced));
fprintf('Max hourly balance residual: %.3g kWh.\n', max(abs(balanceResidual)));

if showPlot
    fig = figure('Name', 'Changxing dispatch', 'Color', 'w');
    try
        theme(fig, 'light');
    catch
    end
    tiledlayout(3, 1, 'TileSpacing', 'compact', 'Padding', 'compact');

    nexttile;
    plot(time, loadEnergy, 'k-'); hold on;
    plot(time, renewable, '-', 'Color', [0.95 0.60 0.10]);
    ylabel('kWh per hour'); grid on; box off;
    legend({'Load', 'Renewable'}, 'Location', 'best');
    title('Hourly load and renewable output');

    nexttile;
    plot(time, curtailment, '-', 'Color', [0.85 0.33 0.10]); hold on;
    plot(time, -gridImport, '-', 'Color', [0.20 0.45 0.75]);
    ylabel('kWh per hour'); grid on; box off;
    legend({'Curtailment', 'Grid import'}, 'Location', 'best');
    title('Curtailment and grid import');

    nexttile;
    plot(time, 100 * socEnd, '-', 'Color', [0.10 0.60 0.35]); hold on;
    plot(time, 100 * tankEnd, '-', 'Color', [0.35 0.30 0.75]);
    ylabel('%'); grid on; box off;
    legend({'Battery SOC', 'Hydrogen tank'}, 'Location', 'best');
    title('Storage state at the end of each hour');
end
end
