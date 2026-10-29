from datetime import datetime
from math import isfinite
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Asset(Contract):
    id: str
    name: str
    kind: Literal["separator", "compressor", "valve", "pipe", "vent", "flange"]
    area: str
    position: tuple[float, float, float]
    dimensions: tuple[float, float, float]
    pressure_class: str
    document_ids: list[str]
    origin_type: Literal["simulated"] = "simulated"

    @model_validator(mode="after")
    def positive_dimensions(self):
        if any(d <= 0 for d in self.dimensions):
            raise ValueError("dimensions must be positive")
        return self

class Connection(Contract):
    from_id: str
    to_id: str
    line_id: str
    relation: Literal["FLOWS_TO"] = "FLOWS_TO"
    verification: Literal["synthetic_design"] = "synthetic_design"

class Region(Contract):
    asset_id: str
    polygon: list[tuple[float, float]] = Field(min_length=3)
    provenance: Literal["synthetic_illustrative_mapping"] = "synthetic_illustrative_mapping"

    @model_validator(mode="after")
    def normalized(self):
        validate_polygon(self.polygon)
        return self

def validate_polygon(polygon):
    if any(not isfinite(v) or not 0 <= v <= 1 for p in polygon for v in p):
        raise ValueError("polygon coordinates must be normalized and finite")
    area = abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon, polygon[1:]+polygon[:1]))) / 2
    if area <= 1e-8:
        raise ValueError("polygon must have positive area")

class Camera(Contract):
    id: str
    position: tuple[float,float,float]
    orientation_degrees: tuple[float,float,float]  # roll, pitch (up positive), heading clockwise from north
    fov_degrees: float = Field(gt=0,lt=180)
    resolution: tuple[int,int]
    calibration_status: Literal["illustrative_uncalibrated"]
    video_id: str | None
    regions: list[Region]

class Sensor(Contract):
    id: str
    asset_id: str
    variable: Literal["methane", "pressure", "temperature", "flow", "wind_speed", "wind_direction"]
    unit: str
    position: tuple[float,float,float]
    sample_interval_seconds: float = Field(gt=0)
    uncertainty: float = Field(ge=0)
    detection_limit: float = Field(ge=0)
    lag_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def units(self):
        units = {"methane":"ppm", "pressure":"bar(a)", "temperature":"K", "flow":"kg/h", "wind_speed":"m/s", "wind_direction":"deg_from_north"}
        if self.unit != units[self.variable]:
            raise ValueError("unit does not match measured variable")
        return self

class Facility(Contract):
    id: str
    name: str
    coordinate_reference: Literal["local_ENU_m"]
    geometry_version: str
    assets: list[Asset]
    connections: list[Connection]
    cameras: list[Camera]
    sensors: list[Sensor]

    @model_validator(mode="after")
    def references(self):
        ids = [a.id for a in self.assets]+[s.id for s in self.sensors]+[c.id for c in self.cameras]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate canonical ID")
        assets = {a.id for a in self.assets}
        for c in self.connections:
            if c.from_id not in assets or c.to_id not in assets or c.from_id == c.to_id:
                raise ValueError("invalid process edge")
        if any(s.asset_id not in assets for s in self.sensors):
            raise ValueError("dangling sensor attachment")
        if any(r.asset_id not in assets for c in self.cameras for r in c.regions):
            raise ValueError("dangling camera mapping")
        visited = set()
        if assets:
            stack = [next(iter(assets))]
            while stack:
                a = stack.pop()
                if a in visited: continue
                visited.add(a)
                stack.extend(c.to_id if c.from_id == a else c.from_id for c in self.connections if a in (c.from_id,c.to_id))
        if visited != assets:
            raise ValueError("disconnected equipment graph")
        return self

class Provenance(Contract):
    source_kind: Literal["recorded", "simulated", "derived", "manually_annotated", "inferred"]
    source_dataset: str
    generator_version: str
    reference: str

class DetectionEvent(Contract):
    event_id: str
    incident_id: str
    scenario_id: str
    camera_id: str
    timestamp_utc: AwareDatetime
    clip_time_seconds: float | None = Field(default=None,ge=0)
    mode: Literal["synthetic_annotation_fixture", "annotation_replay", "model_inference"]
    origin_polygon: list[tuple[float,float]] = Field(min_length=3)
    provenance: Provenance
    confidence: float | None = Field(default=None,ge=0,le=1)

    @model_validator(mode="after")
    def honest_fixture(self):
        validate_polygon(self.origin_polygon)
        if self.mode != "model_inference" and self.confidence is not None:
            raise ValueError("annotation confidence is not model confidence")
        if self.mode == "synthetic_annotation_fixture" and (self.provenance.source_kind != "simulated" or self.clip_time_seconds is not None):
            raise ValueError("synthetic fixture cannot claim a recorded video timestamp")
        return self

class Measurement(Contract):
    observation_id: str
    timestamp_utc: AwareDatetime
    scenario_id: str
    sensor_id: str
    asset_id: str
    value: float | None
    unit: str
    quality_flag: Literal["good", "missing", "below_detection", "invalid_wind"]
    origin_type: Literal["simulated"] = "simulated"
    generator_version: str = "gaussian-fixture-1"

    @model_validator(mode="after")
    def missing_value(self):
        if (self.value is None) != (self.quality_flag == "missing"):
            raise ValueError("only missing readings may have null values")
        return self


IncidentState = Literal["observed", "localized_unconfirmed", "quantified_or_unavailable", "plan_drafted", "pending_review", "approved", "rejected", "reported"]

class EvidenceReference(Contract):
    id: str
    kind: Literal["detection", "camera_mapping", "graph_fact", "measurement", "document", "calculation", "review"]
    uri: str
    provenance: Provenance

class IncidentTransition(Contract):
    """Future durable workflow envelope; does not grant transition permission."""
    event_id: str
    incident_id: str
    scenario_id: str
    timestamp_utc: AwareDatetime
    from_state: IncidentState
    to_state: IncidentState
    actor_id: str
    actor_type: Literal["system", "human", "agent"]
    evidence_ids: list[str]
    idempotency_key: str
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def review_boundary(self):
        if self.to_state in ("approved", "rejected") and self.actor_type != "human":
            raise ValueError("review decisions require an authenticated human; schema is not authentication")
        if self.to_state == self.from_state:
            raise ValueError("transition must change state")
        return self

class ReviewDecision(Contract):
    incident_id: str
    draft_id: str
    draft_version: int = Field(ge=1)
    decision: Literal["approve", "reject", "request_changes"]
    rationale: str = Field(min_length=1)
    idempotency_key: str
    # Reviewer identity MUST come from verified authentication, never request fields.
