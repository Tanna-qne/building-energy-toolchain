function test_changxing_pv_balance()
%TEST_CHANGXING_PV_BALANCE Check all 744 hours against the reference run.

repoRoot = fileparts(fileparts(mfilename('fullpath')));
addpath(fullfile(repoRoot, 'matlab'));
[hourly, summary] = changxing_pv_balance(false);
reference = readtable(fullfile(repoRoot, 'tests', 'reference_july_balance.csv'));

assert(height(hourly) == 744, 'Expected 744 computed hours.');
assert(height(reference) == 744, 'Expected 744 reference hours.');

columns = {
    'Load_kWh',              'load_kWh';
    'PV_kWh',                'pv_kWh';
    'PVDirectUse_kWh',       'pv_direct_kWh';
    'PVSurplusBefore_kWh',   'surplus_no_battery_kWh';
    'DemandGapBefore_kWh',   'gap_no_battery_kWh';
    'BatteryCharge_kWh',     'battery_charge_kWh';
    'BatteryDischarge_kWh',  'battery_discharge_kWh';
    'PVSurplusAfter_kWh',    'surplus_with_battery_kWh';
    'DemandGapAfter_kWh',    'gap_with_battery_kWh';
    'BatterySOC_End',        'SOC_end'
};

for index = 1:size(columns, 1)
    actual = hourly.(columns{index, 1});
    expected = reference.(columns{index, 2});
    difference = max(abs(actual - expected));
    assert(difference < 1e-5, ...
        'Mismatch in %s, maximum difference %.9g.', columns{index, 1}, difference);
end

assert(all(hourly.BatterySOC_End >= 0.1 - 1e-8));
assert(all(hourly.BatterySOC_End <= 0.9 + 1e-8));
assert(~any(hourly.BatteryCharge_kWh > 1e-8 & ...
    hourly.BatteryDischarge_kWh > 1e-8));
assert(abs(hourly.BatterySOC_End(end) - 0.1) < 1e-8);
assert(abs(summary.PVSurplus_kWh(1) - 10081.504913) < 0.01);
assert(abs(summary.PVSurplus_kWh(2) - 6404.239718) < 0.01);
fprintf('MATLAB hourly comparison passed for all 744 hours.\n');
end
