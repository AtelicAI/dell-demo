"""In-memory incident state. Single hard-coded incident until persistence is added."""

STAGES = ["detect", "locate", "quantify", "plan", "approve", "report"]

INCIDENTS = {
    "INC-001": {
        "local_incident_id": "INC-001",
        "site": "Gas-handling skid",
        "camera_id": "CAM-01",
        "source_status": "Source unconfirmed",
        "scenario": "Illustrative scenario",
        # Nothing has run yet, so every stage is honestly not started.
        "stages": [{"stage": s, "status": "not_started"} for s in STAGES],
    }
}
