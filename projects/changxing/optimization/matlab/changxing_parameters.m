function p = changxing_parameters()
%CHANGXING_PARAMETERS Illustrative equipment parameters for the dispatch study.
%
% Every value below is an ASSUMPTION used to exercise the hourly dispatch
% logic. None of them is a measured value, a datasheet value, an optimisation
% result or a figure taken from the literature unless the note says so.
% Replace them with equipment-manual or reviewed-source values before any
% capacity recommendation is written up.
%
% Units: energy kWh, power kW, hydrogen kg, time h.

%% Battery - keeps the 240 kWh / 120 kW example already used by
% changxing_pv_balance.m. Round series label; the product table of the
% referenced Lytran series lists 241 kWh and 125 kW. The 90%% charge and
% discharge efficiencies follow the source already cited in README.md
% (Shi et al., Acta Energiae Solaris Sinica, 2023, 44(11): 513-521).
p.batteryCapacity = 240;      % kWh nominal
p.batteryPower = 120;         % kW charge/discharge limit
p.socMin = 0.10;
p.socMax = 0.90;
p.socInitial = 0.10;          % initial state on the lower bound (assumption)
p.etaCharge = 0.90;
p.etaDischarge = 0.90;

%% Electrolyser - assumption, NOT a datasheet value.
% 100 kW is a plausible service-area scale for this trial size. The specific
% electricity consumption of 50 kWh per kg H2 corresponds to roughly
% 4.5 kWh/Nm^3 at 11.1 Nm^3/kg, i.e. the order of magnitude quoted for
% alkaline electrolysers. Replace with the selected product's curve.
p.electrolyserPower = 100;          % kW
p.electrolyserSpecificEnergy = 50;  % kWh electricity per kg H2

%% Hydrogen storage - assumption, NOT a design value.
% 200 kg with a 10%%~90%% working window and a 10%% initial state mirrors the
% battery treatment. Tank pressure, compression energy and venting are not
% modelled.
p.tankCapacity = 200;          % kg
p.tankMinFraction = 0.10;
p.tankMaxFraction = 0.90;
p.tankInitialFraction = 0.10;

%% Fuel cell - assumption, NOT a datasheet value.
% 100 kW electrical output. The specific yield of 18 kWh electricity per kg H2
% corresponds to about 54%% of the 33.33 kWh/kg lower heating value of
% hydrogen, inside the usual 50%%~60%% band for PEM fuel cells. Do not quote
% this figure as a measured stack efficiency.
p.fuelCellPower = 100;              % kW
p.fuelCellSpecificYield = 18;       % kWh electricity per kg H2

%% Provenance recorded for the record
p.assumptionNote = ['All equipment numbers in changxing_parameters.m are ' ...
    'illustrative assumptions for dispatch testing; only the battery ' ...
    'efficiency values follow the source cited in README.md.'];
end
