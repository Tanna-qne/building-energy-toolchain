"""Generate the Changxing aggregate charging scenario (Python 3, standard library)."""
import calendar
import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

OUT = Path(__file__).resolve().parent
PROJECT = OUT.parent
INPUT = PROJECT / "charging_inputs"
SEED = 20250701
UNIT_KW = 60  # equivalent meter-side power, scenario assumption


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write(name, fields, rows):
    with (OUT / name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)


def main():
    ref_rows = read(INPUT / "changxing_reference_24h.csv")
    assert [int(r["hour_start"]) for r in ref_rows] == list(range(24))
    ref = [float(r["charging_kW"]) for r in ref_rows]
    months = read(INPUT / "zhejiang_monthly.csv")
    assert [int(r["month"]) for r in months] == list(range(1, 13))
    provincial_total = sum(float(r["energy_10000kWh"]) for r in months)
    pools = {False: [], True: []}
    for r in read(INPUT / "shenzhen_daily_variation.csv"):
        pools[int(r["weekday_iso"]) >= 6].append(float(r["relative_to_month_daily_mean"]))
    rng = random.Random(SEED)
    annual = []
    monthly = []
    for r in months:
        m = int(r["month"])
        n = calendar.monthrange(2025, m)[1]
        factor = float(r["energy_10000kWh"]) / n / (provincial_total / 365)
        assert n == int(r["days_2022"]) and abs(factor - float(r["daily_factor"])) < 1e-8
        dates = [datetime(2025, m, i + 1) for i in range(n)]
        samples = [rng.choice(pools[day.weekday() >= 5]) for day in dates]
        normalizer = mean(samples)
        energies = []
        for day, sample in zip(dates, samples):
            for hour, reference in enumerate(ref):
                start = day + timedelta(hours=hour)
                energy = round(reference * factor * sample / normalizer, 6)
                annual.append((start, start + timedelta(hours=1), energy))
                energies.append(energy)
        monthly.append([m, factor, sum(energies), max(energies), n])
    assert len(annual) == 8760 and all(x[1] == y[0] for x,y in zip(annual, annual[1:]))
    annual_mean_day = sum(row[2] for row in annual) / 365
    peak = max(row[2] for row in annual)
    positions = math.ceil(peak / UNIT_KW)
    print("capacity", peak, positions, "hours above 720", sum(row[2]>720 for row in annual))
    write("charging_hourly_2025.csv", ["interval_start","interval_end","charging_kWh"], annual)
    write("charging_monthly.csv", ["month","target_daily_factor","charging_kWh","peak_hourly_kW","actual_daily_factor","capacity_kW","minimum_hourly_headroom_kW"], [row[:4]+[row[2]/row[4]/annual_mean_day,positions*UNIT_KW,positions*UNIT_KW-row[3]] for row in monthly])
    cal = []
    for h in range(24):
        simulated = mean(row[2] for row in annual[h::24])
        cal.append([h,ref[h],simulated,simulated-ref[h]])
    write("calibration_24h.csv", ["hour_start","reference_kW","annual_mean_kW","signed_error_kW"],cal)
    print("errors",math.sqrt(mean(r[3]**2 for r in cal)), max(abs(r[3]) for r in cal))
    july = [row for row in annual if row[0].month==7]
    base = read(PROJECT / "hourly_2025_07.csv")
    assert len(base)==len(july)==744
    combined = []
    from decimal import Decimal
    for (start,end,energy), b in zip(july,base):
        assert (str(start),str(end))==(b["interval_start"],b["interval_end"])
        site = Decimal(b["station_total_kWh"])
        combined.append([start,end,site,energy,site+Decimal(str(energy))])
    write("total_hourly_2025_07.csv",["interval_start","interval_end","station_total_kWh","charging_kWh","total_kWh"],combined)
    print("July",sum(x[2] for x in combined),sum(x[3] for x in combined),sum(x[4] for x in combined),max(combined,key=lambda x:x[4]))
    print("monthly target above 720",[(r[0],round(max(ref)*r[1],2)) for r in monthly if max(ref)*r[1]>720])


if __name__ == "__main__":
    main()
