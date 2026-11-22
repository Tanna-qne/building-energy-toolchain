"""兩個改造措施的 IDF 修改。與 handover/patches/energyplus-mcp.patch 加進 EnergyPlus-MCP 的 utils/retrofit.py 相同,
放一份在這裡讓不裝 MCP server 的腳本(長興 ecm.py、CI)也能用: 加強外牆 / 屋頂隔熱、提高空調設備效率。

做法參考 NREL BCL 的 "Increase R-value of Insulation" 與 "Set COP for DX Cooling Units" 措施:
- 隔熱: 外牆 / 屋頂的構造算出 U 值, 比目標差就加一層無質量保溫補到目標;
  複製一份新構造只給外部表面用, 共用同構造的室內隔間不受影響。
- 空調: 冰水主機 / DX 冷氣 COP 乘上倍數, 鍋爐與瓦斯加熱器效率、風扇總效率拉到下限。
"""

# 表面熱阻(室內 + 室外膜), ASHRAE 冬季條件; 用來從材料層算 U 值
FILM_R = {"wall": 0.13 + 0.04, "roof": 0.10 + 0.04}


def _layers(construction):
    """Construction 的材料層名稱(Outside_Layer, Layer_2 …)。"""
    names = [construction.Outside_Layer]
    for i in range(2, 11):
        v = getattr(construction, f"Layer_{i}", "")
        if v:
            names.append(v)
    return [n for n in names if n]


def _material_r(idf, name):
    """一層材料的熱阻 m²K/W;讀不到(例如玻璃類)回傳 None。"""
    for obj in idf.idfobjects["MATERIAL"]:
        if obj.Name.upper() == name.upper():
            return float(obj.Thickness) / float(obj.Conductivity)
    for kind in ("MATERIAL:NOMASS", "MATERIAL:AIRGAP"):
        for obj in idf.idfobjects[kind]:
            if obj.Name.upper() == name.upper():
                return float(obj.Thermal_Resistance)
    return None


def upgrade_insulation(idf, wall_u=0.25, roof_u=0.18):
    """外牆 U 比 wall_u 差、屋頂比 roof_u 差的構造, 加一層保溫補到目標。回傳修改清單。"""
    targets = {"wall": wall_u, "roof": roof_u}
    surfaces = [s for s in idf.idfobjects["BUILDINGSURFACE:DETAILED"]
                if s.Outside_Boundary_Condition.lower() == "outdoors" and s.Surface_Type.lower() in targets]
    constructions = {c.Name.upper(): c for c in idf.idfobjects["CONSTRUCTION"]}
    changes, new_names = [], {}
    for s in surfaces:
        kind = s.Surface_Type.lower()
        key = (s.Construction_Name.upper(), kind)
        if key not in new_names:
            new_names[key] = None
            c = constructions.get(key[0])
            if c is None:
                continue
            rs = [_material_r(idf, n) for n in _layers(c)]
            if any(r is None for r in rs):
                continue  # 有讀不到熱阻的層(非一般材料), 不動
            r_total = sum(rs) + FILM_R[kind]
            u_old = 1 / r_total
            if u_old <= targets[kind]:
                continue
            add_r = 1 / targets[kind] - r_total
            mat = idf.newidfobject("MATERIAL:NOMASS", Name=f"Added insulation {kind} {c.Name}"[:100],
                                   Roughness="MediumRough", Thermal_Resistance=round(add_r, 3),
                                   Thermal_Absorptance=0.9, Solar_Absorptance=0.7, Visible_Absorptance=0.7)
            layers = _layers(c)
            # 放在最外層之後(外保溫的位置), 外層材料的日射吸收率等表面性質不變
            new_layers = layers[:1] + [mat.Name] + layers[1:]
            if len(new_layers) > 10:
                continue
            nc = idf.newidfobject("CONSTRUCTION", Name=f"{c.Name} insulated"[:100], Outside_Layer=new_layers[0])
            for i, n in enumerate(new_layers[1:], start=2):
                setattr(nc, f"Layer_{i}", n)
            new_names[key] = nc.Name
            changes.append({"construction": c.Name, "type": kind, "u_old": round(u_old, 3),
                            "u_new": targets[kind], "added_r": round(add_r, 3)})
        if new_names[key]:
            s.Construction_Name = new_names[key]
    return changes


# (物件類別, 欄位, 方式) 方式: ("mul", 倍數) 或 ("min", 下限)
HVAC_FIELDS = [
    ("CHILLER:ELECTRIC:EIR", "Reference_COP", "cop"),
    ("CHILLER:ELECTRIC:REFORMULATEDEIR", "Reference_COP", "cop"),
    ("CHILLER:ELECTRIC", "Nominal_COP", "cop"),
    ("CHILLER:CONSTANTCOP", "Nominal_COP", "cop"),
    ("COILPERFORMANCE:DX:COOLING", "Gross_Rated_Cooling_COP", "cop"),
    ("COIL:COOLING:DX:SINGLESPEED", "Gross_Rated_Cooling_COP", "cop"),
    ("COIL:COOLING:DX:TWOSPEED", "High_Speed_Gross_Rated_Cooling_COP", "cop"),
    ("COIL:COOLING:DX:TWOSPEED", "Low_Speed_Gross_Rated_Cooling_COP", "cop"),
    ("HVACTEMPLATE:PLANT:CHILLER", "Nominal_COP", "cop"),
    ("BOILER:HOTWATER", "Nominal_Thermal_Efficiency", "heat"),
    ("COIL:HEATING:FUEL", "Burner_Efficiency", "heat"),
    ("HVACTEMPLATE:PLANT:BOILER", "Efficiency", "heat"),
    ("FAN:VARIABLEVOLUME", "Fan_Total_Efficiency", "fan"),
    ("FAN:CONSTANTVOLUME", "Fan_Total_Efficiency", "fan"),
    ("FAN:ONOFF", "Fan_Total_Efficiency", "fan"),
]


def upgrade_hvac_efficiency(idf, cop_factor=1.25, heater_eff=0.95, fan_eff=0.7):
    """冷氣 / 冰水主機 COP ×cop_factor, 鍋爐與瓦斯加熱器效率、風扇效率拉到下限。回傳修改清單。"""
    changes = []
    for kind, field, how in HVAC_FIELDS:
        if kind not in idf.idfobjects:
            continue
        for obj in idf.idfobjects[kind]:
            try:
                old = float(getattr(obj, field))
            except (ValueError, TypeError, AttributeError):
                continue  # 空白或 autosize
            new = round(old * cop_factor, 3) if how == "cop" else max(old, heater_eff if how == "heat" else fan_eff)
            if new != old:
                setattr(obj, field, new)
                changes.append({"object": kind, "name": obj.Name, "field": field, "old": old, "new": new})
    return changes
