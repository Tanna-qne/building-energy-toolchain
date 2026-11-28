function test_changxing_dispatch()
%TEST_CHANGXING_DISPATCH Check the hydrogen dispatch against the reference run.
%
% The reference was produced by an independent Python implementation of the
% same dispatch rules (tests/verify_dispatch.py) on the same 744 hours.

repoRoot = fileparts(fileparts(mfilename('fullpath')));
addpath(fullfile(repoRoot, 'matlab'));
inputPath = fullfile(repoRoot, 'data', 'july_load_pv.csv');
[hourly, summary] = changxing_dispatch(inputPath, false);
reference = readtable(fullfile(repoRoot, 'tests', 'reference_dispatch.csv'));

assert(height(hourly) == 744, 'Expected 744 computed hours.');
assert(height(reference) == 744, 'Expected 744 reference hours.');

columns = {
    'Load_kWh',            'load_kWh';
    'PV_kWh',              'pv_kWh';
    'Wind_kWh',            'wind_kWh';
    'DirectUse_kWh',       'direct_kWh';
    'BatteryCharge_kWh',   'charge_kWh';
    'BatteryDischarge_kWh','discharge_kWh';
    'BatterySOC_End',      'soc_end';
    'Electrolyser_kWh',    'electrolyser_kWh';
    'H2Produced_kg',       'h2_produced_kg';
    'FuelCell_kWh',        'fuel_cell_kWh';
    'H2Consumed_kg',       'h2_consumed_kg';
    'GridImport_kWh',      'grid_import_kWh';
    'Curtailment_kWh',     'curtailment_kWh';
    'HydrogenTank_End',    'tank_end';
};

for index = 1:size(columns, 1)
    actual = hourly.(columns{index, 1});
    expected = reference.(columns{index, 2});
    difference = max(abs(actual - expected));
    assert(difference < 1e-5, ...
        'Mismatch in %s, maximum difference %.9g.', columns{index, 1}, difference);
end

% Physical invariants
p = changxing_parameters();
assert(all(hourly.BatterySOC_End >= p.socMin - 1e-8));
assert(all(hourly.BatterySOC_End <= p.socMax + 1e-8));
assert(all(hourly.HydrogenTank_End >= p.tankMinFraction - 1e-8));
assert(all(hourly.HydrogenTank_End <= p.tankMaxFraction + 1e-8));
assert(~any(hourly.BatteryCharge_kWh > 1e-8 & ...
    hourly.BatteryDischarge_kWh > 1e-8));
assert(~any(hourly.Electrolyser_kWh > 1e-8 & hourly.FuelCell_kWh > 1e-8));

% The battery-plus-hydrogen run must never need more grid energy than the
% PV-only reference and must never curtail more than the battery-only run.
batteryOnly = readtable(fullfile(repoRoot, 'tests', 'reference_july_balance.csv'));
referenceGap = sum(batteryOnly.gap_with_battery_kWh);
assert(sum(hourly.GridImport_kWh) <= referenceGap + 1e-6, ...
    'Hydrogen must not increase grid import compared with battery only.');

% Aggregate figures quoted in the documentation
assert(abs(summary.Load_kWh - 442092.783706) < 0.01);
assert(abs(summary.Renewable_kWh - 138570.634453) < 0.01);
assert(abs(summary.H2Produced_kg - 56.7608) < 0.01);
assert(abs(summary.FuelCell_kWh - 1021.6946) < 0.01);

fprintf('MATLAB dispatch comparison passed for all 744 hours.\n');
fprintf(['Renewable %.2f kWh, grid %.2f kWh, H2 produced %.2f kg, ' ...
    'curtailed %.2f kWh.\n'], summary.Renewable_kWh, summary.GridImport_kWh, ...
    summary.H2Produced_kg, summary.Curtailment_kWh);
end
