# DesignBuilder integration: MCP server, desktop app and bundled EnergyPlus

This page describes how this repository automates DesignBuilder 2025.1 and reuses the EnergyPlus engine that ships with it. It is written for the DesignBuilder support team, to accompany a request for feedback. The rest of the repository is documented in Chinese; this page is self-contained.

## What it is for

The user builds and edits models in DesignBuilder. An AI assistant (through the Model Context Protocol, MCP) or a small desktop app then runs the simulation in the background and reports the results in plain language: annual energy, end uses, peak demand, unmet hours, and the savings and payback of eight common retrofit measures. The aim is to keep DesignBuilder as the modelling tool, not to replace it.

## Components

| Component | File | Role |
|---|---|---|
| DesignBuilder MCP server | [tools/designbuilder_mcp/server.py](../tools/designbuilder_mcp/server.py) | Eight tools that drive DesignBuilder through the official [db-process](https://github.com/DesignBuilderSoftware/db-process) command line (`/process=`) |
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

`db_zones` and `db_assign_activity` use [DbMcpBridge.cs](../tools/designbuilder_mcp/DbMcpBridge.cs), a C# script that must be added once to each model in Script Manager. Under `/process=` DesignBuilder loads scripts only when a calculation starts, so the job runs in the `Before*IdfGeneration` hooks of a heating design run (about one minute). Setting `ActivityTemplateId` through the API does not load the template's values (the user interface does), so the tool writes up to 37 activity attributes from DesignBuilder's own template library. Tested on the Model Geometry Example: three zones changed, 114 attributes written and read back, and the next annual simulation's IDF used the new occupancy densities and schedules.

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
