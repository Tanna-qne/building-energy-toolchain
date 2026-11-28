"""Verify the 744-hour input and the independent reference calculation."""

import csv
import datetime as dt
import hashlib
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "july_load_pv.csv"
REFERENCE = ROOT / "tests" / "reference_july_balance.csv"
EXPECTED_SHA256 = "563e75d3bf65a7b87131730ddb90b180d11c24b37714fa1faf84b19041a3c587"


def rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def check_close(actual, expected, name, hour):
    if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-5):
        raise AssertionError(f"hour {hour}, {name}: {actual} differs from {expected}")


def main():
    digest = hashlib.sha256(INPUT.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise AssertionError(f"Input changed: expected {EXPECTED_SHA256}, got {digest}")

    data = rows(INPUT)
    reference = rows(REFERENCE)
    if len(data) != 744 or len(reference) != 744:
        raise AssertionError("Both files must contain 744 hourly rows")

    capacity = 240.0
    power = 120.0
    efficiency = 0.9
    lower = 0.1 * capacity
    upper = 0.9 * capacity
    energy = lower
    initial = lower
    first_hour = dt.datetime(2025, 7, 1)
    totals = {"base_surplus": 0.0, "surplus": 0.0, "charge": 0.0, "discharge": 0.0}

    for index, (item, result) in enumerate(zip(data, reference)):
        time = dt.datetime.fromisoformat(item["interval_start_CST"])
        if time != first_hour + dt.timedelta(hours=index):
            raise AssertionError(f"Unexpected time at row {index + 1}")
        if item["interval_start_CST"] != result["interval_start_CST"]:
            raise AssertionError(f"Reference timestamp mismatch at row {index + 1}")

        load = float(item["load_kWh"])
        solar = float(item["pv_kWh"])
        if not all(math.isfinite(x) and x >= 0 for x in (load, solar)):
            raise AssertionError(f"Invalid energy at row {index + 1}")
        check_close(load - solar, float(item["load_minus_pv_kWh"]), "input net", index)

        direct = min(load, solar)
        surplus = max(solar - load, 0.0)
        gap = max(load - solar, 0.0)
        charge = min(surplus, power, max(upper - energy, 0.0) / efficiency)
        discharge = min(gap, power, max(energy - lower, 0.0) * efficiency)
        energy += efficiency * charge - discharge / efficiency
        residual_surplus = surplus - charge
        remaining_gap = gap - discharge

        expected = {
            "load_kWh": load,
            "pv_kWh": solar,
            "pv_direct_kWh": direct,
            "surplus_no_battery_kWh": surplus,
            "gap_no_battery_kWh": gap,
            "battery_charge_kWh": charge,
            "battery_discharge_kWh": discharge,
            "surplus_with_battery_kWh": residual_surplus,
            "gap_with_battery_kWh": remaining_gap,
            "SOC_end": energy / capacity,
        }
        for key, actual in expected.items():
            check_close(actual, float(result[key]), key, index)
        check_close(solar + discharge + remaining_gap, load + charge + residual_surplus, "energy balance", index)
        if not lower - 1e-8 <= energy <= upper + 1e-8:
            raise AssertionError(f"Battery SOC outside bounds at row {index + 1}")
        if charge > 1e-8 and discharge > 1e-8:
            raise AssertionError(f"Simultaneous charge and discharge at row {index + 1}")

        totals["base_surplus"] += surplus
        totals["surplus"] += residual_surplus
        totals["charge"] += charge
        totals["discharge"] += discharge

    check_close(energy, initial, "final stored energy", len(data))
    check_close(totals["base_surplus"], 10081.504913, "baseline surplus", len(data))
    check_close(totals["surplus"], 6404.239717666666, "battery surplus", len(data))
    check_close(efficiency * totals["charge"] - totals["discharge"] / efficiency,
                energy - initial, "battery energy conservation", len(data))
    print(f"Verified {len(data)} hours, hourly balance, battery limits and reference output.")
    print(f"PV surplus: {totals['base_surplus']:.2f} -> {totals['surplus']:.2f} kWh")


if __name__ == "__main__":
    main()
