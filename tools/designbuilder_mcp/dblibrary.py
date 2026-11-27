"""讀 DesignBuilder 本機範本庫(%LOCALAPPDATA%\\DesignBuilder\\Data\\*.dat), 給自動配置熱區用途、作息、圍護結構用。

只讀不改。檔案格式: 第 1 行是屬性編號, 第 2 行是欄位名, 之後每行一筆, 欄位用 "#" 分隔(值前後有空白填充)。
所有數值都直接取自 DB 的範本庫; 這個模組不自己編數字。

    python tools/designbuilder_mcp/dblibrary.py            # 印出各表筆數與幾個例子
"""
import os
import sys
from functools import lru_cache
from pathlib import Path

DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "DesignBuilder" / "Data"


US_ACTIVITY_SOURCE = "ASHRAE 90.1 and ASHRAE 62.1 - 2019"


@lru_cache(maxsize=None)
def table(name):
    """{Id: {欄位: 值字串}}; 讀不到檔回傳空 dict。"""
    path = DATA / f"{name}.dat"
    if not path.exists():
        return {}
    lines = path.read_text(encoding="latin-1").splitlines()
    fields = [f.strip() for f in lines[1].split("#")[1:]]
    out = {}
    for line in lines[2:]:
        if not line.lstrip().startswith("#"):
            continue
        values = [v.strip() for v in line.split("#")[1:]]
        row = dict(zip(fields, values))
        if row.get("Id"):
            out[row["Id"]] = row
    return out


def num(row, key):
    try:
        return float(row.get(key, ""))
    except ValueError:
        return None


def name_of(tbl, id_):
    return table(tbl).get(str(id_), {}).get("Name", "")



# 中文用途 → DB 活動範本名稱(美國預設: ASHRAE 90.1 / 62.1-2019 的 SI 版)。
# 只存名稱, 編號在執行時從使用者本機的 DB 範本庫查; ASHRAE 沒有的類型才用 DB 庫裡其他來源, 並標明。
SPACE_TYPES = {
    "辦公": "Office Buildings - Office space [SI]",
    "會議": "General - Conference/Meeting [SI]",
    "大廳": "Office Buildings - Main entry lobby [SI]",
    "接待": "Office Buildings - Reception area [SI]",
    "走廊": "General - Corridor [SI]",
    "樓梯": "General - Corridor [SI]",
    "茶水間": "Office Buildings - Breakroom [SI]",
    "儲藏": "Office Buildings - Occupiable storage room for dry materials [SI]",
    "廁所": "Toilet",                       # ASHRAE 62.1 把廁所當排風空間, 沒有活動範本; 用 DB 庫裡英國 NCM 的
    "機房": "Light plant room",             # 同上, 英國 NCM
    "商店": "Retail - Sales (others) [SI]",
    "超市": "Retail - Supermarket [SI]",
    "商場公共區": "Retail - Mall common area [SI]",
    "餐廳": "Food and Beverage Service - Restaurant dining room [SI]",
    "快餐": "Food and Beverage Service - Cafeteria/fast-food dining [SI]",
    "廚房": "Food and Beverage Service - Kitchen (cooking) [SI]",
    "教室": "Educational Facilities - Classroom (age 9 plus) [SI]",
    "圖書館": "Educational Facilities - Libraries [SI]",
    "實驗室": "Educational Facilities - Science laboratory [SI]",
    "客房": "Hotels, Motels, Resorts, Dormitories - Bedroom/living room [SI]",
    "住宅": "Transient Residential - Dwelling unit [SI]",
    "倉庫": "Misc. Spaces - Warehouse [SI]",
    "候車": "Misc. Spaces - Transportation waiting [SI]",
    "健身房": "Sports and Entertainment - Health club/weight room [SI]",
    "診間": "Outpatient Health Care Facilities - General examination room [SI]",
}


def activity_by_name(name, source=US_ACTIVITY_SOURCE):
    """同一個名稱在 ASHRAE 2007 / 2010 / 2013 / 2016 / 2019 各有一份(2019 還有兩套), 先找指定來源,
    沒有才用其他來源; 同來源取編號最小的那份。"""
    rows = [r for r in table("ActivityTemplates").values() if r.get("Name") == name]
    pick = [r for r in rows if r.get("Source") == source] or rows
    return min(pick, key=lambda r: int(r["Id"])) if pick else None


def resolve_space(label):
    """中文用途 → DB 活動範本(row)。不認得的用途拋 KeyError, 讓呼叫端列出可選清單, 不自行猜。"""
    if label not in SPACE_TYPES:
        raise KeyError(f"不認得的用途「{label}」; 可用的有: {'、'.join(SPACE_TYPES)}")
    row = activity_by_name(SPACE_TYPES[label])
    if row is None:
        raise KeyError(f"本機 DB 範本庫裡找不到「{SPACE_TYPES[label]}」")
    return row


# 換活動範本時要一起寫入熱區的屬性(DB 介面換範本會自動載入這些, API 只改範本編號不會)。
# 名稱同時是範本庫欄位名與熱區屬性名; 只列影響模擬的項目, 不含名稱、來源、各國規範專用欄位。
ACTIVITY_ATTRIBUTES = (
    "OccupancyValue", "OccupancySchedule", "MetabolicRateId", "MetabolicFactor", "OccupancyLatentFraction",
    "OccupancyDaysWeek", "UseHolidays",
    "HeatingDesignSetPointTemperature", "ActiveCoolingDesignSetPointTemperature",
    "HeatingSetBackSetPointTemperature", "CoolingSetBackSetPointTemperature",
    "NatVentSetPointTemperature", "MechVentSetPointTemperature",
    "MinFreshAir", "MinFreshAirPerArea", "TargetIlluminance1", "FluorescentLightingSchedule",
    "ComputersOn", "ComputersValue", "ComputersSchedule", "EquipmentOn", "EquipmentValue", "EquipmentSchedule",
    "MiscellaneousOn", "MiscellaneousValue", "MiscellaneousSchedule", "CateringOn", "CateringValue", "CateringSchedule",
    "ProcessOn", "ProcessValue", "ProcessSchedule", "DHWOn", "DHWValue", "DHWSchedule", "HUM_MIN", "HUM_MAX",
)


# 中文用途 → ASHRAE 90.1-2019 Table G3.7(Appendix G 基準照明功率密度)的空間類型, DB 照明範本庫裡的名稱(去掉結尾的數值)。
# 一般空間用 Common Space, 特定建築類型才有的用 Building-Specific Space
LIGHTING_SOURCE = "ASHRAE 90.1-2019, Table G3.7"
LIGHTING_TYPES = {
    "辦公": "Common Space, Office - Open plan",
    "會議": "Common Space, Conference/Meeting/Multipurpose Room",
    "大廳": "Common Space, Lobby - All others",
    "接待": "Common Space, Lobby - All others",
    "走廊": "Common Space, Corridor - All others",
    "樓梯": "Common Space, Stairwell",
    "茶水間": "Common Space, Lounge/Breakroom - All others",
    "儲藏": "Common Space, Storage Room - >=50 ft2 (4.6 m2)",
    "廁所": "Common Space, Restroom - All others",
    "機房": "Common Space, Electrical/Mechanical Room",
    "商店": "Common Space, Sales Area",
    "超市": "Common Space, Sales Area",
    "商場公共區": "Building-Specific Space, Retail Facilities - Mall concourse",
    "餐廳": "Common Space, Dining Area - Family dining",
    "快餐": "Common Space, Dining Area - Cafeteria or fast food dining",
    "廚房": "Common Space, Food Preparation Area",
    "教室": "Common Space, Classroom/Lecture Hall/Training Room - All others",
    "圖書館": "Building-Specific Space, Library - Reading area",
    "實驗室": "Common Space, Laboratory - All others",
    "客房": "Common Space, Guest Room",
    "住宅": "Common Space, Dwelling Unit",
    "倉庫": "Building-Specific Space, Warehouse - Storage Area - Medium to bulky, palletized items",
    "候車": "Common Space, Audience Seating Area - Transportation Facility",
    "健身房": "Building-Specific Space, Gymnasium/Fitness Center - Exercise area",
    "診間": "Building-Specific Space, Healthcare Facility - Exam/treatment room",
}
# 換照明範本時寫入熱區的屬性(API 只改範本編號不會帶入數值)。計算方式固定為 W/m², DB 才直接用 G3.7 的功率密度;
# 若用「每 100 lux」, DB 會再乘活動範本的照度(範本裡的 W/m²-100lux 是以 300 lux 換算的)
LIGHTING_ATTRIBUTES = ("TungstenLightingOn", "FluorescentLightingOn", "FluorescentLightingValue",
                       "FluorescentLightingWattsPerM2Per100Lux", "LightingControlOn", "LightingControlType",
                       "LightingReturnAirFraction", "LightingRadiantFraction", "LightingVisibleFraction", "LuminaireType")


def lighting_for_space(label):
    """中文用途 → ASHRAE 2019 G3.7 照明範本 row; 對照表外或庫裡找不到回傳 None。"""
    want = LIGHTING_TYPES.get(label)
    if not want:
        return None
    return next((r for r in table("LightingTemplates").values()
                 if r.get("Source") == LIGHTING_SOURCE and r["Name"].rsplit(",", 1)[0] == want), None)


def lighting_attributes(row):
    return ([("LightingTemplateId", row["Id"]), ("FluorescentLightingUnits", "1-Watts per m2")] +
            [(k, row[k]) for k in LIGHTING_ATTRIBUTES if row.get(k, "") != ""])


def lighting_schedule_fix(row):
    """DB 的 ASHRAE 活動範本多半把照明作息指到同系列的「HVAC Availability」(空調可用時段, 營業時間內全開),
    實驗室則指到「Occupancy」; 庫裡同系列有 ASHRAE 90.1 User's Manual 的「Lighting Receptacle」照明作息。
    回傳 (改用的作息 row, 原作息名稱); 不需要修正回傳 None。"""
    s = table("Schedules")
    cur = s.get(row.get("FluorescentLightingSchedule", ""), {}).get("Name", "")
    for prefix in ("ASHRAE 90.1 HVAC Availability - ", "ASHRAE 90.1 Occupancy - "):
        if cur.startswith(prefix):
            family = cur[len(prefix):]
            fix = next((r for r in s.values() if r["Name"] == f"ASHRAE 90.1 Lighting Receptacle - {family}"), None)
            if fix:
                return fix, cur
    return None


def activity_attributes(row):
    """範本 row → [(屬性, 值)]; 範本裡空白的欄位不寫(沿用熱區原值)。照明作息依 lighting_schedule_fix 修正。"""
    fix = lighting_schedule_fix(row)
    return [(k, fix[0]["Id"] if fix and k == "FluorescentLightingSchedule" else row[k])
            for k in ACTIVITY_ATTRIBUTES if row.get(k, "") != ""]


def activity_label(template_id):
    """範本編號 → 「中文用途(範本名)」; 對照表外的範本只回傳範本名。"""
    row = table("ActivityTemplates").get(str(template_id))
    if not row:
        return f"#{template_id}(範本庫裡找不到)"
    for label, name in SPACE_TYPES.items():
        if name == row["Name"]:
            return f"{label}({row['Name']})"
    return row["Name"]


def _hours_from_compact(text, day):
    """Schedule:Compact(DB 用 "|^" 分行)裡某一類日子的 24 個逐時值。day: Weekdays / Saturday / Sunday。"""
    lines = [x.strip().rstrip(",;") for x in text.replace("|^", "\n").splitlines() if x.strip()]
    blocks, cur = [], None
    for ln in lines:
        if ln.lower().startswith("for:"):
            cur = (ln[4:].strip().split(), [])
            blocks.append(cur)
        elif ln.lower().startswith("until:") and cur is not None:
            t = ln.split(":", 1)[1].strip()
            hh, rest = t.split(":", 1)
            mm, val = rest.split(",", 1) if "," in rest else (rest, "")
            cur[1].append((int(hh) + int(mm.strip()[:2] or 0) / 60, float(val)))
    def hours(names):
        for want in names:
            for days, steps in blocks:
                if want in days and steps:
                    out, h = [], 0
                    for end, val in steps:
                        while h < end and h < 24:
                            out.append(val)
                            h += 1
                    return out + [steps[-1][1]] * (24 - len(out))
        return None

    if day == "Weekdays":
        got = hours(("Weekdays",))
        if got:
            return got
        # 有些作息逐日不同(例如會議室): 平日取週一到週五的平均
        each = [hours((d, "AllDays", "AllOtherDays")) for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")]
        return [sum(v) / 5 for v in zip(*each)] if all(each) else hours(("AllDays", "AllOtherDays"))
    return hours({"Saturday": ("Saturday", "Weekends", "AllDays", "AllOtherDays"),
                  "Sunday": ("Sunday", "Weekends", "Holidays", "AllDays", "AllOtherDays")}[day])


def schedule_hours(schedule_id):
    """{平日, 週六, 週日: 24 個 0–1 的值}; 讀不出來回傳 None。"""
    s = table("Schedules").get(str(schedule_id))
    if not s:
        return None
    if s.get("CompactData"):
        out = {k: _hours_from_compact(s["CompactData"], d) for k, d in
               (("平日", "Weekdays"), ("週六", "Saturday"), ("週日", "Sunday"))}
        return out if all(out.values()) else None
    prof = table("Profiles")
    out = {}
    for k, col in (("平日", "JanMon"), ("週六", "JanSat"), ("週日", "JanSun")):
        p = prof.get(s.get(col, ""))
        if not p or not p.get("Data"):
            return None
        vals = [float(x) for x in p["Data"].split(",")]
        out[k] = [v / 100 if max(vals) > 1 else v for v in vals][:24]
    return out


BARS = " ▁▂▃▄▅▆▇█"


def spark(values):
    """24 個 0–1 的值 → 一行文字小圖, 讓作息在表格裡一眼看得出來。"""
    return "".join(BARS[min(8, max(0, round(v * 8)))] for v in values)


def activity_summary(row):
    sch = schedule_hours(row.get("OccupancySchedule", ""))
    return {
        "id": int(row["Id"]), "name": row["Name"], "source": row.get("Source", ""),
        "people_per_m2": num(row, "OccupancyValue"), "equipment_W_m2": num(row, "EquipmentValue"),
        "outdoor_air_L_s_person": num(row, "MinFreshAir"), "outdoor_air_L_s_m2": num(row, "MinFreshAirPerArea"),
        "illuminance_lux": num(row, "TargetIlluminance1"),
        "heating_setpoint_C": num(row, "HeatingDesignSetPointTemperature"),
        "cooling_setpoint_C": num(row, "ActiveCoolingDesignSetPointTemperature"),
        "occupancy_schedule": name_of("Schedules", row.get("OccupancySchedule", "")),
        "occupancy_hours": sch,
    }


def construction_u(cid):
    """從材料層算 U 值: U = 1 / (SRO + Σ 厚度/導熱係數 + SRI), 只取前 NumberLayers 層。
    範本庫裡存的 U-Value 欄位有些沒更新(例如 ASHRAE 2013 / 2019 的屋頂), 所以不用它。
    回傳 (U, 層清單); 地坪這類以 F 係數表示、沒有表面熱阻的構造回傳 (None, 層清單)。"""
    return construction_u_row(table("Constructions").get(str(cid)))


def construction_u_row(c):
    """同 construction_u, 但直接給構造的欄位(例如從模型裡讀出來、範本庫沒有的構造)。"""
    if not c:
        return None, []
    mats = table("Materials")
    n = int(num(c, "NumberLayers") or 0)
    r, layers = 0.0, []
    for i in range(1, n + 1):
        m = mats.get(c.get(f"Mat {i}", ""), {})
        th, k = num(c, f"Thick {i}"), num(m, "Conductivity (W/mK)")
        if not th:
            continue
        ri = th / k if k else (num(m, "R-Value") or 0.0)
        r += ri
        layers.append({"index": i, "material": m.get("Name", ""), "thickness_m": th, "conductivity": k, "R": ri})
    sro, sri = num(c, "SRO"), num(c, "SRI")
    if sro is None or sri is None or not layers:
        return None, layers
    return 1 / (sro + r + sri), layers


# ---- 圍護結構: ASHRAE 90.1-2019 基準(Appendix G)----

BUILDING_TYPES = {"非住宅": ("Nonresidential", "Non-Res"), "住宅": ("Residential", "Residential"),
                  "半空調": ("Semiheated", "Semiheated")}  # 構造範本名稱, 玻璃名稱裡的寫法
# 主要外殼部位 → DB 屬性。實測: DB 產生 IDF 時屋頂 / 地坪 / 外挑樓板用 Combined* 欄位, 不用 FlatRoofConstr 等
ENVELOPE_PARTS = (("外牆", ("WallConstr",)), ("屋頂", ("CombinedFlatRoofConstr", "PitchedRoofConstr")),
                  ("外挑樓板", ("CombinedExternalFloorConstr",)), ("地坪", ("CombinedGroundFloorConstr",)),
                  ("外門", ("ExternalDoorConstr",)))
TARGET_U_PARTS = ("外牆", "屋頂", "外挑樓板")  # 有保溫層可調的部位; 地坪用 F 係數、外門沒有保溫層
GLAZING_BRACKET = "30% to 40% of Wall"  # 2019 基準玻璃按窗牆比分檔, 10% 以上各檔數值相同; 只有 0–10% 在 CZ5–6 不同


def parse_climate_zone(cz):
    """"4A" / "CZ4" / "3C" → ("4", "A")。"""
    s = str(cz).upper().replace("CZ", "").strip()
    if not s or not s[0].isdigit() or s[0] not in "012345678":
        raise ValueError(f"看不懂氣候區「{cz}」; 請給 ASHRAE 氣候區, 例如 4A、5B、3C")
    return s[0], s[1:2]


def baseline_envelope(climate_zone="4A", building_type="非住宅"):
    """ASHRAE 90.1-2019 基準: (構造範本 row, 外窗玻璃 row)。都直接取自 DB 範本庫。"""
    if building_type not in BUILDING_TYPES:
        raise KeyError(f"不認得的建築類型「{building_type}」; 可用的有: {'、'.join(BUILDING_TYPES)}")
    n, letter = parse_climate_zone(climate_zone)
    tname, gname = BUILDING_TYPES[building_type]
    want = f"CZ{n} {tname} Baseline Constructions [2019]"
    tmpl = next((r for r in table("ConstructionTemplates").values() if r["Name"] == want), None)
    if tmpl is None:
        raise KeyError(f"本機 DB 範本庫裡找不到「{want}」")
    glazing = []
    for g in table("Glazing").values():
        name = g["Name"]
        if f", {gname}, Vertical Glazing, {GLAZING_BRACKET}" not in name or not name.endswith("[2019]"):
            continue
        prefix = name.split(f", {gname}, ")[0]  # "CZ4", 或 CZ3 分組的 "CZ3 (A, B)" / "CZ3 (C)"
        if prefix == f"CZ{n}" or (prefix.startswith(f"CZ{n} (") and (not letter or letter in prefix[4:])):
            glazing.append(g)
    if not glazing:
        raise KeyError(f"本機 DB 範本庫裡找不到 CZ{n}{letter} {gname} 的 2019 基準外窗")
    return tmpl, min(glazing, key=lambda g: int(g["Id"]))


def template_construction_attributes(row):
    """套用構造範本時要寫入的屬性: 範本裡所有 *Constr 欄位與滲風。DB 介面換範本會載入這些, API 只改編號不會。"""
    out = [(k, v) for k, v in row.items() if k.endswith("Constr") and v != ""]
    return out + [(k, row[k]) for k in ("InfiltrationOn", "InfiltrationValue") if row.get(k, "") != ""]


# ---- 簡易空調(DB Simple HVAC): 範本的季節效率與燃料 ----
# EnergyPlus 用理想負載算出冷熱需求, DB 自己的結果畫面再用範本的季節效率換算成燃料; 這裡照同樣的算法。
# 選項只收 DB 範本庫裡的範本; 效率、燃料、新風都是範本的值, 不是我們定的。
HVAC_SYSTEMS = {  # 表單選項 → DB 空調範本名稱
    "風機盤管＋冷機＋燃氣鍋爐": "Fan Coil Unit (4-Pipe), Air cooled Chiller",
    "VAV 中央空調＋冷機＋燃氣鍋爐": "VAV, Air-cooled Chiller, Reheat",
    "箱型機 Packaged DX(全電)": "Packaged DX",
    "分離式冷氣＋新風(全電)": "Split + Separate Mechanical Ventilation",
    "VRF 多聯機＋新風(全電)": "VRF (Air-Cooled), Heat Recovery, DOAS",
    "PTHP 壁掛熱泵(全電,無新風)": "PTHP",
    "暖氣片＋燃氣鍋爐(無冷氣,自然通風)": "Radiator heating, Boiler HW, Nat Vent",
}
# 範本欄位裡模型讀不到(實測讀回 UNKNOWN)或只是範本說明的, 不寫
_HVAC_SKIP = {"Id", "Name", "CategoryId", "Default", "Colour", "Locked", "Scratch", "Source", "RegionId", "NCMID",
              "NCMORDER", "NCMCOD", "NCMCATEGORY", "NCMCATEGORY1", "ParamSelected", "IsSystem", "HVACType", "DSMHVACId",
              "FansOn", "FansSupplyOn", "FansSupplyPower", "FansExtractOn", "FansExtractPower", "FanMotorEfficiency",
              "NaturalVentilationUserDefined", "AirDistributionExhaustHeight"}
# 換算燃料要讀的模型屬性(建築層級)
HVAC_READ = ("HVACDataLevel", "HVACTemplateId", "HeatingOn", "ActiveCoolingOn", "MechanicalVentilationOn", "NaturalVentilationOn",
             "BoilerFuel", "SCoPFrac", "SSEERFrac", "DHWTemplateId", "DHWCoP", "DHWFuelType")


def hvac_template(label):
    """表單選項或範本名稱 → 範本 row。"""
    name = HVAC_SYSTEMS.get(label, label)
    rows = [r for r in table("HVACTemplates").values() if r["Name"] == name]
    if not rows:
        raise KeyError(f"空調系統只能選:{'、'.join(HVAC_SYSTEMS)}")
    return min(rows, key=lambda r: int(r["Id"]))


def hvac_attributes(row):
    """套用空調範本要寫入的建築屬性(API 只改編號不帶數值, 所以逐項寫)。"""
    return [(k, v) for k, v in row.items() if k not in _HVAC_SKIP and v != ""]


def fuel(code, kind="hvac"):
    """DB 燃料欄位 → 「電」/「天然氣」/ 原文。只有編號時: 空調 BoilerFuel 1 = 電、2 = 天然氣;
    熱水 DHWFuelType 1 = 天然氣(兩張表的編號不同, 有文字時以文字為準)。"""
    s = str(code or "").strip()
    text = s.split("-", 1)[1].strip().lower() if "-" in s else ""
    if "electric" in text:
        return "電"
    if "gas" in text:
        return "天然氣"
    if text:
        return text
    return ({"1": "電", "2": "天然氣"} if kind == "hvac" else {"1": "天然氣"}).get(s, s or "未知")


def adjust_glazing(g, target_u=None, shgc=None):
    """基準玻璃(簡易定義: U 值 + 日射透過率 = SHGC)→ 新玻璃要改的欄位與說明。可見光透過率沿用基準。"""
    if not g.get("DefinitionMethod", "").startswith("2"):
        raise ValueError(f"基準玻璃「{g['Name']}」不是簡易定義(U 值 + SHGC),不能直接改")
    changes, said = {}, []
    if target_u is not None:
        if not 0.3 <= target_u <= 6.5:
            raise ValueError(f"U 值要在 0.3–6.5 W/m²K 之間,收到 {target_u:g}")
        changes["U-Value"] = f"{target_u:g}"
        said.append(f"U {target_u:g}")
    if shgc is not None:
        if not 0.05 <= shgc <= 0.9:
            raise ValueError(f"SHGC 要在 0.05–0.9 之間,收到 {shgc:g}")
        changes["TransSolar"] = f"{shgc:g}"
        said.append(f"SHGC {shgc:g}")
    note = "、".join(said)
    changes["Name"] = f"{g['Name']} -> {', '.join(said)} (DbMcp)"  # 橋接的工作檔是 ASCII
    return changes, note


def adjust_to_u(c, target_u):
    """把構造(欄位 dict)裡熱阻最大的那層(保溫層)改厚或改薄, 讓 U 等於 target_u。
    回傳 (要改的欄位, 說明)。目標做不到(拿掉保溫也不夠)就拋 ValueError, 不硬湊。"""
    u0, layers = construction_u_row(c)
    if u0 is None:
        raise ValueError(f"「{c.get('Name', '')}」算不出 U 值, 不能用目標 U 調整")
    ins = max((l for l in layers if l["conductivity"]), key=lambda l: l["R"], default=None)
    if ins is None:
        raise ValueError(f"「{c.get('Name', '')}」沒有可以調厚度的材料層")
    r_rest = 1 / u0 - ins["R"]
    need = 1 / target_u - r_rest
    if need <= 0.001:
        raise ValueError(f"目標 U {target_u} 太高: 拿掉「{ins['material']}」U 也只有 {1 / r_rest:.3f}")
    th = need * ins["conductivity"]
    changes = {f"Thick {ins['index']}": f"{th:.6f}", "U-Value": f"{target_u:.6f}",
               "Name": f"{c.get('Name', '')} -> U {target_u:g} (DbMcp)"}
    note = (f"{ins['material']} {ins['thickness_m'] * 1000:.0f} → {th * 1000:.0f} mm"
            f"(U {u0:.3f} → {target_u:g})")
    return changes, note


# ---- 地點與氣象檔 ----

WEATHER_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "DesignBuilder" / "Weather Data"
# 套地點範本時不寫的欄位(名稱、分類、來源等範本本身的資料)
LOCATION_SKIP = ("Id", "LocationName", "CategoryId", "Default", "Source", "ParamSelected")


def find_locations(keyword, limit=15):
    """地點名稱包含所有關鍵字(不分大小寫)的地點範本; 名稱像 "NY - NEW YORK/JOHN F. KE"、"SUNGSHAN/TAIPEI"。"""
    words = keyword.lower().split()
    rows = [r for r in table("LocationTemplates").values() if all(w in r["LocationName"].lower() for w in words)]
    return sorted(rows, key=lambda r: r["LocationName"])[:limit], len(rows)


def resolve_location(ref):
    """地點範本編號或完整名稱 → row; 找不到或名稱重複時拋錯, 不自己挑。"""
    lt = table("LocationTemplates")
    ref = str(ref).strip()
    if ref.isdigit():
        if ref not in lt:
            raise KeyError(f"DB 庫裡沒有編號 {ref} 的地點")
        return lt[ref]
    hits = [r for r in lt.values() if r["LocationName"].lower() == ref.lower()]
    if len(hits) != 1:
        raise KeyError(f"{'找不到' if not hits else '有多個'}叫「{ref}」的地點; 先用 db_find_locations 找, 再用編號指定")
    return hits[0]


def weather_of(loc):
    """地點用的逐時氣象檔: (HourlyWeather row, 本機檔案路徑或 None)。DB 庫裡的大多要另外下載。"""
    w = table("HourlyWeather").get(loc.get("HourlyWeatherData", ""), {})
    name = w.get("Filename", "").lower()
    local = next((p for p in WEATHER_DIR.glob("*.epw") if p.name.lower() == name), None) if name else None
    return w, local


def location_attributes(loc):
    """套地點範本時寫入 Site 的屬性(欄位名稱就是 Site 屬性名稱)。API 只改 LocationTemplateId 不會帶入這些。"""
    return [("LocationTemplateId", loc["Id"])] + [(k, v) for k, v in loc.items() if v != "" and k not in LOCATION_SKIP]


def epw_location(path):
    """EPW 第一行: LOCATION,城市,州,國家,來源,WMO,緯度,經度,時區,海拔。"""
    head = Path(path).open(encoding="latin-1").readline().split(",")
    return {"city": head[1], "wmo": head[5], "lat": float(head[6]), "lon": float(head[7])}


# ---- 作息: 只從 DB 庫裡選, 不產生新的 ----

SCHEDULE_KINDS = {"人員": ("OccupancySchedule",), "照明": ("FluorescentLightingSchedule",),
                  "設備": ("ComputersSchedule", "EquipmentSchedule", "MiscellaneousSchedule",
                         "CateringSchedule", "ProcessSchedule"),
                  "熱水": ("DHWSchedule",)}


def find_schedules(keyword, limit=20):
    """名稱包含所有關鍵字(空白分隔, 不分大小寫)的作息, 依編號排序。"""
    words = keyword.lower().split()
    rows = [s for s in table("Schedules").values() if all(w in s["Name"].lower() for w in words)]
    return sorted(rows, key=lambda s: int(s["Id"]))[:limit], len(rows)


def resolve_schedule(ref):
    """作息編號或完整名稱 → row。名稱重複(DB 庫裡約 960 個名稱有多份)時拋錯並列出編號, 不自己挑。"""
    s = table("Schedules")
    ref = str(ref).strip()
    if ref.isdigit():
        if ref not in s:
            raise KeyError(f"DB 庫裡沒有編號 {ref} 的作息")
        return s[ref]
    hits = [r for r in s.values() if r["Name"] == ref]
    if not hits:
        raise KeyError(f"DB 庫裡沒有叫「{ref}」的作息; 先用 db_find_schedules 找")
    if len(hits) > 1:
        raise KeyError(f"「{ref}」有 {len(hits)} 份(編號 {', '.join(h['Id'] for h in hits)}), 請用編號指定")
    return hits[0]


# 範本裡 ASHRAE 的屋頂 / 地坪構造放在 Combined* 欄位; FlatRoofConstr 等引用的是 DB 的通用構造, 兩者並列讓差異看得到
CONSTRUCTION_PARTS = (("外牆", "WallConstr"), ("屋頂(ASHRAE)", "CombinedFlatRoofConstr"), ("平屋頂(通用)", "FlatRoofConstr"),
                      ("外挑樓板", "CombinedExternalFloorConstr"), ("地坪", "GroundSlabConstr"), ("外門", "ExternalDoorConstr"))


def construction_template_summary(row):
    cons = table("Constructions")
    parts = {}
    for label, key in CONSTRUCTION_PARTS:
        c = cons.get(row.get(key, ""), {})
        u, layers = construction_u(row.get(key, ""))
        stored = num(c, "U-Value")
        parts[label] = {"construction": c.get("Name", ""), "U_W_m2K": u, "U_stored_in_library": stored,
                        "stored_U_mismatch": bool(u and stored and abs(u - stored) > 0.02 * u), "layers": layers}
    return {"id": int(row["Id"]), "name": row["Name"], "source": row.get("Source", ""),
            "infiltration_ach": num(row, "InfiltrationValue"), "parts": parts}


def us_construction_templates():
    """ASHRAE 90.1 基準構造: 名稱是 "CZn Nonresidential / Residential / Semiheated Baseline Constructions"。"""
    rows = [r for r in table("ConstructionTemplates").values()
            if r["Name"].startswith("CZ") and "Baseline Constructions" in r["Name"]]
    return sorted(rows, key=lambda r: (r["Name"].split()[0], r["Name"]))


def catalog():
    """給 MCP 與報告用的對照資料: 只含選得到的項目與它們的數值, 數值全部取自本機 DB 範本庫。"""
    spaces = {}
    for label in SPACE_TYPES:
        try:
            spaces[label] = activity_summary(resolve_space(label))
        except KeyError as e:
            spaces[label] = {"error": str(e)}
    return {
        "db_data_folder": str(DATA),
        "spaces": spaces,
        "construction_templates": [construction_template_summary(r) for r in us_construction_templates()],
        "hvac_templates": [{"id": int(r["Id"]), "name": r["Name"]} for r in table("HVACTemplates").values()
                           if r["Name"] and r["Name"] != "<None>"],
    }


def _f(v, d=1):
    return "—" if v is None else f"{v:.{d}f}"


def catalog_markdown(cat):
    lines = ["# DesignBuilder 範本對照表(美國預設)", "",
             f"資料來源:本機 DesignBuilder 範本庫 `{cat['db_data_folder']}`。所有數值直接取自範本庫,沒有另外編寫。", "",
             "## 1. 空間用途 → 活動範本(Activity)", "",
             "作息一欄是平日 0–24 時的在室率,每格一小時,越高代表在室人數越多。", "",
             "| 用途 | DB 範本 | 人員 人/m² | 設備 W/m² | 新風 L/s·人 + L/s·m² | 照度 lux | 暖 / 冷設定 °C | 平日在室作息 0→24 時 |",
             "|---|---|---:|---:|---:|---:|---:|---|"]
    for label, s in cat["spaces"].items():
        if "error" in s:
            lines.append(f"| {label} | {s['error']} | | | | | | |")
            continue
        sch = s["occupancy_hours"]
        lines.append(f"| {label} | {s['name']}(#{s['id']},{s['source'] or '來源未標'}) | {_f(s['people_per_m2'], 3)} | "
                     f"{_f(s['equipment_W_m2'])} | {_f(s['outdoor_air_L_s_person'])} + {_f(s['outdoor_air_L_s_m2'], 2)} | "
                     f"{_f(s['illuminance_lux'], 0)} | {_f(s['heating_setpoint_C'], 0)} / {_f(s['cooling_setpoint_C'], 0)} | "
                     f"`{spark(sch['平日']) if sch else '讀不出'}` {s['occupancy_schedule']} |")
    lines += ["", "## 2. ASHRAE 90.1 基準構造範本(按氣候區)", "",
              "U 值單位 W/m²K,由構造的材料層計算(U = 1 / (外表面熱阻 + Σ 厚度/導熱係數 + 內表面熱阻),各值取自 DB 構造庫)。",
              "⚠ 表示 DB 範本庫自己存的 U 值和材料層算出的不一致(範本庫的欄位沒有更新),以計算值為準。",
              "地坪以 F 係數表示,沒有表面熱阻,不算 U 值(顯示 —)。部分範本只有外牆是 ASHRAE 構造,屋頂等引用 DB 的通用構造,構造名稱見 catalog.json。", "",
              "| 範本 | 來源 | " + " | ".join(p for p, _ in CONSTRUCTION_PARTS) + " | 滲風 ach |",
              "|---|---|" + "---:|" * len(CONSTRUCTION_PARTS) + "---:|"]
    for t in cat["construction_templates"]:
        cells = []
        for p, _ in CONSTRUCTION_PARTS:
            part = t["parts"][p]
            cell = _f(part["U_W_m2K"], 3)
            if part["stored_U_mismatch"]:
                cell += f" ⚠ 庫存 {part['U_stored_in_library']:.3f}"
            cells.append(cell)
        lines.append(f"| {t['name']}(#{t['id']}) | {t['source'] or '未標'} | " + " | ".join(cells)
                     + f" | {_f(t['infiltration_ach'], 2)} |")
    lines += ["", "各構件用的構造名稱見 catalog.json。", "", "## 3. 空調範本(HVAC)", ""]
    lines += [f"- #{h['id']} {h['name']}" for h in cat["hvac_templates"]]
    return "\n".join(lines) + "\n"


def main():
    import json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not DATA.exists():
        raise SystemExit(f"找不到 DesignBuilder 範本庫 {DATA};請確認已安裝 DB 並至少打開過一次")
    out = Path(__file__).resolve().parents[2] / "db_models" / "dblibrary"  # 範本庫是 DB 的資料, 只產生在本機, 不放進版本控制
    out.mkdir(parents=True, exist_ok=True)
    cat = catalog()
    (out / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "範本對照表.md").write_text(catalog_markdown(cat), encoding="utf-8")
    bad = [k for k, v in cat["spaces"].items() if "error" in v]
    print(f"已寫入 {out}:用途 {len(cat['spaces'])} 種(找不到 {len(bad)} 種)、"
          f"基準構造 {len(cat['construction_templates'])} 個、空調範本 {len(cat['hvac_templates'])} 個")


if __name__ == "__main__":
    main()
