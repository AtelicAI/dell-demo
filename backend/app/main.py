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
    stages[1]["status"] = locate_status()
    return {**incident, "stages": stages}


def locate_status():
    # Candidates on file (sample ones count; the page labels them Simulated) -> ready for review.
    # Completed only with a named reviewer on real (non-sample) candidates.
    data = read_json("skid", "candidates.json")
    if not data.get("candidates"):
        return "not_started"
    if data.get("reviewed_by") and data.get("status") != "sample":
        return "completed"
    return "ready_for_review"


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


# ----- Screen 3: Locate -----
# JSON only for now; the backend developer will move these queries to Neo4j (same response shape).

def skid_graph():
    eq = read_json("skid", "equipment.json")
    rois = read_json("skid", "camera_rois.json")
    history = read_json("maintenance", "history.json")
    src, ver = eq["source"], eq["mapping_version"]

    nodes, edges = [], []
    for group in ("areas", "equipment", "sensors", "cameras"):
        for a in eq.get(group, []):
            nodes.append({**a, "source": src, "provenance": eq["provenance"], "mapping_version": ver})
    for r in history["records"]:
        nodes.append({"asset_id": r["record_id"], "display_name": r["summary"], "asset_type": "WorkOrder",
                      "date": r["date"], "work_type": r["type"], "work_status": r["status"],
                      "source": "maintenance/history.json", "provenance": history["provenance"], "mapping_version": None})

    def edge(kind, a, b, source, method, version=ver):
        edges.append({"type": kind, "from": a, "to": b, "source": source, "provenance": "synthetic",
                      "method": method, "mapping_version": version})

    for a in eq["equipment"]:
        edge("CONTAINS", eq["facility_id"], a["asset_id"], src, "manually configured")
    for p in eq["piping"]:
        # Direction is gas flow, so from = upstream.
        edge("CONNECTED_TO", p["from"], p["to"], f"{src} (piping)", "manually configured")
    for s in eq["sensors"]:
        edge("MONITORS", s["asset_id"], s["monitors"], src, "manually configured")
    for r in rois["regions"]:
        edge("CAN_OBSERVE", rois["camera_id"], r["asset_id"], "skid/camera_rois.json",
             rois["mapping_method"], rois["mapping_version"])
    for r in history["records"]:
        edge("HAS_WORK_ORDER", r["asset_id"], r["record_id"], "maintenance/history.json", "manually configured", None)
    return nodes, edges


@app.get("/incidents/{incident_id}/graph")
def get_graph(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    nodes, edges = skid_graph()
    # Full skid graph (~20 nodes); the UI starts from the selected asset's one-hop neighbourhood.
    return {
        "nodes": nodes,
        "edges": edges,
        "layout": read_json("skid", "layout.json"),
        "camera_rois": read_json("skid", "camera_rois.json"),
        "store": "json",
    }


@app.get("/incidents/{incident_id}/candidates")
def get_candidates(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    data = read_json("skid", "candidates.json")
    # PRD acceptance: all IDs valid. Report any that are not in the graph instead of hiding them.
    ids = {n["asset_id"] for n in skid_graph()[0]}
    cited = [data.get("assessment", {}).get("asset_id")]
    for c in data.get("candidates", []):
        cited.append(c["asset_id"])
        cited += [e["source_id"] for e in c.get("supporting", []) + c.get("contradicting", [])]
    data["invalid_ids"] = sorted({i for i in cited if i and i not in ids})
    return data
