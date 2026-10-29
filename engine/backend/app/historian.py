"""Disclosed file-backed simulated historian; no claim of InfluxDB integration."""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from .contracts import Measurement
from .data import DATA

HISTORY_SCENARIO_ID = "HIST-FAC-001"

class Historian:
    backend="local_simulated_history"
    def __init__(self, facility):
        self.facility=facility
        self.rows=defaultdict(list)
        sensors={s.id:s for s in facility.sensors}
        with (DATA/"generated/measurements.jsonl").open() as f:
            for line in f:
                row=Measurement.model_validate_json(line)
                s=sensors[row.sensor_id]
                if row.asset_id!=s.asset_id or row.unit!=s.unit:raise ValueError("historian canonical reference mismatch")
                self.rows[row.scenario_id].append(row)
    def history(self, scenario, as_of, window, sensor_ids=None):
        end=datetime.fromisoformat(scenario['start_utc'])+timedelta(seconds=as_of)
        start=end-timedelta(seconds=window)
        return [r.model_dump(mode='json') for r in self.rows[scenario['id']] if start<=r.timestamp_utc<=end and (sensor_ids is None or r.sensor_id in sensor_ids)]

    def long_history(self, start, end):
        """Plant history (5-minute averages) in [start, end); loaded on first use."""
        if not hasattr(self, 'long_rows'):
            sensors = {s.id: s for s in self.facility.sensors}
            self.long_rows = []
            path = DATA / "generated/history.jsonl"
            if path.exists():
                with path.open() as f:
                    for line in f:
                        row = Measurement.model_validate_json(line)
                        s = sensors[row.sensor_id]
                        if row.asset_id != s.asset_id or row.unit != s.unit:
                            raise ValueError("historian canonical reference mismatch")
                        self.long_rows.append(row)
        return [r.model_dump(mode='json') for r in self.long_rows if start <= r.timestamp_utc < end]

    def health(self):
        return bool(self.rows)

    def close(self):
        pass


class HistorianUnavailable(RuntimeError):
    pass


class InfluxHistorian:
    """Real InfluxDB v2 queries; missing samples retain QC and decode to null."""
    backend = "influxdb"

    def __init__(self, facility, client=None):
        import os
        from influxdb_client import InfluxDBClient
        self.facility = facility
        self.bucket = os.getenv("INFLUX_BUCKET", "incident-observations")
        self.client = client or InfluxDBClient(
            url=os.getenv("INFLUX_URL", "http://127.0.0.1:8286"),
            token=os.environ["INFLUX_TOKEN"], org=os.getenv("INFLUX_ORG", "atelic"),
            timeout=30000,
        )

    def health(self):
        try:
            return self.client.ping()
        except Exception:
            return False

    def close(self):
        self.client.close()

    def history(self, scenario, as_of, window, sensor_ids=None):
        import json
        end = datetime.fromisoformat(scenario["start_utc"]) + timedelta(seconds=as_of)
        start = end - timedelta(seconds=window)
        # Flux stop is exclusive; preserve the existing API's inclusive boundary.
        stop = end + timedelta(microseconds=1)
        quote = json.dumps
        query = f'''from(bucket: {quote(self.bucket)})
          |> range(start: time(v: {quote(start.isoformat())}), stop: time(v: {quote(stop.isoformat())}))
          |> filter(fn: (r) => r._measurement == "observation" and r.scenario_id == {quote(scenario['id'])})'''
        if sensor_ids is not None:
            if not sensor_ids:
                return []
            query += '\n |> filter(fn: (r) => contains(value: r.sensor_id, set: ' + quote(sorted(sensor_ids)) + '))'
        query += '\n |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")'
        try:
            tables = self.client.query_api().query(query)
            sensors = {s.id: s for s in self.facility.sensors}
            result = []
            for table in tables:
                for record in table.records:
                    r = record.values
                    sensor = sensors[r["sensor_id"]]
                    if r["asset_id"] != sensor.asset_id or r["unit"] != sensor.unit:
                        raise ValueError("InfluxDB observation disagrees with canonical sensor")
                    row = Measurement(
                        observation_id=r["observation_id"], timestamp_utc=r["_time"],
                        scenario_id=r["scenario_id"], sensor_id=r["sensor_id"], asset_id=r["asset_id"],
                        unit=r["unit"], quality_flag=r["quality_flag"],
                        value=None if r["quality_flag"] == "missing" else r["value"],
                        generator_version=r["generator_version"], origin_type=r["origin_type"],
                    )
                    result.append(row.model_dump(mode="json"))
            return sorted(result, key=lambda r: (r["timestamp_utc"], r["sensor_id"]))
        except Exception as exc:
            raise HistorianUnavailable("InfluxDB query failed or returned invalid canonical observations") from exc

    def long_history(self, start, end):
        """Plant history (5-minute averages) in [start, end) from the same bucket, separate scenario tag."""
        quote = json.dumps
        query = f'''from(bucket: {quote(self.bucket)})
          |> range(start: time(v: {quote(start.isoformat())}), stop: time(v: {quote(end.isoformat())}))
          |> filter(fn: (r) => r._measurement == "observation" and r.scenario_id == {quote(HISTORY_SCENARIO_ID)})
          |> pivot(rowKey: ["_time", "sensor_id"], columnKey: ["_field"], valueColumn: "_value")'''
        try:
            sensors = {s.id: s for s in self.facility.sensors}
            result = []
            for table in self.client.query_api().query(query):
                for record in table.records:
                    r = record.values
                    sensor = sensors[r["sensor_id"]]
                    if r["asset_id"] != sensor.asset_id or r["unit"] != sensor.unit:
                        raise ValueError("InfluxDB observation disagrees with canonical sensor")
                    result.append(Measurement(
                        observation_id=r["observation_id"], timestamp_utc=r["_time"], scenario_id=r["scenario_id"],
                        sensor_id=r["sensor_id"], asset_id=r["asset_id"], unit=r["unit"], quality_flag=r["quality_flag"],
                        value=None if r["quality_flag"] == "missing" else r["value"],
                        generator_version=r["generator_version"], origin_type=r["origin_type"]).model_dump(mode="json"))
            return sorted(result, key=lambda r: (r["timestamp_utc"], r["sensor_id"]))
        except Exception as exc:
            raise HistorianUnavailable("InfluxDB plant-history query failed or returned invalid observations") from exc


def create_historian(facility):
    import os
    backend = os.getenv("HISTORIAN_BACKEND", "file")
    if backend == "file":
        return Historian(facility)
    if backend == "influxdb":
        return InfluxHistorian(facility)
    raise ValueError("HISTORIAN_BACKEND must explicitly be file or influxdb")
