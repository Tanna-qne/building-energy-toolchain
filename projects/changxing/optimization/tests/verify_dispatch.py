"""Independent Python check for the renewables + battery + hydrogen dispatch.

Re-implements the dispatch rules in tests/reference_dispatch.csv and verifies
that the MATLAB entry point agrees. Run with:

    python3 tests/verify_dispatch.py            # verify against the reference
    python3 tests/verify_dispatch.py --write    # regenerate the reference

All energies are kWh over one-hour intervals; SOC and tank state are
fractions. Parameters mirror matlab/changxing_parameters.m and are
illustrative assumptions, not datasheet values.
"""

import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "july_load_pv.csv"
REFERENCE = ROOT / "tests" / "reference_dispatch.csv"

COLUMNS = [
    "load_kWh", "pv_kWh", "wind_kWh", "direct_kWh", "charge_kWh",
    "discharge_kWh", "soc_end", "electrolyser_kWh", "h2_produced_kg",
    "fuel_cell_kWh", "h2_consumed_kg", "grid_import_kWh", "curtailment_kWh",
    "tank_end",
]

# --- parameters, identical to matlab/changxing_parameters.m -----------------
BATTERY_CAPACITY = 240.0
BATTERY_POWER = 120.0
SOC_MIN, SOC_MAX, SOC_INITIAL = 0.10, 0.90, 0.10
ETA_CHARGE = ETA_DISCHARGE = 0.90

ELECTROLYSER_POWER = 100.0
ELECTROLYSER_KWH_PER_KG = 50.0

TANK_CAPACITY = 200.0
TANK_MIN, TANK_MAX, TANK_INITIAL = 0.10, 0.90, 0.10

FUEL_CELL_POWER = 100.0
FUEL_CELL_KWH_PER_KG = 18.0


def dispatch(rows):
    """Run the dispatch and return one dict per hour."""
    energy_min = SOC_MIN * BATTERY_CAPACITY
    energy_max = SOC_MAX * BATTERY_CAPACITY
    tank_min = TANK_MIN * TANK_CAPACITY
    tank_max = TANK_MAX * TANK_CAPACITY
    soc = SOC_INITIAL * BATTERY_CAPACITY
    tank = TANK_INITIAL * TANK_CAPACITY
    results = []

    for item in rows:
        load = float(item["load_kWh"])
        solar = float(item["pv_kWh"])
        wind = float(item.get("wind_kWh") or 0.0)
        renewable = solar + wind
        record = dict.fromkeys(COLUMNS, 0.0)
        record.update(load_kWh=load, pv_kWh=solar, wind_kWh=wind)

        if renewable >= load:
            surplus = renewable - load
            record["direct_kWh"] = load

            room_ac = max(energy_max - soc, 0.0) / ETA_CHARGE
            charge = min(surplus, BATTERY_POWER, room_ac)
            soc += ETA_CHARGE * charge
            surplus -= charge
            record["charge_kWh"] = charge

            tank_room = max(tank_max - tank, 0.0)
            electrolyser = min(surplus, ELECTROLYSER_POWER,
                               tank_room * ELECTROLYSER_KWH_PER_KG)
            produced = electrolyser / ELECTROLYSER_KWH_PER_KG
            tank += produced
            surplus -= electrolyser
            record["electrolyser_kWh"] = electrolyser
            record["h2_produced_kg"] = produced
            record["curtailment_kWh"] = surplus
        else:
            deficit = load - renewable
            record["direct_kWh"] = renewable

            available_ac = max(soc - energy_min, 0.0) * ETA_DISCHARGE
            discharge = min(deficit, BATTERY_POWER, available_ac)
            soc -= discharge / ETA_DISCHARGE
            deficit -= discharge
            record["discharge_kWh"] = discharge

            usable = max(tank - tank_min, 0.0) * FUEL_CELL_KWH_PER_KG
            fuel_cell = min(deficit, FUEL_CELL_POWER, usable)
            tank -= fuel_cell / FUEL_CELL_KWH_PER_KG
            deficit -= fuel_cell
            record["fuel_cell_kWh"] = fuel_cell
            record["h2_consumed_kg"] = fuel_cell / FUEL_CELL_KWH_PER_KG
            record["grid_import_kWh"] = deficit

        record["soc_end"] = soc / BATTERY_CAPACITY
        record["tank_end"] = tank / TANK_CAPACITY
        results.append(record)

    return results


def check_invariants(results):
    for hour, r in enumerate(results, start=1):
        renewable = r["pv_kWh"] + r["wind_kWh"]
        balance = (renewable + r["discharge_kWh"] + r["fuel_cell_kWh"]
                   + r["grid_import_kWh"] - r["load_kWh"]
                   - r["charge_kWh"] - r["electrolyser_kWh"]
                   - r["curtailment_kWh"])
        if abs(balance) > 1e-5:
            raise AssertionError(f"hour {hour}: energy balance off by {balance}")
        if not (SOC_MIN - 1e-8 <= r["soc_end"] <= SOC_MAX + 1e-8):
            raise AssertionError(f"hour {hour}: SOC outside bounds")
        if not (TANK_MIN - 1e-8 <= r["tank_end"] <= TANK_MAX + 1e-8):
            raise AssertionError(f"hour {hour}: tank level outside bounds")
        if r["charge_kWh"] > 1e-8 and r["discharge_kWh"] > 1e-8:
            raise AssertionError(f"hour {hour}: simultaneous battery charge/discharge")
        if r["electrolyser_kWh"] > 1e-8 and r["fuel_cell_kWh"] > 1e-8:
            raise AssertionError(f"hour {hour}: electrolyser and fuel cell both ran")


def totals(results):
    total = {key: sum(r[key] for r in results) for key in COLUMNS}
    total["load_kWh"] = total.pop("load_kWh")
    return total


def load_rows():
    with INPUT.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_reference(rows, results):
    with REFERENCE.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["interval_start_CST"] + COLUMNS)
        for item, result in zip(rows, results):
            writer.writerow([item["interval_start_CST"]] +
                            [f"{result[key]:.10f}" for key in COLUMNS])


def compare_reference(rows, results):
    with REFERENCE.open(newline="", encoding="utf-8") as stream:
        stored = list(csv.DictReader(stream))
    if len(stored) != len(results):
        raise AssertionError("Reference row count does not match the input")
    for hour, (item, result) in enumerate(zip(rows, results), start=1):
        if item["interval_start_CST"] != stored[hour - 1]["interval_start_CST"]:
            raise AssertionError(f"hour {hour}: timestamp mismatch")
        for key in COLUMNS:
            if abs(result[key] - float(stored[hour - 1][key])) > 1e-5:
                raise AssertionError(
                    f"hour {hour}, {key}: {result[key]} differs from the reference")


def main():
    rows = load_rows()
    results = dispatch(rows)
    check_invariants(results)
    total = totals(results)

    if "--write" in sys.argv:
        write_reference(rows, results)
        print(f"Wrote {len(results)} hours to {REFERENCE.name}")
    else:
        compare_reference(rows, results)

    renewable = total["pv_kWh"] + total["wind_kWh"]
    supplied = total["direct_kWh"] + total["discharge_kWh"] + total["fuel_cell_kWh"]
    print(f"Verified {len(results)} hours of renewable + battery + H2 dispatch.")
    print(f"Load {total['load_kWh']:.2f} kWh, renewable {renewable:.2f} kWh")
    print(f"Direct {total['direct_kWh']:.2f}, battery {total['discharge_kWh']:.2f}, "
          f"fuel cell {total['fuel_cell_kWh']:.2f}, grid {total['grid_import_kWh']:.2f}")
    print(f"Electrolyser {total['electrolyser_kWh']:.2f} kWh -> "
          f"{total['h2_produced_kg']:.2f} kg H2; curtailed {total['curtailment_kWh']:.2f} kWh")
    print(f"Renewable share of load {100 * supplied / total['load_kWh']:.2f}%")


if __name__ == "__main__":
    main()
