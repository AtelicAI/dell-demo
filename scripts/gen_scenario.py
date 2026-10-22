"""Generate simulated sensor + weather CSVs for INC-001. Deterministic (no randomness).

Run: python3 scripts/gen_scenario.py
Output is SIMULATED data (provenance: synthetic), driven by data/scenario/release_truth.json.
"""
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "scenario"
STEP_S = 30


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    site = json.loads((DATA / "site.json").read_text())
    truth = json.loads((DATA / "release_truth.json").read_text())
    start, end = parse(site["replay_start_utc"]), parse(site["replay_end_utc"])
    release = parse(truth["release_start_utc"])

    sensors, weather = [], []
    t = start
    while t <= end:
        m = (t - start).total_seconds() / 60
        on = t >= release
        ramp = min(1.0, (t - release).total_seconds() / 300) if on else 0.0
        ch4 = 2.0 + 0.1 * math.sin(m / 3) + ramp * (9.0 + 2.5 * math.sin(m * 1.7))
        pressure = 42.0 + 0.05 * math.sin(m / 5) - ramp * 0.4
        wind_speed = 3.5 + 0.6 * math.sin(m / 4)
        wind_dir = 250 + 12 * math.sin(m / 7)

        sensors.append([iso(t), "GD-01", "methane_ppm", round(ch4, 2)])
        sensors.append([iso(t), "PT-101", "pressure_bara", round(pressure, 3)])
        weather.append([iso(t), "WS-01", round(wind_speed, 2), round(wind_dir, 1)])
        t += timedelta(seconds=STEP_S)

    with open(DATA / "sensors.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "sensor_id", "quantity", "value"])
        w.writerows(sensors)
    with open(DATA / "weather.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "sensor_id", "wind_speed_m_s", "wind_dir_deg_from_north"])
        w.writerows(weather)
    print(f"wrote {len(sensors)} sensor rows, {len(weather)} weather rows")


if __name__ == "__main__":
    main()
