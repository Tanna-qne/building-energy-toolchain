# DesignBuilder integration: MCP server, desktop app and bundled EnergyPlus

This page describes how this repository automates DesignBuilder 2025.1 and reuses the EnergyPlus engine that ships with it. It is written for the DesignBuilder support team, to accompany a request for feedback. The rest of the repository is documented in Chinese; this page is self-contained.

## What it is for

The user builds and edits models in DesignBuilder. An AI assistant (through the Model Context Protocol, MCP) or a small desktop app then runs the simulation in the background and reports the results in plain language: annual energy, end uses, peak demand, unmet hours, and the savings and payback of eight common retrofit measures. The aim is to keep DesignBuilder as the modelling tool, not to replace it.

## Components

| Component | File | Role |
|---|---|---|
| DesignBuilder MCP server | [tools/designbuilder_mcp/server.py](../tools/designbuilder_mcp/server.py) | Fifteen tools that drive DesignBuilder through the official [db-process](https://github.com/DesignBuilderSoftware/db-process) command line (`/process=`) |
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
| `db_simulate` | Optionally changes building-level attributes, then runs the annual EnergyPlus simulation inside DesignBuilder and collects `in.idf`, `eplusout.err`, `eplustbl.htm`, `eplusout.eso` from `%LOCALAPPDATA%\DesignBuilder\EnergyPlus` | Yes | Yes (2026-11-13, 2026-11-23) |
| `db_measures_report` / `db_report_status` | Runs eight retrofit measures on the exported IDF in the background and reports savings and payback | No | Yes (2026-11-13, 2026-11-23) |
| `db_calculate` | Heating design, cooling design, daylighting, annual daylighting or CFD through `/process=` | Yes | Not yet |
| `db_create_model` | Builds a dsbXML model from basic parameters (via [honeybee-designbuilder](https://github.com/ladybug-tools/honeybee-designbuilder)) for import into DesignBuilder | No | Not recorded |
| `db_open` | Opens a file in DesignBuilder for the user | Yes | Not recorded |
| `db_zones` | Lists every zone with its block, IDF name, area and *effective* (inherited) activity, occupancy and set points, read through DesignBuilder's API | Yes (headless) | Yes (2026-11-26) |
| `db_assign_activity` | Assigns a space type to zones (by IDF name, block or `*`): previews the change, then writes the activity template id **and** the template's values, saves, and reads them back | Yes (headless) | Yes (2026-11-26) |
| `db_build_model` | Builds a DesignBuilder model from massing parameters (rectangle, L, U, T, courtyard, podium with tower): 4.5 m perimeter zones plus a core on every floor, one use per floor from DesignBuilder's activity templates, and a floor plan page with a zone table | Yes (headless) | Yes (2026-11-26) |
| `db_find_schedules` | Searches DesignBuilder's schedule library by name and shows each schedule's weekday, Saturday and Sunday profile | No | Yes (2026-11-26) |
| `db_assign_schedule` | Assigns existing library schedules (occupancy, lighting, equipment, hot water) to zones: preview, then write, save and read back. It never creates new schedules | Yes (headless) | Yes (2026-11-26) |
| `db_assign_construction` | Applies the ASHRAE 90.1-2019 baseline constructions for a climate zone (default 4A) and building type at building level: walls, roofs, exposed and ground floors, doors, glazing, internal constructions and infiltration. An optional target U for walls, roofs or exposed floors adds a copy of the baseline construction with a thicker or thinner insulation layer | Yes (headless) | Yes (2026-11-26) |
| `db_update_bridge` | Replaces an installed DbMcpBridge script inside a model with the repository version, without opening DesignBuilder's user interface, then runs a test job | Optional test (headless) | Yes (2026-11-26) |

`db_zones` and `db_assign_activity` use [DbMcpBridge.cs](../tools/designbuilder_mcp/DbMcpBridge.cs), a C# script that must be added once to each model in Script Manager. Under `/process=` DesignBuilder loads scripts only when a calculation starts, so the job runs in the `Before*IdfGeneration` hooks of a heating design run (about one minute). Setting `ActivityTemplateId` through the API does not load the template's values (the user interface does), so the tool writes up to 37 activity attributes from DesignBuilder's own template library. Tested on the Model Geometry Example: three zones changed, 114 attributes written and read back, and the next annual simulation's IDF used the new occupancy densities and schedules.

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

## Known limitations

- On this machine DesignBuilder runs as administrator. Every launch shows a Windows UAC prompt, and the tools cannot close DesignBuilder afterwards; the user closes it.
- Only one DesignBuilder instance can run, so tools that open DesignBuilder refuse to start while it is open.
- Completion is detected from the time stamps of `eplusout.err` and `eplustbl.htm`. `db_calculate` assumes the `.dsb` is saved when a calculation finishes; this has not been verified.
- When DesignBuilder stops at a dialog (for example a missing weather file), the tools wait until their timeout and cannot see the dialog.
- Simulation results stored inside the `.dsb` cannot be read directly, so results are taken from the EnergyPlus output folder.
- The bundled engine has no IDF version updater, so IDFs older than 23.1 must be re-exported from DesignBuilder.

## Questions for DesignBuilder

1. Is `/process=` the recommended way to automate DesignBuilder, or is there a scripting or plugin API that would be more robust?
2. Is there a supported way to make DesignBuilder exit when a process chain completes, or an official signal that a simulation or calculation has finished?
3. Is administrator mode required, or can DesignBuilder run normally as a standard user?
4. Can simulation results stored in the `.dsb` be exported (for example to CSV or SQL) from the command line?
5. For heating and cooling design, daylighting and CFD run through `/process=`, how can completion be detected, and can the results be exported?
6. Can dialogs be suppressed during automated runs, or written to a log instead?
7. Is it acceptable to call the bundled `energyplusapi.dll` directly, as described above, on a machine with a valid DesignBuilder licence? Is there anything in the licence terms we should be aware of?
