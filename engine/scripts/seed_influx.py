"""Idempotent canonical replay ingestion; independent measured data has a separate bucket."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from app.contracts import Measurement
from app.data import DATA, load_facility, load_json
from app.historian import HISTORY_SCENARIO_ID


def observations(scenario_ids):
    """Scenario replays, then the plant's pre-incident history under its own scenario tag."""
    for name, allowed in (("generated/measurements.jsonl", scenario_ids), ("generated/history.jsonl", {HISTORY_SCENARIO_ID})):
        path = DATA / name
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                r = Measurement.model_validate_json(line)
                if r.scenario_id not in allowed:
                    raise ValueError("Invalid canonical observation; ingestion stopped")
                yield r


def seed():
    facility = load_facility()
    sensors = {s.id: s for s in facility.sensors}
    scenarios = {s["id"]: s for s in load_json("scenarios/scenarios.json")}
    org = os.getenv("INFLUX_ORG", "atelic")
    bucket = os.getenv("INFLUX_BUCKET", "incident-observations")
    with InfluxDBClient(url=os.getenv("INFLUX_URL", "http://127.0.0.1:8286"), token=os.environ["INFLUX_TOKEN"], org=org, timeout=60000) as client:
        if client.buckets_api().find_bucket_by_name(bucket) is None:
            client.buckets_api().create_bucket(bucket_name=bucket, org=org, retention_rules=[])
        write = client.write_api(write_options=SYNCHRONOUS)
        points = []
        count = 0
        for r in observations(set(scenarios)):
            s = sensors[r.sensor_id]
            if s.asset_id != r.asset_id or s.unit != r.unit:
                raise ValueError("Invalid canonical observation; ingestion stopped")
            # Same sensor/scenario/time identifies a point. Every field is overwritten
            # on retry; a missing observation is NOT silently written as measured zero.
            p = (Point("observation").tag("scenario_id", r.scenario_id)
                 .tag("sensor_id", r.sensor_id).tag("asset_id", r.asset_id).tag("unit", r.unit)
                 .field("observation_id", r.observation_id).field("quality_flag", r.quality_flag)
                 .field("origin_type", r.origin_type).field("generator_version", r.generator_version)
                 .field("value", float(r.value) if r.value is not None else 0.0)
                 .time(r.timestamp_utc, WritePrecision.NS))
            points.append(p)
            count += 1
            if len(points) == 2000:
                write.write(bucket=bucket, record=points)
                points = []
        if points:
            write.write(bucket=bucket, record=points)
        write.close()
    print(f"Ingested {count} canonical observations into isolated InfluxDB; missing values retain QC")


if __name__ == "__main__":
    seed()
