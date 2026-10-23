import csv
import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.plan import GENERATOR, build_draft, version_hash
from app.plume import ppm_per_kg_h
from app.state import INCIDENTS

# Docker mounts ./data at /data; locally fall back to the repo's data/ folder.
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))

app = FastAPI(title="Atelic x Dell x NVIDIA — Methane Incident Intelligence")

# Frontend (nginx :8080) calls the API directly on :8000.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"])


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
    # A stored estimate is a model output awaiting engineer review; never "completed" on its own.
    stages[2]["status"] = "ready_for_review" if incident.get("quantification") else "not_started"
    # Draft exists -> in progress; submitted -> awaiting engineer review. Only Approve can go further.
    wo = incident.get("work_order")
    stages[3]["status"] = ("ready_for_review" if wo["submitted"] else "in_progress") if wo else "not_started"
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


# ----- Screen 4: Quantify -----
# Interim Gaussian plume inversion over the simulated scenario. The backend team will replace it
# with pyELQ (same response shape). Inputs are synthetic, so the result is a simulated estimate.

def read_rows(name):
    path = DATA_DIR / "scenario" / name
    if not path.is_file():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def sensor_series():
    series = {"methane_ppm": [], "pressure_bara": [], "wind_speed_m_s": [], "wind_dir_deg_from_north": []}
    sensor_of = {}
    for r in read_rows("sensors.csv"):
        series.setdefault(r["quantity"], []).append([r["timestamp_utc"], float(r["value"])])
        sensor_of[r["quantity"]] = r["sensor_id"]
    for r in read_rows("weather.csv"):
        for q in ("wind_speed_m_s", "wind_dir_deg_from_north"):
            series[q].append([r["timestamp_utc"], float(r[q])])
            sensor_of[q] = r["sensor_id"]
    return series, sensor_of


@app.get("/incidents/{incident_id}/sensor-series")
def get_sensor_series(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    series, sensor_of = sensor_series()
    return {"series": series, "sensor_of": sensor_of, "provenance": "synthetic",
            "source": ["scenario/sensors.csv", "scenario/weather.csv"], "site": read_json("scenario", "site.json")}


GAUSSIAN = "gaussian_plume"
MODEL_VERSION = "gaussian-plume-v0"
BACKGROUND_MIN = 10        # first minutes of replay assumed plume-free
ONSET_PPM = 1.0            # enhancement over background that opens the data window
OFF_AXIS = 0.2             # drop samples where the model gives < 20% of on-axis enhancement
MIN_SAMPLES = 5
FAILURE = "Insufficient concentration, wind or source geometry data to estimate the release rate reliably."


def gaussian_inputs():
    """What the Gaussian method needs, and which pieces are missing."""
    missing = []
    geo_path = DATA_DIR / "scenario" / "geometry.json"
    geo = json.loads(geo_path.read_text()) if geo_path.is_file() else None
    source = read_json("skid", "candidates.json").get("assessment", {}).get("asset_id")
    series, sensor_of = sensor_series()
    if not series["methane_ppm"]:
        missing.append("Methane concentration series (scenario/sensors.csv)")
    if not series["wind_speed_m_s"]:
        missing.append("Wind speed and direction series (scenario/weather.csv)")
    if not source:
        missing.append("Assumed source asset (no Locate assessment)")
    if not geo:
        missing.append("Source and detector geometry (scenario/geometry.json)")
    else:
        for a in filter(None, (source, sensor_of.get("methane_ppm"))):
            if a not in geo["assets"]:
                missing.append(f"Position and height for {a} (scenario/geometry.json)")
    return geo, source, series, sensor_of, missing


def methods():
    meta = read_json("ogi", "source_metadata.json")
    sp = meta.get("experimental_setpoint") or {}
    sp_missing = []
    if sp.get("rate") is None:
        sp_missing.append(f"Release setpoint for {meta.get('ogi_clip_id')} not on file")
    if meta.get("status") != "verified":
        sp_missing.append("OGI clip licence and metadata not verified")
    return [
        {"id": GAUSSIAN, "label": "Gaussian plume inversion", "note": "Interim, deterministic. Single point detector.",
         "missing": gaussian_inputs()[4]},
        {"id": "pyelq", "label": "pyELQ source inversion", "note": "Backend service", "missing": ["pyELQ service not connected"]},
        {"id": "experimental_setpoint", "label": "Controlled-release setpoint", "note": "GasVid/METEC known release",
         "missing": sp_missing},
    ]


def percentile(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_gaussian():
    geo, source, series, sensor_of, missing = gaussian_inputs()
    base = {"method": GAUSSIAN, "model_version": MODEL_VERSION, "model_note": "Interim, pyELQ pending", "provenance": "model_estimated",
            "input_provenance": "synthetic", "source_asset_id": source,
            "run_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if missing:
        return {**base, "rate_kg_ch4_h": None, "failure": FAILURE, "missing": missing}

    detector = sensor_of["methane_ppm"]
    src, rec, stab = geo["assets"][source], geo["assets"][detector], geo["stability_class"]
    wind = {t: (s, d) for (t, s), (_, d) in zip(series["wind_speed_m_s"], series["wind_dir_deg_from_north"])}
    ch4 = series["methane_ppm"]
    t0 = datetime.fromisoformat(ch4[0][0].replace("Z", "+00:00"))
    minutes = lambda t: (datetime.fromisoformat(t.replace("Z", "+00:00")) - t0).total_seconds() / 60
    bg = statistics.median(v for t, v in ch4 if minutes(t) < BACKGROUND_MIN)
    onset = next((t for t, v in ch4 if minutes(t) >= BACKGROUND_MIN and v - bg > ONSET_PPM), None)

    window = [(t, v) for t, v in ch4 if onset and t >= onset and t in wind]
    rates, off_axis, samples = [], 0, []
    for t, v in window:
        s, d = wind[t]
        unit = ppm_per_kg_h(src, rec, s, d, stab)
        axis = ppm_per_kg_h(src, rec, s, d_on_axis(src, rec), stab)
        if axis <= 0 or unit < OFF_AXIS * axis:
            off_axis += 1
            samples.append({"t": t, "used": False, "rate_kg_ch4_h": None})
            continue
        rates.append((v - bg) / unit)
        samples.append({"t": t, "used": True, "rate_kg_ch4_h": round(rates[-1], 3)})

    qc = {"background_ppm": round(bg, 2), "background_basis": f"Median, first {BACKGROUND_MIN} min",
          "samples_in_window": len(window), "samples_used": len(rates), "samples_off_axis": off_axis,
          "onset_rule": f"> {ONSET_PPM:g} ppm over background",
          "off_axis_rule": f"< {OFF_AXIS:.0%} of on-axis model value"}
    result = {**base, "detector_id": detector, "wind_sensor_id": sensor_of["wind_speed_m_s"],
              "data_window": {"start_utc": onset, "end_utc": window[-1][0] if window else None}, "qc": qc,
              "samples": samples,
              "assumptions": [
                  f"Source assumed at {source}, the top Locate candidate (unconfirmed hypothesis).",
                  f"Source and detector positions and heights from draft geometry ({geo['mapping_version']}).",
                  f"Steady-state Gaussian plume, ground reflection, stability class {stab} ({geo['stability_note']}).",
                  "Constant release rate over the data window; wind measured at one anemometer applies to the whole skid.",
                  "CH₄ ppm from mg/m³ at 25 °C, 1 atm.",
                  "Uncertainty interval reflects sample-to-sample scatter only; it excludes stability-class "
                  "and geometry error.",
                  "Sensor traces were generated with this same plume model, so agreement with the simulated "
                  "release checks the pipeline, not the method's real-world accuracy.",
              ]}
    if len(rates) < MIN_SAMPLES:
        return {**result, "rate_kg_ch4_h": None, "failure": FAILURE,
                "missing": [f"Only {len(rates)} usable concentration samples (need {MIN_SAMPLES})"]}
    return {**result, "rate_kg_ch4_h": round(statistics.median(rates), 2),
            "interval": {"low": round(percentile(rates, 0.1), 2), "high": round(percentile(rates, 0.9), 2),
                         "basis": f"P10–P90 of {len(rates)} per-sample estimates"}}


def d_on_axis(src, rec):
    """Wind-from bearing that puts rec on the plume centreline."""
    return (math.degrees(math.atan2(rec["x_m"] - src["x_m"], rec["y_m"] - src["y_m"])) + 180) % 360


def release_check():
    truth = read_json("scenario", "release_truth.json")
    return {"rate_kg_ch4_h": truth["rate_kg_ch4_h"], "source_asset_id": truth["source_asset_id"],
            "provenance": truth["provenance"]}


@app.get("/incidents/{incident_id}/quantification")
def get_quantification(incident_id: str):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    q = incident.get("quantification")
    return {"result": q, "methods": methods(), "release_check": release_check() if q else None}


@app.post("/incidents/{incident_id}/quantify")
def post_quantify(incident_id: str, body: dict = Body(default={})):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    method = body.get("method", GAUSSIAN)
    m = next((m for m in methods() if m["id"] == method), None)
    if m is None:
        raise HTTPException(status_code=400, detail="Unknown method")
    if method != GAUSSIAN or m["missing"]:
        raise HTTPException(status_code=409, detail={"failure": FAILURE, "missing": m["missing"]})
    incident["quantification"] = run_gaussian()
    return {"result": incident["quantification"], "methods": methods(), "release_check": release_check()}


# ----- Screen 5: Plan -----
# Rule-template draft (app/plan.py) until a Nemotron drafter is wired. Versions, flags and the
# submission live in memory. Nothing here can reach plant equipment.

EDITABLE = ("problem", "proposed_inspection", "priority", "required_permits")
OPERATOR = "Operator (not signed in)"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_draft(incident):
    det = {"metadata": read_json("ogi", "source_metadata.json"), "annotations": read_json("ogi", "plume_annotations.json")}
    docs = [(DATA_DIR / "docs" / n, f"docs/{n}") for n in ("maintenance_procedure.md", "isolation_checklist.md")]
    return build_draft(read_json("skid", "candidates.json"), incident.get("quantification"), det,
                       read_json("maintenance", "history.json"), [d for d in docs if d[0].is_file()])


def draft_inputs(incident):
    """What the drafter reads right now, so the page can show its real inputs before and after drafting."""
    cands = read_json("skid", "candidates.json")
    a = cands.get("assessment", {})
    q = incident.get("quantification")
    meta, ann = read_json("ogi", "source_metadata.json"), read_json("ogi", "plume_annotations.json")
    marked = bool(ann.get("ranges") or ann.get("frames")) and ann.get("ogi_clip_id") == meta.get("ogi_clip_id")
    docs = [n for n in ("maintenance_procedure.md", "isolation_checklist.md") if (DATA_DIR / "docs" / n).is_file()]
    records = read_json("maintenance", "history.json")["records"]
    rate = q and q.get("rate_kg_ch4_h")
    return [
        {"id": "source", "label": "Likely source", "value": a.get("asset_id") if a.get("result") == "likely_source" else None,
         "detail": f"Ranked first of {len(cands.get('candidates', []))} candidates, unconfirmed",
         "provenance": "hypothesis", "screen": "locate.html"},
        {"id": "rate", "label": "Emission rate", "value": f"{rate} kg CH₄/h" if rate is not None else None,
         "detail": (f"P10–P90 {q['interval']['low']}–{q['interval']['high']} · simulated inputs" if rate is not None
                    else "Not estimated yet"), "provenance": "model_estimated" if rate is not None else "pending",
         "screen": "quantify.html"},
        {"id": "detection", "label": "Camera observation", "value": meta.get("ogi_clip_id") if marked else None,
         "detail": ("Plume region marked, " + ("reviewed" if ann.get("reviewed_by") else "not yet reviewed")) if marked
                   else "No plume region marked", "provenance": "manual_annotation" if marked else "pending",
         "screen": "detection.html"},
        {"id": "docs", "label": "Procedures", "value": f"{len(docs)} documents",
         "detail": f"Illustrative procedure and checklist, plus {len(records)} maintenance records",
         "provenance": "synthetic", "screen": None},
    ]


def work_order_view(incident):
    wo = incident.get("work_order")
    q = incident.get("quantification")
    _, missing = make_draft(incident)
    view = {"generator": GENERATOR, "can_draft": not missing, "missing": missing, "inputs": draft_inputs(incident),
            "nvidia_inference": status()["nvidia_inference"]}
    if not wo:
        return {**view, "draft": None, "versions": [], "flags": {}, "submitted": None, "stale": False}
    latest = wo["versions"][-1]
    stale = latest["draft"]["basis"]["quantification_run_at"] != (q.get("run_at_utc") if q else None)
    return {**view, "draft": {**latest["draft"], "fields": latest["fields"], "version": latest["version"],
                              "hash": latest["hash"], "status": "Draft"},
            "versions": [{k: v[k] for k in ("version", "hash", "created_at", "author", "changed")} for v in wo["versions"]],
            "flags": wo["flags"], "submitted": wo["submitted"], "stale": stale}


def incident_or_404(incident_id):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def add_version(incident, draft, fields, author, changed):
    wo = incident.setdefault("work_order", {"versions": [], "flags": {}, "submitted": None})
    wo["versions"].append({"version": len(wo["versions"]) + 1, "draft": draft, "fields": fields, "author": author,
                           "changed": changed, "created_at": now_utc(), "hash": version_hash(fields, draft["sections"])})
    # A new version replaces what the reviewer would see, so any earlier submission no longer applies.
    wo["submitted"] = None


@app.get("/incidents/{incident_id}/work-order")
def get_work_order(incident_id: str):
    return work_order_view(incident_or_404(incident_id))


@app.post("/incidents/{incident_id}/work-order/draft")
def post_work_order_draft(incident_id: str):
    incident = incident_or_404(incident_id)
    draft, missing = make_draft(incident)
    if missing:
        raise HTTPException(status_code=409, detail={"missing": missing})
    fields = draft.pop("fields")
    old = incident.get("work_order")
    add_version(incident, draft, fields, f"{GENERATOR['id']} {GENERATOR['version']}", ["Generated"] if not old else ["Regenerated"])
    # Flags point at claims of the replaced text, so they go with it.
    incident["work_order"]["flags"] = {}
    return work_order_view(incident)


@app.post("/incidents/{incident_id}/work-order/edit")
def post_work_order_edit(incident_id: str, body: dict = Body(...)):
    incident = incident_or_404(incident_id)
    wo = incident.get("work_order")
    if not wo:
        raise HTTPException(status_code=409, detail="No draft to edit")
    latest = wo["versions"][-1]
    if body.get("base_version") != latest["version"]:
        raise HTTPException(status_code=409, detail=f"Draft changed since you opened it (now v{latest['version']}). Reload.")
    fields = dict(latest["fields"])
    for k, v in (body.get("fields") or {}).items():
        if k in EDITABLE:
            fields[k] = [s.strip() for s in v if s.strip()] if k == "required_permits" else (v.strip() or None if v else None)
    changed = [k for k in EDITABLE if fields[k] != latest["fields"][k]]
    if not changed:
        raise HTTPException(status_code=400, detail="No changes")
    add_version(incident, latest["draft"], fields, OPERATOR, changed)
    return work_order_view(incident)


@app.post("/incidents/{incident_id}/work-order/flag")
def post_work_order_flag(incident_id: str, body: dict = Body(...)):
    incident = incident_or_404(incident_id)
    wo = incident.get("work_order")
    if not wo:
        raise HTTPException(status_code=409, detail="No draft")
    cid = body.get("claim_id")
    ids = {c["id"] for cs in wo["versions"][-1]["draft"]["sections"].values() for c in cs}
    if cid not in ids:
        raise HTTPException(status_code=400, detail="Unknown claim")
    if body.get("flagged", True):
        wo["flags"][cid] = {"note": (body.get("note") or "").strip() or None, "by": OPERATOR, "at": now_utc()}
    else:
        wo["flags"].pop(cid, None)
    wo["submitted"] = None
    return work_order_view(incident)


@app.post("/incidents/{incident_id}/work-order/submit")
def post_work_order_submit(incident_id: str, body: dict = Body(...)):
    incident = incident_or_404(incident_id)
    wo = incident.get("work_order")
    if not wo:
        raise HTTPException(status_code=409, detail="No draft")
    latest = wo["versions"][-1]
    if body.get("version") != latest["version"]:
        raise HTTPException(status_code=409, detail=f"Not the latest draft (now v{latest['version']}). Reload.")
    if not latest["fields"]["priority"]:
        raise HTTPException(status_code=409, detail="Set a priority before submitting")
    # Submission only queues the draft for engineer review. It approves nothing and operates nothing.
    wo["submitted"] = {"version": latest["version"], "hash": latest["hash"], "at": now_utc(), "by": OPERATOR,
                       "open_flags": sorted(wo["flags"])}
    return work_order_view(incident)
