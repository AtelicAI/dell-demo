import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.state import INCIDENTS

# Docker mounts ./data at /data; locally fall back to the repo's data/ folder.
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))

app = FastAPI(title="Atelic x Dell x NVIDIA — Methane Incident Intelligence")

# Frontend (nginx :8080) calls the API directly on :8000.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    # Only report "running" when explicitly deployed on the Dell edge box.
    dell_edge = "running" if os.getenv("DELL_EDGE_DEPLOYED") == "1" else "target_deployment"
    return {
        "dell_edge": dell_edge,
        # No NIM client exists yet; switch to a real probe when it does.
        "nvidia_inference": "not_connected",
        # No replay clock yet.
        "replay_time_utc": None,
    }


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    stages = [dict(s) for s in incident["stages"]]
    stages[0]["status"] = detect_status()
    return {**incident, "stages": stages}


def detect_status():
    # Detect is driven by the plume annotation for the current clip: a region exists -> ready for
    # review; a named reviewer -> completed. Never completed without a reviewer.
    meta = read_json("ogi", "source_metadata.json")
    ann = read_json("ogi", "plume_annotations.json")
    has_region = bool(ann.get("frames") or ann.get("ranges"))
    if not has_region or not ann.get("ogi_clip_id") or ann["ogi_clip_id"] != meta.get("ogi_clip_id"):
        return "not_started"
    return "completed" if ann.get("reviewed_by") else "ready_for_review"


@app.get("/overview/plume")
def overview_plume():
    # Served as-is; the UI decides badges from "status" and "provenance".
    return json.loads((DATA_DIR / "carbon_mapper" / "verified_plume.json").read_text())


def read_json(*parts):
    return json.loads((DATA_DIR.joinpath(*parts)).read_text())


@app.get("/incidents/{incident_id}/detections")
def get_detections(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    # Served as-is; the UI decides status and badges. clip_available is a real file check.
    return {
        "metadata": read_json("ogi", "source_metadata.json"),
        "annotations": read_json("ogi", "plume_annotations.json"),
        "clip_available": (DATA_DIR / "ogi" / "source_clip.mp4").is_file(),
        # Per-frame start times from scripts/ogi_inspect.py; needed for exact frames on variable-rate clips.
        "frame_times": read_json("ogi", "frame_times.json") if (DATA_DIR / "ogi" / "frame_times.json").is_file() else None,
    }


CHUNK = 2 * 1024 * 1024


@app.get("/ogi/clip")
def ogi_clip(request: Request):
    # Starlette 0.38 FileResponse has no Range support; the player needs it to seek and frame-step.
    path = DATA_DIR / "ogi" / "source_clip.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip not available")
    size = path.stat().st_size
    start, end = 0, size - 1
    rng = request.headers.get("range", "")
    if rng.startswith("bytes="):
        a, _, b = rng[6:].split(",")[0].partition("-")
        if a:
            start, end = int(a), int(b) if b else size - 1
        else:
            start = size - int(b)
        # Cap each chunk so an open-ended "bytes=0-" doesn't load the whole clip.
        end = min(end, size - 1, start + CHUNK - 1)
        if start > end:
            raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    with path.open("rb") as f:
        f.seek(start)
        body = f.read(end - start + 1)
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(len(body))}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(body, status_code=206 if rng else 200, media_type="video/mp4", headers=headers)
