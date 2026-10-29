"""Generate simulated sensor + weather CSVs for a live incident. Deterministic (fixed random seed).

Run: python3 scripts/gen_scenario.py [INC-001|INC-002]   (default INC-001)
Output is SIMULATED data (provenance: synthetic), driven by the incident's release_truth.json.
INC-001 reads and writes data/scenario/ (sensors.csv + the site-wide weather.csv); INC-002 reads
data/incidents/INC-002/ and writes only its sensors.csv (it shares weather.csv and site.json).
The detector (GD-01, or detector_id in release_truth.json) follows the Gaussian plume model in backend/app/plume.py with the draft geometry.json,
plus seeded turbulence so 30 s samples scatter around the time-averaged plume.
"""
import csv
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT / "data" / "scenario"
STEP_S = 30
BACKGROUND_PPM = 2.0
TURBULENCE_SIGMA = 0.25  # log-normal spread of 30 s samples around the mean plume

sys.path.insert(0, str(ROOT / "backend"))
from app.plume import ppm_per_kg_h  # noqa: E402


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    incident = sys.argv[1] if len(sys.argv) > 1 else "INC-001"
    data = SCENARIO if incident == "INC-001" else ROOT / "data" / "incidents" / incident
    site = json.loads((SCENARIO / "site.json").read_text())
    truth = json.loads((data / "release_truth.json").read_text())
    detector = truth.get("detector_id", "GD-01")
    start, end = parse(site["replay_start_utc"]), parse(site["replay_end_utc"])
    release = parse(truth["release_start_utc"])
    geo = json.loads((data / "geometry.json").read_text())
    src, rec = geo["assets"][truth["source_asset_id"]], geo["assets"][detector]
    rng = random.Random(42)

    sensors, weather = [], []
    t = start
    while t <= end:
        m = (t - start).total_seconds() / 60
        on = t >= release
        wind_speed = 3.5 + 0.6 * math.sin(m / 4)
        wind_dir = 250 + 12 * math.sin(m / 7)
        plume = truth["rate_kg_ch4_h"] * ppm_per_kg_h(src, rec, wind_speed, wind_dir, geo["stability_class"]) if on else 0.0
        ch4 = BACKGROUND_PPM + 0.1 * math.sin(m / 3) + plume * math.exp(rng.gauss(0, TURBULENCE_SIGMA))
        pressure = 42.0 + 0.05 * math.sin(m / 5) - (0.4 if on else 0.0)

        sensors.append([iso(t), detector, "methane_ppm", round(ch4, 2)])
        sensors.append([iso(t), "PT-101", "pressure_bara", round(pressure, 3)])
        weather.append([iso(t), "WS-01", round(wind_speed, 2), round(wind_dir, 1)])
        t += timedelta(seconds=STEP_S)

    with open(data / "sensors.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "sensor_id", "quantity", "value"])
        w.writerows(sensors)
    if data != SCENARIO:
        print(f"wrote {len(sensors)} sensor rows to {data / 'sensors.csv'} (weather.csv is shared, not rewritten)")
        return
    with open(SCENARIO / "weather.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "sensor_id", "wind_speed_m_s", "wind_dir_deg_from_north"])
        w.writerows(weather)
    print(f"wrote {len(sensors)} sensor rows, {len(weather)} weather rows")


if __name__ == "__main__":
    main()
