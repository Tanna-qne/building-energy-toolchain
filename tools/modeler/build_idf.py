"""輸入幾個數字 -> 建好 EnergyPlus 模型 -> 交給 db_import.py 跑基準與全部措施。

用法(要用 tools/modeler 自己的 venv, 裡面有 OpenStudio 與 Ladybug Tools):
    tools/modeler/.venv/Scripts/python.exe tools/modeler/build_idf.py tools/modeler/example.json
    ... --no-run      只產生 IDF, 不跑模擬

建模全部交給現成套件: dragonfly(量體) + honeybee-energy(DOE 用途負載、構造、空調樣板)
+ honeybee-openstudio(轉 OpenStudio -> IDF)。這裡只負責把參數接起來。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent.parent
# Ladybug Tools 會在 HOME / APPDATA 底下建資料夾, 指到這裡讓整套工具留在工作區內
os.environ["HOME"] = os.environ["APPDATA"] = str(HERE / "home")
(HERE / "home").mkdir(exist_ok=True)

import openstudio  # noqa: E402
from ladybug_geometry.geometry3d import Point3D, Face3D  # noqa: E402
from dragonfly.building import Building  # noqa: E402
from dragonfly.model import Model  # noqa: E402
from dragonfly.windowparameter import SimpleWindowRatio  # noqa: E402
from honeybee_energy.lib.constructionsets import construction_set_by_identifier  # noqa: E402
from honeybee_energy.lib.programtypes import BUILDING_TYPES  # noqa: E402
from honeybee_energy.hvac.allair.vav import VAV  # noqa: E402
from honeybee_energy.hvac.allair.psz import PSZ  # noqa: E402
from honeybee_energy.hvac.allair.ptac import PTAC  # noqa: E402
from honeybee_energy.simulation.parameter import SimulationParameter  # noqa: E402
from honeybee_openstudio.simulation import simulation_parameter_to_openstudio, assign_epw_to_model  # noqa: E402
from honeybee_openstudio.writer import model_to_openstudio  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

EP = Path(r"C:/Users/kiri/AppData/Local/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64")
EPW = EP / "WeatherData" / "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"  # 同 batch_driver.WEATHER
DSH_PY = Path(r"C:/Users/kiri/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe")
INBOX = WS / "db_models" / "inbox"
HVAC_TYPES = {t: cls for cls in (VAV, PSZ, PTAC) for t in cls.EQUIPMENT_TYPES}

DEFAULTS = {
    "shape": "rect",          # rect 或 L
    "stories": 1,
    "floor_height": 3.5,      # m, 樓板到樓板
    "wwr": 0.4,               # 窗牆比
    "building_type": "MediumOffice",
    "climate_zone": 5,        # ASHRAE 氣候區; Chicago 是 5
    "construction": "SteelFramed",
    "hvac": "VAV_Chiller_Boiler",
    "vintage": "ASHRAE_2019",
}


def footprint(p):
    """rect: width × depth; L: 從 width × depth 的右上角挖掉 notch_width × notch_depth。"""
    w, d = p["width"], p["depth"]
    if p["shape"] == "rect":
        pts = [(0, 0), (w, 0), (w, d), (0, d)]
    elif p["shape"] == "L":
        nw, nd = p["notch_width"], p["notch_depth"]
        if not (0 < nw < w and 0 < nd < d):
            raise ValueError("L 形的 notch_width / notch_depth 要小於 width / depth")
        pts = [(0, 0), (w, 0), (w, d - nd), (w - nw, d - nd), (w - nw, d), (0, d)]
    else:
        raise ValueError(f"shape 只支援 rect 或 L, 收到 {p['shape']!r}")
    return Face3D([Point3D(x, y, 0) for x, y in pts])


def build(p):
    if p["building_type"] not in BUILDING_TYPES:
        raise ValueError(f"building_type 要是其中之一: {', '.join(BUILDING_TYPES)}")
    if p["hvac"] not in HVAC_TYPES:
        raise ValueError(f"hvac 要是其中之一: {', '.join(HVAC_TYPES)}")

    # 夠大才切周邊 / 核心區; 太窄切了會產生極小的 zone
    narrow = min(p["width"], p["depth"]) if p["shape"] == "rect" else \
        min(p["width"] - p["notch_width"], p["depth"] - p["notch_depth"])
    offset = 4.5 if narrow > 15 else 0
    bldg = Building.from_footprint(p["name"], [footprint(p)], [p["floor_height"]] * p["stories"],
                                   perimeter_offset=offset, tolerance=0.01)
    bldg.separate_top_bottom_floors()
    bldg.set_outdoor_window_parameters(SimpleWindowRatio(p["wwr"]))

    energy = bldg.properties.energy
    energy.set_all_program_type_from_building_type(p["building_type"])
    energy.construction_set = construction_set_by_identifier(
        f"2019::ClimateZone{p['climate_zone']}::{p['construction']}")
    # conditioned_only 要關掉: 新建的 Room2D 預設沒有 HVAC, 會被當成「非空調」而全部跳過
    energy.set_all_room_2d_hvac(HVAC_TYPES[p["hvac"]](f"{p['name']}_HVAC", p["vintage"], p["hvac"]),
                                conditioned_only=False)

    hb_model = Model(p["name"], [bldg], units="Meters", tolerance=0.01, angle_tolerance=1.0) \
        .to_honeybee("Building", use_multiplier=True, tolerance=0.01)[0]

    # 自動定尺寸需要設計日; 取氣象檔旁 .ddy 的 99.6% 暖房 / 0.4% 冷房
    sim_par = SimulationParameter()
    sim_par.sizing_parameter.add_from_ddy_996_004(str(EPW.with_suffix(".ddy")))
    os_model = simulation_parameter_to_openstudio(sim_par)
    assign_epw_to_model(str(EPW), os_model)
    os_model = model_to_openstudio(hb_model, seed_model=os_model)
    return openstudio.energyplus.ForwardTranslator().translateModel(os_model), hb_model


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    p = {**DEFAULTS, **json.loads(Path(args[0]).read_text(encoding="utf-8"))}
    for k in ("name", "width", "depth"):
        if k not in p:
            raise SystemExit(f"參數檔缺少 {k}")

    try:
        workspace, hb_model = build(p)
    except ValueError as e:  # 參數填錯: 只印原因, 不印 traceback
        raise SystemExit(f"參數錯誤: {e}")
    INBOX.mkdir(parents=True, exist_ok=True)
    out = INBOX / f"{p['name']}.idf"
    workspace.save(openstudio.path(str(out)), True)
    floor = sum(r.floor_area * r.multiplier for r in hb_model.rooms)
    print(f"已產生 {out}(OpenStudio {openstudio.openStudioVersion()} / E+ {openstudio.energyPlusVersion()},"
          f" {len(hb_model.rooms)} 個 zone, 樓地板 {floor:.0f} m²)")

    if "--no-run" not in sys.argv:
        subprocess.run([str(DSH_PY), str(WS / "db_import.py"), str(out)], check=True)


if __name__ == "__main__":
    main()
