"""把定版模型的單速 PTHP 盤管換成變頻(變速)盤管, 寫出 cx_vs_uncalibrated.idf(尚未重新校準)。

    uv run --with eppy python projects/changxing/hvac_vs/convert.py

原模型: Coil:Cooling/Heating:DX:SingleSpeed, 美國 DOE 原型的單速 PTHP 曲線,
部分負荷靠開停機(PLF = 0.85 + 0.15·PLR)。中國服務區常用的分體空調多為變頻機, 低負荷時降頻運行而不是開停。

做法:
- 盤管換成 Coil:Cooling:DX:VariableSpeed(4 速)與 Coil:Heating:DX:VariableSpeed(5 速, 額定點為第 3 速)。
- 各速的容量比、COP 比、風量比與各速的溫度修正曲線, 取自 EnergyPlus 26.2 自帶範例
  ChangeoverBypassVAV_AirToAirHeatPump.idf 的變頻熱泵資料(EXAMPLE 常數)。
- 每區的額定容量、額定風量與原模型相同; 額定點 COP 縮放回原設定(製冷 3.5、製熱 3.3),
  所以「額定能效」不變, 改變的只有部分負荷與不同室外溫度下的表現。
- 製熱的除霜(ReverseCycle / OnDemand)、壓縮機最低運行溫度 −8 °C、電輔熱沿用原模型。
- 最低速以下仍需開停, 沿用原模型的 PLF 曲線。
"""
import sys
from pathlib import Path

from eppy.modeleditor import IDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parents[1]))
from eplus_paths import energyplus_dir  # noqa: E402

SRC = HERE / "cx_singlespeed.idf"  # 換盤管前的定版(單速 PTHP), 保留作對照
OUT = HERE / "cx_vs_uncalibrated.idf"
EXAMPLE = "ChangeoverBypassVAV_AirToAirHeatPump.idf"
COOL_NOMINAL, HEAT_NOMINAL = 4, 3  # 範例的額定速度


def example_coil(ex, kind):
    o = ex.idfobjects[kind][0]
    n = int(float(o.Number_of_Speeds))
    sp = []
    for s in range(1, n + 1):
        g = lambda f: o[f"Speed_{s}_{f}"]  # noqa: E731
        if kind.endswith("COOLING:DX:VARIABLESPEED"):
            sp.append({"cap": float(g("Reference_Unit_Gross_Rated_Total_Cooling_Capacity")),
                       "shr": float(g("Reference_Unit_Gross_Rated_Sensible_Heat_Ratio")),
                       "cop": float(g("Reference_Unit_Gross_Rated_Cooling_COP")),
                       "flow": float(g("Reference_Unit_Rated_Air_Flow_Rate")),
                       "cond_flow": float(g("Reference_Unit_Rated_Condenser_Air_Flow_Rate")),
                       "capft": g("Total_Cooling_Capacity_Function_of_Temperature_Curve_Name"),
                       "eirft": g("Energy_Input_Ratio_Function_of_Temperature_Curve_Name")})
        else:
            sp.append({"cap": float(g("Reference_Unit_Gross_Rated_Heating_Capacity")),
                       "cop": float(g("Reference_Unit_Gross_Rated_Heating_COP")),
                       "flow": float(g("Reference_Unit_Rated_Air_Flow_Rate")),
                       "capft": g("Heating_Capacity_Function_of_Temperature_Curve_Name"),
                       "eirft": g("Energy_Input_Ratio_Function_of_Temperature_Curve_Name")})
    return sp, o.Speed_1_Energy_Input_Ratio_Function_of_Air_Flow_Fraction_Curve_Name  # 範例各速共用 HPACFFF


def copy_curve(ex, idf, name):
    if any(o.Name.upper() == name.upper() for k in ("CURVE:BIQUADRATIC", "CURVE:CUBIC", "CURVE:QUADRATIC")
           for o in idf.idfobjects[k]):
        return
    for k in ("CURVE:BIQUADRATIC", "CURVE:CUBIC", "CURVE:QUADRATIC"):
        for o in ex.idfobjects[k]:
            if o.Name.upper() == name.upper():
                idf.copyidfobject(o)
                return
    raise KeyError(name)


def convert(idf, ex):
    cool, fff = example_coil(ex, "COIL:COOLING:DX:VARIABLESPEED")
    heat, _ = example_coil(ex, "COIL:HEATING:DX:VARIABLESPEED")
    for s in cool + heat:
        copy_curve(ex, idf, s["capft"])
        copy_curve(ex, idf, s["eirft"])
    copy_curve(ex, idf, fff)
    kc = 3.5 / cool[COOL_NOMINAL - 1]["cop"]
    kh = 3.3 / heat[HEAT_NOMINAL - 1]["cop"]
    notes = []
    for u in idf.idfobjects["ZONEHVAC:PACKAGEDTERMINALHEATPUMP"]:
        old_c = idf.getobject("COIL:COOLING:DX:SINGLESPEED", u.Cooling_Coil_Name)
        old_h = idf.getobject("COIL:HEATING:DX:SINGLESPEED", u.Heating_Coil_Name)
        c = idf.newidfobject("COIL:COOLING:DX:VARIABLESPEED", Name=old_c.Name + " VS")
        c.Indoor_Air_Inlet_Node_Name, c.Indoor_Air_Outlet_Node_Name = old_c.Air_Inlet_Node_Name, old_c.Air_Outlet_Node_Name
        c.Number_of_Speeds, c.Nominal_Speed_Level = len(cool), COOL_NOMINAL
        c.Gross_Rated_Total_Cooling_Capacity_At_Selected_Nominal_Speed_Level = old_c.Gross_Rated_Total_Cooling_Capacity
        c.Rated_Air_Flow_Rate_At_Selected_Nominal_Speed_Level = old_c.Rated_Air_Flow_Rate
        c.Nominal_Time_for_Condensate_to_Begin_Leaving_the_Coil = 0
        c.Initial_Moisture_Evaporation_Rate_Divided_by_SteadyState_AC_Latent_Capacity = 0
        c.Energy_Part_Load_Fraction_Curve_Name = old_c.Part_Load_Fraction_Correlation_Curve_Name
        c.Condenser_Type = "AirCooled"
        c.Crankcase_Heater_Capacity = 0
        c.Maximum_Outdoor_DryBulb_Temperature_for_Crankcase_Heater_Operation = 10
        c.Minimum_Outdoor_DryBulb_Temperature_for_Compressor_Operation = old_c.Minimum_Outdoor_DryBulb_Temperature_for_Compressor_Operation
        for i, s in enumerate(cool, 1):
            c[f"Speed_{i}_Reference_Unit_Gross_Rated_Total_Cooling_Capacity"] = s["cap"]
            c[f"Speed_{i}_Reference_Unit_Gross_Rated_Sensible_Heat_Ratio"] = s["shr"]
            c[f"Speed_{i}_Reference_Unit_Gross_Rated_Cooling_COP"] = round(s["cop"] * kc, 4)
            c[f"Speed_{i}_Reference_Unit_Rated_Air_Flow_Rate"] = s["flow"]
            c[f"Speed_{i}_Reference_Unit_Rated_Condenser_Air_Flow_Rate"] = s["cond_flow"]
            c[f"Speed_{i}_Total_Cooling_Capacity_Function_of_Temperature_Curve_Name"] = s["capft"]
            c[f"Speed_{i}_Total_Cooling_Capacity_Function_of_Air_Flow_Fraction_Curve_Name"] = fff
            c[f"Speed_{i}_Energy_Input_Ratio_Function_of_Temperature_Curve_Name"] = s["eirft"]
            c[f"Speed_{i}_Energy_Input_Ratio_Function_of_Air_Flow_Fraction_Curve_Name"] = fff

        h = idf.newidfobject("COIL:HEATING:DX:VARIABLESPEED", Name=old_h.Name + " VS")
        h.Indoor_Air_Inlet_Node_Name, h.Indoor_Air_Outlet_Node_Name = old_h.Air_Inlet_Node_Name, old_h.Air_Outlet_Node_Name
        h.Number_of_Speeds, h.Nominal_Speed_Level = len(heat), HEAT_NOMINAL
        h.Rated_Heating_Capacity_At_Selected_Nominal_Speed_Level = old_h.Gross_Rated_Heating_Capacity
        h.Rated_Air_Flow_Rate_At_Selected_Nominal_Speed_Level = old_h.Rated_Air_Flow_Rate
        h.Energy_Part_Load_Fraction_Curve_Name = old_h.Part_Load_Fraction_Correlation_Curve_Name
        for f in ("Defrost_Energy_Input_Ratio_Function_of_Temperature_Curve_Name",
                  "Minimum_Outdoor_DryBulb_Temperature_for_Compressor_Operation",
                  "Maximum_Outdoor_DryBulb_Temperature_for_Defrost_Operation", "Crankcase_Heater_Capacity",
                  "Maximum_Outdoor_DryBulb_Temperature_for_Crankcase_Heater_Operation", "Defrost_Strategy",
                  "Defrost_Control", "Defrost_Time_Period_Fraction", "Resistive_Defrost_Heater_Capacity"):
            h[f] = old_h[f]
        for i, s in enumerate(heat, 1):
            h[f"Speed_{i}_Reference_Unit_Gross_Rated_Heating_Capacity"] = s["cap"]
            h[f"Speed_{i}_Reference_Unit_Gross_Rated_Heating_COP"] = round(s["cop"] * kh, 4)
            h[f"Speed_{i}_Reference_Unit_Rated_Air_Flow_Rate"] = s["flow"]
            h[f"Speed_{i}_Heating_Capacity_Function_of_Temperature_Curve_Name"] = s["capft"]
            h[f"Speed_{i}_Total_Heating_Capacity_Function_of_Air_Flow_Fraction_Curve_Name"] = fff
            h[f"Speed_{i}_Energy_Input_Ratio_Function_of_Temperature_Curve_Name"] = s["eirft"]
            h[f"Speed_{i}_Energy_Input_Ratio_Function_of_Air_Flow_Fraction_Curve_Name"] = fff

        u.Cooling_Coil_Object_Type, u.Cooling_Coil_Name = "Coil:Cooling:DX:VariableSpeed", c.Name
        u.Heating_Coil_Object_Type, u.Heating_Coil_Name = "Coil:Heating:DX:VariableSpeed", h.Name
        idf.removeidfobject(old_c)
        idf.removeidfobject(old_h)
        notes.append(u.Name)
    return notes, kc, kh, cool, heat


def main():
    IDF.setiddname(str(energyplus_dir() / "Energy+.idd"))
    idf = IDF(str(SRC))
    ex = IDF(str(energyplus_dir() / "ExampleFiles" / EXAMPLE))
    units, kc, kh, cool, heat = convert(idf, ex)
    idf.saveas(str(OUT))
    cn, hn = cool[COOL_NOMINAL - 1]["cap"], heat[HEAT_NOMINAL - 1]["cap"]
    print(f"{len(units)} 台 PTHP 換成變頻盤管 → {OUT.name}")
    print("製冷 容量比 / COP:", [(round(s["cap"] / cn, 3), round(s["cop"] * kc, 3)) for s in cool])
    print("製熱 容量比 / COP:", [(round(s["cap"] / hn, 3), round(s["cop"] * kh, 3)) for s in heat])


if __name__ == "__main__":
    main()
