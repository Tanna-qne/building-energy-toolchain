"""敏感度分析與留出驗證共用: 參數定義、IDF 修改、平行模擬、輸出指標。"""
import csv
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eppy.modeleditor import IDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE_IDF = ROOT / "cx_lowest_max.idf"
EPW = ROOT / "weather" / "CHN_Changxing_ERA5_31.00N_120.00E_2025.epw"
EP = Path(r"C:/Users/kiri/AppData/Local/Programs/EnergyPlusV26-2-0/EnergyPlus-26.2.0-4bd7a1f26f-Windows-x86_64")
WORKERS = 10

# (代號, 說明, 下限, 上限, 定版值); 乘數類定版值為 1, 偏移類為 0
PARAMS = [
    ("k_wall", "外牆保溫導熱係數 ×", 0.7, 1.3, 1.0),
    ("k_roof", "屋面保溫導熱係數 ×", 0.7, 1.3, 1.0),
    ("u_win", "外窗 U 值 W/m²K", 1.8, 2.8, 2.2),
    ("shgc", "外窗 SHGC", 0.30, 0.50, 0.40),
    ("infil", "滲風量 ×", 0.5, 2.0, 1.0),
    ("oa", "新風量 ×", 0.7, 1.3, 1.0),
    ("people", "人員密度 ×", 0.7, 1.3, 1.0),
    ("lpd", "照明功率密度 ×", 0.8, 1.2, 1.0),
    ("epd", "設備功率密度 ×", 0.8, 1.2, 1.0),
    ("kitchen", "廚房電器功率 ×", 0.8, 1.2, 1.0),
    ("cop_c", "製冷額定 COP", 2.8, 4.2, 3.5),
    ("cop_h", "製熱額定 COP", 2.6, 4.0, 3.3),
    ("t_cool", "製冷設定溫度偏移 °C", -1.0, 1.0, 0.0),
    ("t_heat", "製熱設定溫度偏移 °C", -1.0, 1.0, 0.0),
]
NAMES = [p[0] for p in PARAMS]
BASELINE = {p[0]: p[4] for p in PARAMS}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Model:
    """載入一次定版 IDF, 記下被修改欄位的原值; apply() 每次都從原值計算, 不會累積誤差。"""

    def __init__(self, path=BASE_IDF):
        IDF.setiddname(str(EP / "Energy+.idd"))
        self.idf = IDF(str(path))
        i = self.idf
        self.mat = {o.Name: o.Conductivity for o in i.idfobjects["MATERIAL"]}
        self.infil = [(o, o.Flow_Rate_per_Exterior_Surface_Area) for o in i.idfobjects["ZONEINFILTRATION:DESIGNFLOWRATE"]]
        self.vent = [(o, o.Flow_Rate_per_Person) for o in i.idfobjects["ZONEVENTILATION:DESIGNFLOWRATE"]]
        self.dsoa = [(o, o.Outdoor_Air_Flow_per_Person) for o in i.idfobjects["DESIGNSPECIFICATION:OUTDOORAIR"]]
        self.people = [(o, o.People_per_Floor_Area) for o in i.idfobjects["PEOPLE"]]
        self.lights = [(o, o.Watts_per_Floor_Area) for o in i.idfobjects["LIGHTS"]]
        self.equip = [(o, o.Watts_per_Floor_Area) for o in i.idfobjects["ELECTRICEQUIPMENT"]]
        self.kitchen = [(o, o.Design_Level) for o in i.idfobjects["OTHEREQUIPMENT"]
                        if o.EndUse_Subcategory == "KitchenAppliances"]
        self.setpt = {}
        for o in i.idfobjects["SCHEDULE:DAY:INTERVAL"]:
            for tag in ("cool", "heat"):
                if o.Name.endswith(f"_{tag}_Day Schedule"):
                    vals = [(k, _num(v)) for k, v in enumerate(o.obj) if k > 4 and k % 2 == 1]
                    self.setpt.setdefault(tag, []).append((o, vals))

    def apply(self, p):
        i = self.idf
        for o in i.idfobjects["MATERIAL"]:
            base = self.mat[o.Name]
            if o.Name.startswith("Fireproof insulation"):
                o.Conductivity = base * p["k_wall"]
            elif o.Name.startswith("PU "):
                o.Conductivity = base * p["k_roof"]
        for o in i.idfobjects["WINDOWMATERIAL:SIMPLEGLAZINGSYSTEM"]:
            o.UFactor, o.Solar_Heat_Gain_Coefficient = p["u_win"], p["shgc"]
        for o, v in self.infil:
            o.Flow_Rate_per_Exterior_Surface_Area = v * p["infil"]
        for o, v in self.vent:
            o.Flow_Rate_per_Person = v * p["oa"]
        for o, v in self.dsoa:
            o.Outdoor_Air_Flow_per_Person = v * p["oa"]
        for o, v in self.people:
            o.People_per_Floor_Area = v * p["people"]
        for o, v in self.lights:
            o.Watts_per_Floor_Area = v * p["lpd"]
        for o, v in self.equip:
            o.Watts_per_Floor_Area = v * p["epd"]
        for o, v in self.kitchen:
            o.Design_Level = v * p["kitchen"]
        for o in i.idfobjects["COIL:COOLING:DX:SINGLESPEED"]:
            o.Gross_Rated_Cooling_COP = p["cop_c"]
        for o in i.idfobjects["COIL:HEATING:DX:SINGLESPEED"]:
            o.Gross_Rated_Heating_COP = p["cop_h"]
        for tag, key in (("cool", "t_cool"), ("heat", "t_heat")):
            for o, vals in self.setpt.get(tag, []):
                for k, v in vals:
                    if v is not None and 15 <= v <= 30:  # 只移動運行設定值, 跳過停機時的 50 °C / 5 °C
                        o.obj[k] = round(v + p[key], 3)

    def save(self, p, path):
        self.apply(p)
        self.idf.saveas(str(path))


def simulate(idf_path, run_dir):
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    r = subprocess.run([str(EP / "energyplus.exe"), "-w", str(EPW), "-d", str(run_dir), "-r", str(idf_path)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=run_dir)
    if r.returncode:
        raise RuntimeError(f"{idf_path.name}: " + (run_dir / "eplusout.err").read_text(encoding="latin-1")[-1500:])
    with open(run_dir / "eplusmtr.csv", encoding="latin-1") as f:
        rows = list(csv.reader(f))
    head = rows[0]
    out = {}
    for n in ("Electricity:Facility", "Heating:Electricity", "Cooling:Electricity", "Fans:Electricity",
              "Ancillary:ExteriorEquipment:Electricity"):
        col = head.index(n + " [J](Hourly)")
        out[n] = [float(r[col]) / 3.6e6 for r in rows[1:]]
    shutil.rmtree(run_dir, ignore_errors=True)
    return out


def run_many(idf_paths, work):
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(lambda ip: simulate(ip, work / ip.stem), idf_paths))


def reference():
    """長興原表 24 點(通訊作者提供, 不隨倉庫公開; 本機 calibration_24h.csv)。"""
    with open(ROOT / "calibration_24h.csv", encoding="utf-8-sig") as f:
        return [float(r["reference_kW"]) for r in csv.DictReader(f)]


def ancillary7():
    with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as f:
        return [float(r["ancillary7_kWh"]) for r in csv.DictReader(f)]


def metrics(res, ref, anc7):
    comp = [a - b for a, b in zip(res["Electricity:Facility"], anc7)]
    mean = [sum(comp[h::24]) / 365 for h in range(24)]
    err = [m - t for m, t in zip(mean, ref)]
    rbar = sum(ref) / 24
    month = [int(k) for k in _months()]
    seas = {"S": [], "W": [], "T": []}
    for v, m in zip(comp, month):
        seas["S" if m in (6, 7, 8) else "W" if m in (12, 1, 2) else "T"].append(v)
    avg = {k: sum(v) / len(v) for k, v in seas.items()}
    return {
        "annual_MWh": sum(comp) / 1000,
        "july_MWh": sum(v for v, m in zip(comp, month) if m == 7) / 1000,
        "peak_kW": max(comp),
        "heating_MWh": sum(res["Heating:Electricity"]) / 1000,
        "cooling_MWh": sum(res["Cooling:Electricity"]) / 1000,
        "cvrmse_pct": 100 * (sum(e * e for e in err) / 23) ** 0.5 / rbar,
        "nmbe_pct": 100 * sum(err) / 23 / rbar,
        "summer_ratio": avg["S"] / avg["T"],
        "winter_ratio": avg["W"] / avg["T"],
        "hourly_mean": mean,
    }


_MONTHS = None


def _months():
    global _MONTHS
    if _MONTHS is None:
        with open(ROOT / "hourly_2025.csv", encoding="utf-8-sig") as f:
            _MONTHS = [r["interval_start"][5:7] for r in csv.DictReader(f)]
    return _MONTHS
