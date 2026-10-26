"""In-memory incident state. Single hard-coded incident until persistence is added."""

STAGES = ["detect", "locate", "quantify", "plan", "approve", "report"]

INCIDENTS = {
    "INC-001": {
        "local_incident_id": "INC-001",
        "site": "Gas-handling unit",
        "camera_id": "CAM-01",
        "source_status": "Source unconfirmed",
        "scenario": None,
        # Normal operations until CAM-01 raises the alert (POST /incidents/:id/trigger); reset clears it.
        "triggered": False,
        "triggered_at": None,
        # Nothing has run yet, so every stage is honestly not started.
        "stages": [{"stage": s, "status": "not_started"} for s in STAGES],
    }
}
