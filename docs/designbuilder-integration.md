# DesignBuilder integration: MCP server, desktop app and bundled EnergyPlus

This page describes how this repository automates DesignBuilder 2025.1 and reuses the EnergyPlus engine that ships with it. It is written for the DesignBuilder support team, to accompany a request for feedback. The rest of the repository is documented in Chinese; this page is self-contained.

## What it is for

The user builds and edits models in DesignBuilder. An AI assistant (through the Model Context Protocol, MCP) or a small desktop app then runs the simulation in the background and reports the results in plain language: annual energy, end uses, peak demand, unmet hours, and the savings and payback of eight common retrofit measures. The aim is to keep DesignBuilder as the modelling tool, not to replace it.

## Components

| Component | File | Role |
|---|---|---|
| DesignBuilder MCP server | [tools/designbuilder_mcp/server.py](../tools/designbuilder_mcp/server.py) | Eighteen tools that drive DesignBuilder through the official [db-process](https://github.com/DesignBuilderSoftware/db-process) command line (`/process=`) |
| Command-line runner | [tools/designbuilder_mcp/cli.py](../tools/designbuilder_mcp/cli.py) | The same chain without an assistant: simulate a `.dsb`, then run the measures report |
| Desktop app | [tools/app/app.py](../tools/app/app.py) | Pick a `.dsb` / `.idf`, or enter a few building parameters, choose weather, run, open the report |
| Engine adapter | [tools/dbengine/](../tools/dbengine/) | Makes DesignBuilder's bundled EnergyPlus usable from the command line (see below) |
| EnergyPlus-MCP | [LBNL-ETA/EnergyPlus-MCP](https://github.com/LBNL-ETA/EnergyPlus-MCP) + [local patch](../tools/energyplus_mcp/energyplus-mcp.patch) | Applies the retrofit measures to the exported IDF and runs the simulations |
| Installer | [安裝.bat](../安裝.bat) → [tools/setup/install.py](../tools/setup/install.py) | One-click setup on a machine with DesignBuilder; optional MCP registration |

## DesignBuilder MCP tools

All operations work on a copy of the user's model in `db_models/dbmcp/<model>/`; the original `.dsb` is never modified.

| Tool | What it does | Opens DesignBuilder | Tested |
|---|---|---|---|
| `db_status` | Whether DesignBuilder is running, the executable path, licence expiry date | No | Yes (2026-11-23) |
| `db_inspect` | Exports the model to dsbXML (`/process=` export) and lists buildings, blocks, zones and building-level attributes | First call only (then cached) | Yes (2026-11-13) |
| `db_simulate` | Optionally changes building-level attributes, then runs the annual EnergyPlus simulation inside DesignBuilder and collects `in.idf`, `eplusout.err`, `eplustbl.htm`, `eplusout.eso`, `eplusout.mtr` from `%LOCALAPPDATA%\DesignBuilder\EnergyPlus`. `hourly=True` turns on hourly output first | Yes | Yes (2026-11-13, 2026-11-23, 2026-11-27) |
| `db_results` | Extracts hourly, daily or monthly results from a `db_simulate` output folder into CSV: building meters (electricity, lighting, equipment, heating, cooling, gas), outdoor temperature, and per-zone air and operative temperature, humidity and ideal-loads heating / cooling. It only reads what the simulation wrote; missing variables are reported with the frequencies that do exist | No | Yes (2026-11-27) |
| `db_measures_report` / `db_report_status` | Runs eight retrofit measures on the exported IDF in the background and reports savings and payback | No | Yes (2026-11-13, 2026-11-23) |
| `db_calculate` | Heating design, cooling design, daylighting, annual daylighting or CFD through `/process=` | Yes | Not yet |
| `db_create_model` | Builds a dsbXML model from basic parameters (via [honeybee-designbuilder](https://github.com/ladybug-tools/honeybee-designbuilder)) for import into DesignBuilder | No | Not recorded |
| `db_open` | Opens a file in DesignBuilder for the user | Yes | Not recorded |
| `db_zones` | Lists every zone with its block, IDF name, area and *effective* (inherited) activity, occupancy and set points, read through DesignBuilder's API | Yes (headless) | Yes (2026-11-26) |
| `db_assign_activity` | Assigns a space type to zones (by IDF name, block or `*`): previews the change, then writes the activity template id **and** the template's values, saves, and reads them back | Yes (headless) | Yes (2026-11-26) |
| `db_build_model` | Builds a DesignBuilder model from massing parameters (rectangle, L, U, T, courtyard, podium with tower): 4.5 m perimeter zones plus a core on every floor, one use per floor from DesignBuilder's activity templates, and a floor plan page with a zone table | Yes (headless) | Yes (2026-11-26) |
| `db_find_locations` | Searches DesignBuilder's location library (6,368 locations) and shows the ASHRAE climate zone, coordinates, design temperatures and whether the weather file is on this computer | No | Yes (2026-11-27) |
| `db_set_location` | Applies a library location to the model: coordinates, time zone, elevation, climate zone, design conditions and hourly weather file (343 site attributes). A weather file that is not on this computer can be supplied as an EPW, which is checked against the location's coordinates | Yes (headless) | Yes (2026-11-27) |
| `db_find_schedules` | Searches DesignBuilder's schedule library by name and shows each schedule's weekday, Saturday and Sunday profile | No | Yes (2026-11-26) |
| `db_assign_schedule` | Assigns existing library schedules (occupancy, lighting, equipment, hot water) to zones: preview, then write, save and read back. It never creates new schedules | Yes (headless) | Yes (2026-11-26) |
| `db_assign_construction` | Applies the ASHRAE 90.1-2019 baseline constructions for a climate zone (default 4A) and building type at building level: walls, roofs, exposed and ground floors, doors, glazing, internal constructions and infiltration. An optional target U for walls, roofs or exposed floors adds a copy of the baseline construction with a thicker or thinner insulation layer | Yes (headless) | Yes (2026-11-26) |
| `db_update_bridge` | Replaces an installed DbMcpBridge script inside a model with the repository version, without opening DesignBuilder's user interface, then runs a test job | Optional test (headless) | Yes (2026-11-26) |

`db_zones` and `db_assign_activity` use [DbMcpBridge.cs](../tools/designbuilder_mcp/DbMcpBridge.cs), a C# script that the model must contain: it is added once in Script Manager, or inherited by models made with `db_build_model` (see problems 7 and 11 below). Under `/process=` DesignBuilder loads scripts only when a calculation starts, so the job runs in the `Before*IdfGeneration` hooks of a heating design run (about one minute). Setting `ActivityTemplateId` through the API does not load the template's values (the user interface does), so the tool writes up to 37 activity attributes from DesignBuilder's own template library. Tested on the Model Geometry Example: three zones changed, 114 attributes written and read back, and the next annual simulation's IDF used the new occupancy densities and schedules.

`db_assign_activity` also sets lighting. Each space type maps to an ASHRAE 90.1-2019 Table G3.7 lighting template from DesignBuilder's library, for example 11.84 W/m² for an open plan office. The tool writes that template and its values, and sets `FluorescentLightingUnits` to `1-Watts per m2`. With the per-100-lux method, DesignBuilder would multiply by the activity's target illuminance: the templates' W/m²-100 lux values are the W/m² values divided by 3, i.e. based on 300 lux. New models otherwise inherit DesignBuilder's "Reference" lighting template (15 W/m²).

The schedule and construction tools take their values only from DesignBuilder's library, so the assistant chooses among existing items and never types numbers. The exception is a target U, which the user gives. Findings from testing on the same model:

- The model's tables (`Site.GetTable`) contain the whole library (2717 constructions, 4274 schedules), so library ids can be used directly.
- When DesignBuilder generates the IDF, walls use `WallConstr`, but flat roofs, exposed floors and ground floors use the `Combined*` attributes (for example `CombinedFlatRoofConstr`), not `FlatRoofConstr`. The tool writes both kinds as the template defines them.
- `Table.GetUniqueId()` returned 0, so the bridge gives a new record the largest existing id plus one (at least 10000).
- After applying the CZ4 baseline with a wall target of U 0.4, the annual simulation's IDF had a 76 mm wall insulation layer, the ASHRAE roof and floor layers, an F-factor ground floor, glazing U 3.24 and SHGC 0.39, 0.7 ach infiltration and the newly assigned occupancy and hot water schedules.

`db_build_model` generates the massing with [dragonfly](https://github.com/ladybug-tools/dragonfly-core) and writes dsbXML with honeybee-designbuilder ([massing.py](../tools/designbuilder_mcp/massing.py)). The bridge then imports it into a copy of a template model that already contains DbMcpBridge (`Site.ImportXMLFile`) and saves it under the new name (`DocumentFileOperations.SaveAs`). Three findings from testing:

- Importing replaces the whole site, including the model's own scripts (`EMSPrograms` records from 10000) and the `EMSOn` / `EMSId` building attributes that select them. The bridge keeps these and writes them back after the import, so the new model can be used with the bridge tools straight away, without a manual install.
- If the calculation that loaded the script keeps running after the import, DesignBuilder fails ("Automation error"). This happens both before IDF generation and after the calculation. The bridge therefore ends the DesignBuilder process once the job output is written.
- `SaveAs` writes an uncompressed `.skh` file (and does not add a path separator between the folder and the name). The tool packs it into a `.dsb`, which is a zip archive holding `~name.skh`.

The ASHRAE 2019 baseline ground floor is an F-factor construction, and DesignBuilder refuses to calculate a zone whose ground floor has no exposed perimeter (a core zone): "has zero exposed perimeter. Replace the F-factor construction with a layered construction". `db_assign_construction` therefore adds a layered copy of the same construction and, through the bridge, gives it only to zones whose ground floor has no exposed perimeter (`Adjacency.ExposedWallGroundFloorLength`). Other ground floor zones keep the F-factor construction. Tested on a two-storey U-shaped model with 18 zones: one core zone was switched, and the annual simulation ran with eight F-factor ground floors.

By default DesignBuilder requests only monthly and run period output. `db_simulate(hourly=True)` first sets the site attribute `HourlyOutput` through the bridge and saves the model (about one minute), then simulates. Setting the attribute in the `BeforeEnergyIdfGeneration` hook of the simulation itself only takes effect from the next run: DesignBuilder has already chosen the output variables by then. On every run with the bridge it also saves `zones.json`, which maps zone handles to zone names: with an evaluation licence the IDF uses numeric names (zone handles) regardless of `IDFNameType`. Meters are written to `eplusout.mtr`, which `db_simulate` now collects as well. `db_results` checks each meter by comparing the sum of its hourly, daily or monthly values with the run period value.

Zones are named `F03 North` / `F03 Core`, and blocks are named by floor (`F03`). The IDF names only appear after DesignBuilder's first calculation, so the tools also accept zone names.

A `.dsb` file is a zip archive holding one `.skh` file, in which the script is stored as a length-prefixed string (newlines as `^`, `#` as `_HASH_`). `db_update_bridge` relies on this to update a script that is already installed. It backs up the model first and refuses to change anything if the layout is not as expected. Installing the script in a model for the first time still has to be done in Script Manager.

Example of a `db_simulate` result (Model Geometry Example, London Gatwick weather, 2026-11-23): annual site energy 43,900 kWh, 134 kWh/m², interior lighting 58% of the total, peak electricity 12.1 kW, 2 unmet heating hours.

## Using DesignBuilder's bundled EnergyPlus

DesignBuilder 2025.1 ships EnergyPlus 23.1 as `energyplusapi.dll` (in `%LOCALAPPDATA%\DesignBuilder\EnergyPlus`, also in `C:\ProgramData\DesignBuilder\JobServer\versions\DLL23.1.0.002-64.zip`), without `energyplus.exe` or ReadVarsESO. To avoid installing a second copy of EnergyPlus, the setup step:

1. copies the DLL, `Energy+.idd` and `ExpandObjects.exe` from the DesignBuilder installation into `tools/dbengine/engine/`;
2. compiles a small `energyplus.exe` ([energyplus_shim.cs](../tools/dbengine/energyplus_shim.cs)) with the C# compiler included in Windows. It calls `stateNew()` and `energyplus(state, argc, argv)` from the DLL with the usual command-line arguments, and writes the CSV files that ReadVarsESO would produce;
3. copies the EPW files from `C:\ProgramData\DesignBuilder\Weather Data`.

Results match the official EnergyPlus build. For an IDF exported by DesignBuilder, annual electricity is 39,792 kWh with both the bundled 23.1 engine and EnergyPlus 26.2, and the eight retrofit measures give identical percentage savings. Parametric models for this engine are generated with OpenStudio 3.6.1, which targets EnergyPlus 23.1.

DesignBuilder itself is not modified, and nothing in its installation folders is changed; the files are copied.

## Problems found and how they were handled

Every problem below came up while building and testing the tools on DesignBuilder 2025.1 (evaluation licence, Windows 11). Each entry gives what was seen, the cause, what the tools now do, and how the fix was checked. Several of them are silent: DesignBuilder reports success but the model or the results are not what was asked for. The tools therefore read every write back and check results against totals.

### Running DesignBuilder unattended

1. **A UAC prompt on every launch, and DesignBuilder could not be closed.** Cause: a Windows compatibility flag (`RUNASADMIN` in `HKCU\...\AppCompatFlags\Layers`) made DesignBuilder run as administrator. A copy started from the assistant's sandbox was invisible to the user and could only be cleared by rebooting. Fix: the flag was removed (a backup of the registry value is kept). DesignBuilder runs as a standard user without problems. Check: the tools now close DesignBuilder themselves after every run.
2. **No signal that a run has finished.** File time stamps were unreliable, and heating and cooling design write no output files. Fix: the tools read DesignBuilder's own diagnostics log (`%LOCALAPPDATA%\DesignBuilder\Diagnostics`). A run is finished when the last batch command has been processed and DesignBuilder has released the model ("Removing 'In Use' file"). Check: XML export, annual simulation, and heating and cooling design all finish and close automatically.
3. **Runs that hang on a dialog.** Under `/process=` DesignBuilder has no main window, but dialogs (missing file, model errors) still appear and wait for a click. Fix: if the log does not grow for 300 seconds, the tool closes DesignBuilder and reports the last log lines. The dialog's own text cannot be read.

### Scripting API (DbMcpBridge)

4. **Changing a template id does not change the values.** Setting `ActivityTemplateId` through the API stores the id but not the template's values (the user interface loads them). The same applies to construction and lighting templates. Fix: the tools write every attribute of the template themselves (up to 37 for an activity), then read them back. Check: the values appear in the next simulation's IDF.
5. **Scripts do not run when a model is only opened.** Under `/process=` scripts are loaded when a calculation starts. Fix: jobs run in the `Before*IdfGeneration` hooks of a heating design calculation, which takes about a minute per job.
6. **The script editor corrupts code.** It drops non-ASCII characters and rewrites `||` as `_OR_` and `#` as `_HASH_`. Fix: the bridge is kept ASCII only and avoids `||`.
7. **Each model needs the script installed by hand.** Fix: the `.dsb` format was worked out (a zip archive holding one `.skh` file, with the script stored as a length-prefixed string). `db_update_bridge` replaces an installed script without the user interface, and models made by `db_build_model` inherit the script. Check: three models updated, and each ran a test job afterwards.
8. **New records got id 0, and the readback still matched.** `Table.GetUniqueId()` returned 0. A copied construction was saved as id 0 and the wall pointed to it; checking the building attributes against the values written could not catch this. Fix: the bridge uses the largest existing id plus one (at least 10000), and the tool rejects ids that are not new. The affected model was restored from its backup.
9. **Importing a model crashes DesignBuilder.** `Site.ImportXMLFile` during the calculation that loaded the script ends in "Automation error / object variable not set". This happened both before IDF generation and after the calculation. `Save()` returned true but nothing was written, because the model is only written to disk when the batch ends normally. Fix: after the import the bridge calls `SaveAs` and then ends the DesignBuilder process itself.
10. **`SaveAs` does not write a `.dsb`.** It writes an uncompressed `.skh`, and joins the folder and the file name without a separator. Fix: the tool passes the folder with a trailing backslash and packs the `.skh` into a `.dsb` (zip). Check: the new file opens and runs in DesignBuilder.
11. **Importing deletes the model's scripts.** The import replaces the whole site, including the `EMSPrograms` records and the `EMSOn` / `EMSId` switches that select the script. Fix: the bridge copies them before the import and writes them back afterwards.

### Model data

12. **Stored U-values in the library are out of date** for many ASHRAE 2013–2019 constructions. Fix: U is computed from the material layers and DesignBuilder's surface resistances, and the stored value is not used.
13. **Roofs and floors use attributes other than the obvious ones.** In the generated IDF, walls come from `WallConstr`, but flat roofs, exposed floors and ground floors come from `CombinedFlatRoofConstr`, `CombinedExternalFloorConstr` and `CombinedGroundFloorConstr`, not `FlatRoofConstr`. This was found by matching the IDF material layers to the model's construction records.
14. **Core zones cannot be simulated with the ASHRAE ground floor.** The 2019 baseline slab is an F-factor construction, and DesignBuilder refuses a zone whose ground floor has no exposed perimeter ("has zero exposed perimeter. Replace the F-factor construction with a layered construction"). Fix: those zones get a layered copy of the same slab; the bridge finds them from `Adjacency.ExposedWallGroundFloorLength`. Check: a U-shaped model with one core zone simulated with eight F-factor ground floors.
15. **New models were lit at 15 W/m².** They inherited DesignBuilder's "Reference" lighting template. Fix: each use gets its ASHRAE 90.1-2019 Table G3.7 lighting template, with the units set to W/m². With the per-100-lux method DesignBuilder would multiply by the target illuminance, which gives 19.7 W/m² for a 500 lux office. Check: the IDF had 11.84 W/m² (office) and 13.99 W/m² (lobby); lighting energy fell by 14%.
16. **The ASHRAE activity templates use an HVAC availability schedule for lighting.** Most ASHRAE 90.1 templates in the library point their lighting schedule to "ASHRAE 90.1 HVAC Availability - …" (full on throughout the opening hours, about 4,800 hours a year for an office). The laboratory template points to the occupancy schedule. The library also holds the matching "ASHRAE 90.1 Lighting Receptacle - …" schedule, usually with the id one lower (for example 6025 vs 6026), so this looks like an off-by-one error in the library data. Fix: `db_assign_activity` replaces the lighting schedule with the Lighting Receptacle schedule of the same family (18 of the 25 space types) and lists every replacement in its preview. Check: on the U-shaped test model all 18 zones used schedule 6025 in the IDF. Lighting energy fell from 137,849 to 83,942 kWh (−39%) and heating rose from 27,196 to 36,966 kWh, as expected with lower internal gains. The site EUI went from 105 to 84 kWh/m². The remaining lighting energy matches 12.9 W/m² × 2,912 full-load hours.

### Location and weather

17. **A location template's values are not loaded through the API either** (see problem 4). Fix: `db_set_location` writes all 343 fields of the location template as site attributes: coordinates, time zone, elevation, climate zone, monthly design conditions and weather file. Three fields (`WMO`, `Pressure`, `KoppenClass`) read back as `UNKNOWN` because the site has no such attributes. They are reported as not written, not as errors. Check: after switching a model from San Francisco to Chicago O'Hare, the IDF had Chicago's coordinates, time zone (−6), elevation and design days (winter −18.6 °C), and the Chicago weather file. The EUI went from 84 to 193 kWh/m², mostly heating.
18. **Most weather files are not on the computer.** The library lists 3,178 weather files, but `IsProvided` means DesignBuilder can download a file, not that it is present; only three EPW files were in `C:\ProgramData\DesignBuilder\Weather Data`. A local file's name can also differ from the library name (`USA_IL_CHICAGO-OHARE INTL AP_TMY3.epw` vs `USA_IL_CHICAGO OHARE INTL AP_TMY3.epw`). What DesignBuilder does about a missing file during an unattended run was not tested, because it could stop at a dialog. Fix: the tools check for the exact file name and refuse to apply a location without it. The user can download the file in DesignBuilder or give an EPW file. The EPW's coordinates must be within 0.5° of the location's, and the file is copied under the name DesignBuilder expects.
19. **Time zone ids do not follow the order of `TimeZoneCodes.txt`.** San Francisco (2) matched its row, but London (77) did not. The tools do not show a time zone label; the time zone is checked in the generated IDF instead.

### Building models from parameters

20. **dragonfly did not expand repeated floors.** `separate_mid_floors` left a four-storey tower as one floor ×2. Fix: repeated floors are copied and moved up one by one, so every floor has real zones.
21. **Zone names are lost.** The writer used numbers as zone titles. Fix: zones are named `F03 North` / `F03 Core` before writing, and blocks `F03`. Imported zones have no IDF names until the first calculation, so the tools accept zone names as well.
22. **The floor areas look 5% short.** DesignBuilder reports net floor area (inside the walls), while the massing uses the outline. This is expected and is not treated as an error.

### Results

23. **No hourly output by default.** DesignBuilder requests monthly and run period values only. Setting `HourlyOutput` inside the simulation's own hook only takes effect from the next run. Fix: `db_simulate(hourly=True)` sets it and saves first. Check: the first hourly run of a new model produced 8,760 rows.
24. **Zones are numbers in the results.** With an evaluation licence the IDF uses zone handles as names, regardless of `IDFNameType`. Fix: every simulation with the bridge saves `zones.json` (handle → zone name).
25. **Building meters are not in the `.eso`.** EnergyPlus writes them to `eplusout.mtr`, which is now collected too. DesignBuilder also appends `,ON` to some frequencies in the `.eso` dictionary (`!Hourly,ON`), which the reader handles.
26. **Totals are checked.** For every meter, `db_results` compares the sum of the hourly, daily or monthly values with EnergyPlus's run period value. They have matched in all tests (for example 195,416.7 kWh total electricity).

## Known limitations

- Only one DesignBuilder instance can run, so tools that open DesignBuilder refuse to start while it is open.
- When DesignBuilder stops at a dialog, the tools notice only after 300 seconds without progress, and cannot read the dialog.
- Every bridge job runs a heating design calculation, which takes about a minute. Building a model and assigning uses takes four to five minutes.
- A model needs the bridge script before the bridge tools work. It can be installed once in Script Manager, or the model can be created with `db_build_model`. `db_update_bridge` depends on the undocumented `.dsb` layout.
- Weather files that are not on this computer must be downloaded in DesignBuilder or supplied as an EPW file; the tools do not download them. Without `location`, new models keep the template's location (San Francisco).
- Results stored inside the `.dsb` (design loads, daylighting, CFD) cannot be read, and `db_calculate` has not been tested.
- `db_results` writes CSV only (no `.xlsx`).
- The bundled engine has no IDF version updater, so IDFs older than 23.1 must be re-exported from DesignBuilder.

## Questions for DesignBuilder

1. Is `/process=` the recommended way to automate DesignBuilder, or is there a scripting or plugin API that would be more robust?
2. Is there a supported way to make DesignBuilder exit when a process chain completes, or an official signal that a simulation or calculation has finished?
3. Can simulation results stored in the `.dsb` be exported (for example to CSV or SQL) from the command line?
4. For heating and cooling design, daylighting and CFD run through `/process=`, how can completion be detected, and can the results be exported?
5. Can dialogs be suppressed during automated runs, or written to a log instead?
6. Is there a supported way to load a template's values through the API (problem 4), to import a dsbXML file from a script without the crash in problem 9, and to install a script in a model without the user interface?
7. Is `Table.GetUniqueId()` expected to return 0 (problem 8)?
8. Are the lighting schedules of the ASHRAE 90.1 activity templates intentional (problem 16)?
9. Is it acceptable to call the bundled `energyplusapi.dll` directly, as described above, on a machine with a valid DesignBuilder licence? Is there anything in the licence terms we should be aware of?
