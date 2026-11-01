"""參數 JSON -> 建好 EnergyPlus 模型(IDF, 放進 db_models/inbox/)。

平常用根目錄的 run_building.py, 它會呼叫這支再接著跑模擬。單獨用(只建模):
    tools/modeler/.venv/Scripts/python.exe tools/modeler/build_idf.py tools/modeler/example.json

建模全部交給現成套件: dragonfly(量體) + honeybee-energy(DOE 用途負載、構造、空調樣板)
+ honeybee-openstudio(轉 OpenStudio -> IDF)。這裡只負責把參數接起來。
不要從這支直接啟動 db_import: 由這個 venv 的 Python 帶起的 E+ Transition 程式會當掉。
"""
import json
import os
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
    "vintage": "ASHRAE_2019",            # 空調樣板的年代(honeybee 轉換器實際不依它設效率, 高效率空調另由 upgrade_hvac 處理)
    "construction_vintage": "2019",      # 牆 / 屋頂 / 窗構造的年代: pre_1980, 1980_2004, 2004 … 2019
}


def construction_set(p, upgrade_envelope=False):
    """基準: {年代}::ClimateZone{cz}::{構造}。upgrade_envelope 只換外牆與屋頂(窗不動, 避免和貼膜混在一起):
    舊年代 -> 同氣候區 2019; 已是 2019 -> 冷兩級氣候區的 2019(保溫更厚)。回傳 (構造組, 說明)。"""
    base_id = f"{p['construction_vintage']}::ClimateZone{p['climate_zone']}::{p['construction']}"
    try:
        base = construction_set_by_identifier(base_id)
    except ValueError:
        raise ValueError(f"找不到構造組 {base_id}(construction_vintage / climate_zone / construction 的組合不存在)")
    if not upgrade_envelope:
        return base, ""
    cz = p["climate_zone"] if str(p["construction_vintage"]) != "2019" else min(int(p["climate_zone"]) + 2, 8)
    up = construction_set_by_identifier(f"2019::ClimateZone{cz}::{p['construction']}")
    wall0, roof0 = base.wall_set.exterior_construction, base.roof_ceiling_set.exterior_construction
    wall1, roof1 = up.wall_set.exterior_construction, up.roof_ceiling_set.exterior_construction
    if wall1.u_factor >= wall0.u_factor and roof1.u_factor >= roof0.u_factor:
        return None, "牆和屋頂已經是構造庫裡最好的保溫等級,沒有可以再升級的"
    cs = base.duplicate()
    cs.identifier = f"{base.identifier}_insulation_upgrade"
    cs.wall_set.exterior_construction = wall1
    cs.roof_ceiling_set.exterior_construction = roof1
    note = (f"外牆 U {wall0.u_factor:.2f} → {wall1.u_factor:.2f}、屋頂 U {roof0.u_factor:.2f} → {roof1.u_factor:.2f} W/m²K"
            f"(換成 2019 年氣候區 {cz} 的保溫規範)")
    return cs, note


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


COP_FACTOR = 1.25         # 高效率空調: 冰水主機 / 冷氣 COP ×1.25
HEATER_EFF = 0.95         # 冷凝式鍋爐 / 瓦斯加熱
FAN_EFF = 0.70            # 風扇總效率下限


def _val(x):
    """OpenStudio 有些 getter 回傳 OptionalDouble。"""
    if hasattr(x, "is_initialized"):
        return x.get() if x.is_initialized() else None
    return x


def upgrade_hvac(os_model):
    """把設備效率直接改成高效率款。honeybee 的轉換器不依 vintage 設效率(試過, 換年代模型完全相同),
    所以用 OpenStudio SDK 改。回傳說明; 沒有可升級的設備回傳空字串。"""
    changes = []

    def bump(objs, get, set_, label, new):
        olds = [v for v in (_val(getattr(o, get)()) for o in objs) if v]
        for o in objs:
            v = _val(getattr(o, get)())
            if v:
                getattr(o, set_)(new(v))
        if olds:
            changes.append(f"{label} {sum(olds) / len(olds):.2f} → {new(sum(olds) / len(olds)):.2f}")

    up = lambda v: round(v * COP_FACTOR, 2)  # noqa: E731
    bump(os_model.getChillerElectricEIRs(), "referenceCOP", "setReferenceCOP", "冰水主機 COP", up)
    bump(os_model.getCoilCoolingDXSingleSpeeds(), "ratedCOP", "setRatedCOP", "冷氣 COP", up)
    bump(os_model.getCoilCoolingDXTwoSpeeds(), "ratedHighSpeedCOP", "setRatedHighSpeedCOP", "冷氣 COP(高速)", up)
    bump(os_model.getCoilCoolingDXTwoSpeeds(), "ratedLowSpeedCOP", "setRatedLowSpeedCOP", "冷氣 COP(低速)", up)
    bump(os_model.getBoilerHotWaters(), "nominalThermalEfficiency", "setNominalThermalEfficiency", "鍋爐效率",
         lambda v: max(v, HEATER_EFF))
    bump(os_model.getCoilHeatingGass(), "gasBurnerEfficiency", "setGasBurnerEfficiency", "瓦斯加熱效率",
         lambda v: max(v, HEATER_EFF))
    for fans in (os_model.getFanVariableVolumes(), os_model.getFanConstantVolumes(), os_model.getFanOnOffs()):
        bump(fans, "fanTotalEfficiency", "setFanTotalEfficiency", "風扇效率", lambda v: max(v, FAN_EFF))
    # 同一個標籤可能出現多次(多種風扇), 只留第一筆
    seen, out = set(), []
    for c in changes:
        key = c.split(" ")[0]
        if key not in seen and c.split(" ")[1] != c.split(" ")[3]:
            seen.add(key)
            out.append(c)
    return "、".join(out)


def build(p, cs=None, hvac_upgrade=False):
    """cs: 指定構造組(加強隔熱的變體用); hvac_upgrade: 設備改成高效率款(高效率空調的變體用)。
    回傳 (IDF workspace, honeybee 模型, 設備升級說明)。"""
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
    energy.construction_set = cs or construction_set(p)[0]
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
    note = upgrade_hvac(os_model) if hvac_upgrade else ""
    return openstudio.energyplus.ForwardTranslator().translateModel(os_model), hb_model, note


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    p = {**DEFAULTS, **json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))}
    for k in ("name", "width", "depth"):
        if k not in p:
            raise SystemExit(f"參數檔缺少 {k}")

    try:
        workspace, hb_model, _ = build(p)
    except ValueError as e:  # 參數填錯: 只印原因, 不印 traceback
        raise SystemExit(f"參數錯誤: {e}")
    INBOX.mkdir(parents=True, exist_ok=True)
    out = INBOX / f"{p['name']}.idf"
    workspace.save(openstudio.path(str(out)), True)
    out.with_suffix(".json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    floor = sum(r.floor_area * r.multiplier for r in hb_model.rooms)

    # 兩個要「換構造 / 換設備」的措施: MCP 沒有對應工具, 用同一組參數重建變體模型。
    # 放在 <name>.variants/(不在 inbox 根目錄, 免得被當成獨立模型), db_import 會讀 variants.json
    vdir = INBOX / f"{p['name']}.variants"
    vdir.mkdir(exist_ok=True)
    for f in vdir.glob("*.idf"):
        f.unlink()
    variants = {}
    cs, note = construction_set(p, upgrade_envelope=True)
    if cs:
        build(p, cs)[0].save(openstudio.path(str(vdir / "insulation.idf")), True)
        variants["insulation"] = {"file": "insulation.idf", "note": note}
    else:
        variants["insulation"] = {"skip": note}
    ws, _, note = build(p, hvac_upgrade=True)
    if note:
        ws.save(openstudio.path(str(vdir / "hvac_eff.idf")), True)
        variants["hvac_eff"] = {"file": "hvac_eff.idf", "note": note}
    else:
        variants["hvac_eff"] = {"skip": "設備效率已經達到高效率款的水準,沒有可以再升級的"}
    (vdir / "variants.json").write_text(json.dumps(variants, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已產生 {out}(OpenStudio {openstudio.openStudioVersion()} / E+ {openstudio.energyPlusVersion()},"
          f" {len(hb_model.rooms)} 個 zone, 樓地板 {floor:.0f} m²)")


if __name__ == "__main__":
    main()
