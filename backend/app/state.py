"""In-memory incident state. Two live incidents (one per alerting camera) until persistence is added."""

STAGES = ["detect", "locate", "quantify", "plan", "approve", "report"]

# Data files each live incident reads, relative to DATA_DIR. A missing file is a real gap the UI shows.
FILES = {
    "INC-001": {
        "ogi_meta": "ogi/source_metadata.json", "ogi_ann": "ogi/plume_annotations.json",
        "frame_times": "ogi/frame_times.json", "clip": "ogi/source_clip.mp4",
        "clip_before": "ogi/before_clip.mp4", "clip_original": "ogi/source_clip_original.mp4",
        "rois": "skid/camera_rois.json", "candidates": "skid/candidates.json",
        "geometry": "scenario/geometry.json", "sensors": "scenario/sensors.csv",
        "release_truth": "scenario/release_truth.json",
    },
    "INC-002": {
        "ogi_meta": "incidents/INC-002/source_metadata.json", "ogi_ann": "incidents/INC-002/plume_annotations.json",
        "frame_times": "incidents/INC-002/frame_times.json", "clip": "incidents/INC-002/clip.mp4",
        # CAM-02's plume-free segment is its normal-operations view; no leak clip yet.
        "clip_before": "ogi/cam02_MOV_2571.mp4", "clip_original": "incidents/INC-002/clip_original.mp4",
        "rois": "skid/camera_rois_cam02.json", "candidates": "incidents/INC-002/candidates.json",
        "geometry": "incidents/INC-002/geometry.json", "sensors": "incidents/INC-002/sensors.csv",
        "release_truth": "incidents/INC-002/release_truth.json",
    },
}


def new_incident(incident_id, camera_id):
    return {
        "local_incident_id": incident_id,
        "site": "Gas-handling unit",
        "camera_id": camera_id,
        "source_status": "Source unconfirmed",
        "scenario": None,
        # Normal operations until the camera raises the alert (POST /incidents/:id/trigger); reset clears it.
        "triggered": False,
        "triggered_at": None,
        # Nothing has run yet, so every stage is honestly not started.
        "stages": [{"stage": s, "status": "not_started"} for s in STAGES],
    }


INCIDENTS = {
    "INC-001": new_incident("INC-001", "CAM-01"),
    "INC-002": new_incident("INC-002", "CAM-02"),
}
