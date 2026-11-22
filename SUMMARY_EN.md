# Building Energy Simulation Toolchain — English Summary

[![CI](https://github.com/Tanna-qne/building-energy-toolchain/actions/workflows/ci.yml/badge.svg)](https://github.com/Tanna-qne/building-energy-toolchain/actions/workflows/ci.yml)

EnergyPlus-based building energy modelling, calibration and retrofit analysis. The repository has one complete case study and a reusable pipeline. The detailed documentation is in Chinese; this page summarises it.

## Case study: hourly base load for a highway service area with PV, storage and EV charging

**Problem.** Sizing PV, battery storage and EV chargers for the Changxing service area (Zhejiang, China) needs an 8,760-hour load profile. The only measured data is one 24-hour typical day, which is the annual average for each clock hour (4,031 kWh/day).

**Model** ([cx_lowest_max.idf](projects/changxing/cx_lowest_max.idf))
- The model is a two-storey service building with 18 thermal zones, 13 of them conditioned. The floor area is scaled to 5,048 m².
- Geometry, envelope and internal loads come from a published service-area prototype (Hong 2024, Hefei University of Technology), based on Chinese design standards DB34/5076 and GB 50034.
- Split heat pumps are modelled as EnergyPlus PTHP (rated cooling COP 3.5) and provide both cooling and heating.
- The weather file is a 2025 hourly EPW built from ECMWF ERA5 reanalysis via the Open-Meteo API. The conversion script reproduces the committed EPW byte for byte, and a solar-geometry check confirms the time alignment.

**Calibration**
- Each iteration runs a full year, averages the 365 days hour by hour, and compares the result with the 24 measured points.
- The residuals are turned into three calibration variables: hourly operating intensity, a distributed peak load and an ancillary circuit.
- The result is CV(RMSE) 0.10% and NMBE +0.06%, against ASHRAE Guideline 14 hourly limits of 30% and ±10%. The largest hourly deviation is 0.61 kW.
- This fit shows that the calibration converged; it is not independent validation, and the documentation says so.
- Independent corroboration: the simulated summer and winter loads are 1.25× and 1.06× the shoulder season. Metered service-area data in Ye et al. (2025, *Energy* 340) shows about 1.27× and 1.08×.

**Morris global sensitivity** (14 uncalibrated parameters, 150 annual runs, run in parallel)
- The building is dominated by internal loads. Equipment, kitchen, lighting and occupancy account for about half of the annual sensitivity. Each envelope parameter changes annual energy by 0.3% or less.
- July energy is most sensitive to cooling COP (μ* 14%).
- Heating energy depends on outdoor air, infiltration, heating setpoint and heating COP (μ* 36–43%). None of these has site data, so the 67 MWh of annual heating is an order-of-magnitude estimate.

**Hold-out validation**
- The final model has 24 hourly intensities for 24 data points, so a hold-out test is impossible on it directly.
- Instead, a 7-block, low-degree-of-freedom version was refitted with 4-fold cross-validation, and EnergyPlus was rerun for each fold.
- The out-of-sample CV(RMSE) is 47%, against 53% for a block-mean baseline with no physics.
- The isolated meal-time peaks cannot be predicted from neighbouring hours. This is quantitative evidence that the hourly shape is operational input, and that using 24 hourly values is required by the data rather than overfitting.

**Energy conservation measures** (9 annual runs on the calibrated model)

| Measure | Annual saving | Note |
|---|---:|---|
| Heat pump COP ×1.25, fan efficiency 0.52 → 0.70 | 80.3 MWh (5.4%) | 21 MWh of this comes from fans |
| Lighting power −30% | 54.5 MWh (3.7%) | Heating rises 2.7 MWh because internal gains drop |
| Equipment power −20% | 49.5 MWh (3.3%) | Same effect on heating |
| Airtightness (infiltration ×0.5) | 8.4 MWh (0.6%) | 7.8 MWh of this is heating |
| Wall U 0.50 → 0.25, roof U 0.45 → 0.18 | 6.5 MWh (0.4%) | |
| Cool roof, window film | 1.5 / 0.6 MWh | Cooling savings almost cancelled by extra heating (hot-summer/cold-winter climate) |
| **All measures combined** | **197.4 MWh (13.3%)** | Individual savings add up to 201.3 MWh; measures interact |

- The ranking matches the Morris screening: two independent methods point to equipment and operation, not the envelope.
- Effect on the energy system: after the retrofit, July PV surplus grows from 43 to 65 hours, but net peak load falls only 3.7% because evening EV charging sets the peak. Storage should therefore be re-optimised on the post-retrofit load.
- Economics: electricity prices are swept from 0.5 to 1.0 CNY/kWh, and break-even investment is reported for a 5-year payback. No capital costs are assumed.

**PV and EV charging series**
- PV has two scenarios: one scales the measured typical day by hourly irradiance, the other uses NREL PVWatts v5 physics.
- EV charging is the typical day × provincial monthly factors × measured day-to-day variability.
- Both are constrained so that their annual hour-by-hour mean matches the typical day. All three series are aligned hourly as the dispatch input.

Full documentation: [projects/changxing/MODEL_README.md](projects/changxing/MODEL_README.md) (Chinese). Section 9 lists every assumption and limitation, including the calibration assumption that air conditioning is off from 15:00 to 17:00 between October and April.

## Reusable pipeline

- **Parametric modelling**: building parameters (footprint, storeys, window-to-wall ratio, use, HVAC type) → dragonfly / honeybee-energy / OpenStudio → EnergyPlus IDF.
- **Retrofit scan**: the same 8 measures and their combination, with savings, simple payback, and tariff and cost sensitivity. Simulations run in parallel through a pool of EnergyPlus MCP servers.
- **DesignBuilder integration**: imports IDFs exported from DesignBuilder (auto-upgraded from 23.1 to 26.2). An MCP server reads and edits DesignBuilder models, runs simulations and builds models from parameters.

## Reproducibility

- GitHub Actions installs the official EnergyPlus 26.2 Linux build on every push.
- It reruns the calibrated Changxing model and all 9 measures, and compares them with the committed results. The latest run matched every hour (maximum difference 0.0000 kWh), and each measure matched to the 0.1 kWh reported.
- Scripts find EnergyPlus through `ENERGYPLUS_DIR`, `PATH` or the default install locations, so nothing is tied to one machine.

```
uv run --with eppy python tests/ci_check.py
```

Not public: the measured typical day (provided by the corresponding author of Lin et al., *Energy* 2026) and the reference papers. Calibration, sensitivity and hold-out analyses therefore need the local file. The retrofit analysis and the CI check use only public files.

**Tools:** EnergyPlus 26.2, OpenStudio SDK, Ladybug Tools, eppy, Python, GitHub Actions.
