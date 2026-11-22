"""單速(cx_singlespeed.idf)與變頻(定版 cx_lowest_max.idf)兩個校準後模型的對比 → compare.json。

    uv run --with eppy python projects/changxing/hvac_vs/compare.py

兩個模型都各自校準到長興原表, 所以差別反映「空調模型的假設改變後, 校準把電量分到哪裡」。
除電表外另輸出盤管的供冷 / 供熱量與耗電, 用來算季節 COP。需要本機的長興原表(算 CV(RMSE))。
"""
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sensitivity"))
from common import BASE_IDF, EP_EXE, EPW, ancillary7, metrics, reference  # noqa: E402

MODELS = {"singlespeed": HERE / "cx_singlespeed.idf", "variable_speed": BASE_IDF}
COIL_VARS = ["Heating Coil Electricity Energy", "Heating Coil Heating Energy", "Heating Coil Defrost Electricity Energy",
             "Cooling Coil Electricity Energy", "Cooling Coil Total Cooling Energy"]
METERS = ("Electricity:Facility", "Heating:Electricity", "Cooling:Electricity", "Fans:Electricity")


def run(idf):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        text = idf.read_text(encoding="latin-1") + "".join(f"\nOutput:Variable,*,{v},RunPeriod;\n" for v in COIL_VARS)
        (d / "m.idf").write_text(text, encoding="latin-1")
        subprocess.run([str(EP_EXE), "-w", str(EPW), "-d", str(d), "-r", str(d / "m.idf")],
                       capture_output=True, cwd=d, check=True, stdin=subprocess.DEVNULL)
        rows = list(csv.reader(open(d / "eplusout.csv", encoding="latin-1")))
        coils = {}
        for h, v in zip(rows[0][1:], rows[-1][1:]):
            key, var = h.split(":", 1)
            var = var.split(" [")[0]
            if var in COIL_VARS:
                tag = var + (" (supplemental)" if "SUPPLEMENTAL" in key.upper() else "")
                coils[tag] = coils.get(tag, 0) + float(v) / 3.6e9
        with open(d / "eplusmtr.csv", encoding="latin-1") as f:
            m = list(csv.reader(f))
        res = {n: [float(r[m[0].index(n + " [J](Hourly)")]) / 3.6e6 for r in m[1:]] for n in METERS}
    return res, coils


def main():
    out = {}
    for tag, idf in MODELS.items():
        res, coils = run(idf)
        met = metrics(res, reference(), ancillary7())
        met.pop("hourly_mean")
        met["fans_MWh"] = sum(res["Fans:Electricity"]) / 1000
        met["station_total_MWh"] = sum(res["Electricity:Facility"]) / 1000
        met["coils_MWh"] = coils
        met["seasonal_cop_cooling"] = coils["Cooling Coil Total Cooling Energy"] / coils["Cooling Coil Electricity Energy"]
        met["seasonal_cop_heating"] = coils["Heating Coil Heating Energy"] / coils["Heating Coil Electricity Energy"]
        out[tag] = met
        print(tag, {k: round(v, 3) for k, v in met.items() if isinstance(v, float)}, flush=True)
    (HERE / "compare.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
