import csv
import json
import math
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.plan import GENERATOR, build_draft, version_hash
from app.plume import ppm_per_kg_h
from app.report import build_report, to_markdown
from app.state import FILES, INCIDENTS

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
        # Incident replay starts here; the shell runs one clock from it for the pill and camera tiles.
        "replay_start_utc": read_json("scenario", "site.json").get("replay_start_utc"),
    }


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    stages = [dict(s) for s in incident["stages"]]
    if not incident["triggered"]:
        # Normal operations: no alert yet, so nothing in the workflow has started.
        return {**incident, "source_status": "No active alert", "stages": stages}
    stages[0]["status"] = detect_status(incident)
    stages[1]["status"] = locate_status(incident)
    # A stored estimate is a model output awaiting engineer review; never "completed" on its own.
    stages[2]["status"] = "ready_for_review" if incident.get("quantification") else "not_started"
    # Draft exists -> in progress; submitted -> awaiting engineer review; approved by a named
    # reviewer -> completed. Approve is blocked after a rejection or change request until resubmitted.
    wo = incident.get("work_order")
    rv = latest_review(wo)
    approved = bool(rv and rv["decision"] == "approve")
    if wo:
        stages[3]["status"] = "completed" if approved else "ready_for_review" if wo["submitted"] else "in_progress"
        stages[4]["status"] = ("completed" if approved else "ready_for_review" if wo["submitted"]
                               else "blocked" if wo["reviews"] else "not_started")
    # Report: a draft report exists once anything has run; completed only when a final (approved)
    # report with the current content has been exported.
    if incident.get("quantification") or wo:
        filed = approved and any(x["status"] == "final" and x["hash"] == make_report(incident)["report_hash"]
                                 for x in incident.get("report_exports", []))
        stages[5]["status"] = "completed" if filed else "in_progress"
    return {**incident, "stages": stages}


def locate_status(incident):
    # Candidates on file (sample ones count; the page labels them Simulated) -> ready for review.
    # Completed only with a named reviewer on real (non-sample) candidates.
    data = fjson(incident, "candidates")
    if not data.get("candidates"):
        return "not_started"
    if data.get("reviewed_by") and data.get("status") != "sample":
        return "completed"
    return "ready_for_review"


def detect_status(incident):
    # Detect is driven by the plume annotation for the current clip: a region exists -> ready for
    # review; a named reviewer -> completed. Never completed without a reviewer.
    meta = fjson(incident, "ogi_meta")
    ann = fjson(incident, "ogi_ann")
    has_region = bool(ann.get("frames") or ann.get("ranges"))
    if not has_region or not ann.get("ogi_clip_id") or ann["ogi_clip_id"] != meta.get("ogi_clip_id"):
        return "not_started"
    return "completed" if ann.get("reviewed_by") else "ready_for_review"


def require_triggered(incident):
    if not incident["triggered"]:
        raise HTTPException(status_code=409, detail=f"No active incident: {incident['camera_id']} has not raised an alert")


@app.post("/incidents/{incident_id}/trigger")
def post_trigger(incident_id: str):
    # The alert event: the incident's camera switches from the pre-leak view to the leak and the workflow opens.
    incident = incident_or_404(incident_id)
    if not incident["triggered"]:
        incident["triggered"] = True
        incident["triggered_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        audit(incident, "alert_triggered", "Presenter (Trigger event control)", f"{incident['camera_id']} alert raised; workflow opened")
        auto_run(incident)
    return get_incident(incident_id)


AUTO = "System (automatic on alert)"
AUTO_NOTE = "Started automatically when the alert was raised."


def auto_run(incident):
    """On the alert, run the steps that need no human decision: the emission estimate, then the work
    order draft. Each is the same real run as its button (span + audit). Priority, submission and
    approval stay with people; nothing here approves anything."""
    if not next(m for m in methods(incident) if m["id"] == GAUSSIAN)["missing"]:
        run_quantify(incident, AUTO, AUTO_NOTE)
    if not make_draft(incident)[1]:
        run_draft(incident, AUTO_NOTE)


@app.post("/incidents/{incident_id}/reset")
def post_reset(incident_id: str):
    # Back to normal operations for the next run: drop the alert and everything derived from it.
    incident = incident_or_404(incident_id)
    incident.update(triggered=False, triggered_at=None)
    for k in ("quantification", "work_order", "audit", "spans", "report_exports"):
        incident.pop(k, None)
    return get_incident(incident_id)


@app.get("/overview/plume")
def overview_plume():
    # Served as-is; the UI decides badges from "status" and "provenance".
    return json.loads((DATA_DIR / "carbon_mapper" / "verified_plume.json").read_text())


@app.get("/overview/plume/image")
def overview_plume_image():
    # Cached by scripts/fetch_carbon_mapper.py; the provider's own image URLs are signed and expire.
    path = DATA_DIR / "carbon_mapper" / "plume.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Plume image not cached")
    return Response(path.read_bytes(), media_type="image/png")


def read_json(*parts):
    return json.loads((DATA_DIR.joinpath(*parts)).read_text())


# Per-incident data files (app/state.py FILES), so each live incident reads its own scenario.
def frel(incident, key):
    return FILES[incident["local_incident_id"]][key]


def fpath(incident, key):
    return DATA_DIR / frel(incident, key)


def fjson(incident, key):
    return json.loads(fpath(incident, key).read_text())


@app.get("/incidents")
def list_incidents():
    # Live incidents (state from this backend) plus the closed history file (sample data, read-only).
    live = []
    for iid, inc in INCIDENTS.items():
        view = get_incident(iid)
        cands = fjson(inc, "candidates")
        a = cands.get("assessment", {})
        q, wo = inc.get("quantification"), inc.get("work_order")
        live.append({
            "incident_id": iid, "camera_id": inc["camera_id"], "triggered": inc["triggered"],
            "clip_available": fpath(inc, "clip").is_file(),
            "triggered_at": inc["triggered_at"], "stages": view["stages"],
            "asset_id": a.get("asset_id") if inc["triggered"] and a.get("result") == "likely_source" else None,
            "asset_provenance": "hypothesis",
            "rate_kg_ch4_h": q.get("rate_kg_ch4_h") if q else None,
            "review": latest_review(wo),
        })
    path = DATA_DIR / "incidents" / "history.json"
    history = json.loads(path.read_text()) if path.is_file() else None
    return {"live": live, "history": history}


@app.get("/audit")
def all_audit():
    # Every live incident's audit log in one list (for notifications), oldest first.
    events = [{**e, "incident_id": iid} for iid, inc in INCIDENTS.items() for e in inc.get("audit", [])]
    return {"events": sorted(events, key=lambda e: (e["at"], e["incident_id"], e["seq"]))}


@app.get("/incidents/{incident_id}/detections")
def get_detections(incident_id: str):
    incident = incident_or_404(incident_id)
    # Served as-is; the UI decides status and badges. clip_available is a real file check.
    return {
        "camera_id": incident["camera_id"],
        "metadata": fjson(incident, "ogi_meta"),
        "annotations": fjson(incident, "ogi_ann"),
        "triggered": incident["triggered"],
        "clip_available": fpath(incident, "clip").is_file(),
        # The camera before the leak (normal operations), played until the alert is triggered.
        "before_available": fpath(incident, "clip_before").is_file(),
        # Unprocessed infrared for the same frames, for the before/after slider.
        "original_available": fpath(incident, "clip_original").is_file(),
        # Per-frame start times from scripts/ogi_inspect.py; needed for exact frames on variable-rate clips.
        "frame_times": fjson(incident, "frame_times") if fpath(incident, "frame_times").is_file() else None,
        "camera_wall": read_json("skid", "camera_wall.json") if (DATA_DIR / "skid" / "camera_wall.json").is_file() else None,
    }


CHUNK = 2 * 1024 * 1024


@app.get("/incidents/{incident_id}/clip")
def incident_clip(incident_id: str, request: Request):
    return serve_mp4(fpath(incident_or_404(incident_id), "clip"), request)


@app.get("/incidents/{incident_id}/clip/before")
def incident_clip_before(incident_id: str, request: Request):
    return serve_mp4(fpath(incident_or_404(incident_id), "clip_before"), request)


@app.get("/incidents/{incident_id}/clip/original")
def incident_clip_original(incident_id: str, request: Request):
    return serve_mp4(fpath(incident_or_404(incident_id), "clip_original"), request)


@app.get("/ogi/cams/{camera_id}")
def ogi_cam_clip(camera_id: str, request: Request):
    # Looping clips for the other skid cameras; the file comes from skid/camera_wall.json only.
    wall = read_json("skid", "camera_wall.json")
    cam = next((c for c in wall["cameras"] if c["camera_id"] == camera_id), None)
    feed = cam and cam.get("feed")
    name = feed.get("file") if isinstance(feed, dict) else None
    if not name or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="No clip for this camera")
    return serve_mp4(DATA_DIR / "ogi" / name, request)


def serve_mp4(path, request):
    # Starlette 0.38 FileResponse has no Range support; the player needs it to seek and frame-step.
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
    roi_files = sorted({f["rois"] for f in FILES.values()})
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
    for rel in roi_files:
        rois = read_json(rel)
        for r in rois["regions"]:
            edge("CAN_OBSERVE", rois["camera_id"], r["asset_id"], rel, rois["mapping_method"], rois["mapping_version"])
    for r in history["records"]:
        edge("HAS_WORK_ORDER", r["asset_id"], r["record_id"], "maintenance/history.json", "manually configured", None)
    return nodes, edges


@app.get("/incidents/{incident_id}/graph")
def get_graph(incident_id: str):
    incident = incident_or_404(incident_id)
    nodes, edges = skid_graph()
    # Full skid graph (~20 nodes); the UI starts from the selected asset's one-hop neighbourhood.
    return {
        "nodes": nodes,
        "edges": edges,
        "layout": read_json("skid", "layout.json"),
        "camera_rois": fjson(incident, "rois"),
        "store": "json",
    }


@app.get("/incidents/{incident_id}/candidates")
def get_candidates(incident_id: str):
    data = fjson(incident_or_404(incident_id), "candidates")
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

WEATHER = "scenario/weather.csv"  # one anemometer for the whole site, shared by every incident


def read_rows(rel):
    path = DATA_DIR / rel
    if not path.is_file():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def sensor_series(incident):
    series = {"methane_ppm": [], "pressure_bara": [], "wind_speed_m_s": [], "wind_dir_deg_from_north": []}
    sensor_of = {}
    for r in read_rows(frel(incident, "sensors")):
        series.setdefault(r["quantity"], []).append([r["timestamp_utc"], float(r["value"])])
        sensor_of[r["quantity"]] = r["sensor_id"]
    for r in read_rows(WEATHER):
        for q in ("wind_speed_m_s", "wind_dir_deg_from_north"):
            series[q].append([r["timestamp_utc"], float(r[q])])
            sensor_of[q] = r["sensor_id"]
    return series, sensor_of


@app.get("/incidents/{incident_id}/sensor-series")
def get_sensor_series(incident_id: str):
    incident = incident_or_404(incident_id)
    series, sensor_of = sensor_series(incident)
    return {"series": series, "sensor_of": sensor_of, "provenance": "synthetic",
            "source": [frel(incident, "sensors"), WEATHER], "site": read_json("scenario", "site.json")}


GAUSSIAN = "gaussian_plume"
MODEL_VERSION = "gaussian-plume-v0"
BACKGROUND_MIN = 10        # first minutes of replay assumed plume-free
ONSET_PPM = 1.0            # enhancement over background that opens the data window
OFF_AXIS = 0.2             # drop samples where the model gives < 20% of on-axis enhancement
MIN_SAMPLES = 5
FAILURE = "Insufficient concentration, wind or source geometry data to estimate the release rate reliably."


def gaussian_inputs(incident):
    """What the Gaussian method needs, and which pieces are missing."""
    missing = []
    geo_rel = frel(incident, "geometry")
    geo = fjson(incident, "geometry") if fpath(incident, "geometry").is_file() else None
    source = fjson(incident, "candidates").get("assessment", {}).get("asset_id")
    series, sensor_of = sensor_series(incident)
    if not series["methane_ppm"]:
        missing.append(f"Methane concentration series ({frel(incident, 'sensors')})")
    if not series["wind_speed_m_s"]:
        missing.append(f"Wind speed and direction series ({WEATHER})")
    if not source:
        missing.append("Assumed source asset (no Locate assessment)")
    if not geo:
        missing.append(f"Source and detector geometry ({geo_rel})")
    else:
        for a in filter(None, (source, sensor_of.get("methane_ppm"))):
            if a not in geo["assets"]:
                missing.append(f"Position and height for {a} ({geo_rel})")
    return geo, source, series, sensor_of, missing


def methods(incident):
    meta = fjson(incident, "ogi_meta")
    sp = meta.get("experimental_setpoint") or {}
    sp_missing = []
    if sp.get("rate") is None:
        sp_missing.append(f"Release setpoint for the {incident['camera_id']} clip not on file")
    if meta.get("status") != "verified":
        sp_missing.append("OGI clip licence and metadata not verified")
    return [
        {"id": GAUSSIAN, "label": "Gaussian plume inversion", "note": "Interim, deterministic. Single point detector.",
         "missing": gaussian_inputs(incident)[4]},
        {"id": "pyelq", "label": "pyELQ source inversion", "note": "Backend service", "missing": ["pyELQ service not connected"]},
        {"id": "experimental_setpoint", "label": "Controlled-release setpoint", "note": "Known controlled-release rate",
         "missing": sp_missing},
    ]


def percentile(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_gaussian(incident):
    geo, source, series, sensor_of, missing = gaussian_inputs(incident)
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
                  "Constant release rate over the data window; wind measured at one anemometer applies to the whole site.",
                  "CH₄ ppm from mg/m³ at 25 °C, 1 atm.",
                  "Uncertainty interval reflects sample-to-sample scatter only; it excludes stability-class "
                  "and geometry error.",
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


def release_check(incident):
    truth = fjson(incident, "release_truth")
    return {"rate_kg_ch4_h": truth["rate_kg_ch4_h"], "source_asset_id": truth["source_asset_id"],
            "provenance": truth["provenance"]}


@app.get("/incidents/{incident_id}/quantification")
def get_quantification(incident_id: str):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    q = incident.get("quantification")
    return {"result": q, "methods": methods(incident), "release_check": release_check(incident) if q else None}


@app.post("/incidents/{incident_id}/quantify")
def post_quantify(incident_id: str, body: dict = Body(default={})):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    require_triggered(incident)
    method = body.get("method", GAUSSIAN)
    m = next((m for m in methods(incident) if m["id"] == method), None)
    if m is None:
        raise HTTPException(status_code=400, detail="Unknown method")
    if method != GAUSSIAN or m["missing"]:
        raise HTTPException(status_code=409, detail={"failure": FAILURE, "missing": m["missing"]})
    run_quantify(incident, OPERATOR)
    return {"result": incident["quantification"], "methods": methods(incident), "release_check": release_check(incident)}


def run_quantify(incident, actor, note=None):
    t0, at = time.perf_counter(), now_utc()
    incident["quantification"] = run_gaussian(incident)
    q = incident["quantification"]
    rate = q["rate_kg_ch4_h"]
    add_span(incident, "emission_estimate", at, elapsed_ms(t0), component="Emissions (Atelic backend)",
             tool="run_gaussian: Gaussian plume inversion", model=None, model_version=q["model_version"],
             evidence=[{"label": "Sensor and wind traces", "path": f"{frel(incident, 'sensors')}, {WEATHER}", "screen": "quantify.html"},
                       {"label": "Draft skid geometry", "path": frel(incident, "geometry"), "screen": "quantify.html"}],
             result=(f"{rate} kg CH₄/h, P10–P90 {q['interval']['low']}–{q['interval']['high']} "
                     f"({q['qc']['samples_used']} of {q['qc']['samples_in_window']} samples)") if rate is not None
                    else f"Not estimated: {'; '.join(q.get('missing') or [q.get('failure', '')])}",
             note=note, status="ok" if rate is not None else "blocked")
    audit(incident, "quantification_run", actor,
          f"{q['model_version']}: {rate} kg CH₄/h (model estimated)" if rate is not None else f"{q['model_version']}: not estimated")


# ----- Screen 5: Plan -----
# Rule-template draft (app/plan.py) until a Nemotron drafter is wired. Versions, flags and the
# submission live in memory. Nothing here can reach plant equipment.

EDITABLE = ("problem", "proposed_inspection", "priority", "required_permits")
OPERATOR = "Operator (not signed in)"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(incident, event, actor, detail, version=None, hash_=None):
    # Append-only: entries are never edited or removed (in memory until persistence is added).
    log = incident.setdefault("audit", [])
    log.append({"seq": len(log) + 1, "at": now_utc(), "event": event, "actor": actor, "detail": detail,
                "version": version, "hash": hash_})


def latest_review(wo):
    """The reviewer decision on the latest draft version, or None."""
    if not wo or not wo["reviews"]:
        return None
    rv = wo["reviews"][-1]
    return rv if rv["version"] == wo["versions"][-1]["version"] else None


def check_unlocked(wo):
    rv = latest_review(wo)
    if rv and rv["decision"] == "approve":
        raise HTTPException(status_code=409, detail=f"Approved by {rv['reviewer']}; the draft is locked.")


# Site procedures; the drafter picks the one that names the likely source.
DOCS = ("maintenance_procedure.md", "valve_procedure.md", "isolation_checklist.md")


def make_draft(incident):
    det = {"metadata": fjson(incident, "ogi_meta"), "annotations": fjson(incident, "ogi_ann")}
    docs = [(DATA_DIR / "docs" / n, f"docs/{n}") for n in DOCS]
    return build_draft(fjson(incident, "candidates"), incident.get("quantification"), det,
                       read_json("maintenance", "history.json"), [d for d in docs if d[0].is_file()],
                       incident["camera_id"], FILES[incident["local_incident_id"]])


def draft_inputs(incident):
    """What the drafter reads right now, so the page can show its real inputs before and after drafting."""
    cands = fjson(incident, "candidates")
    a = cands.get("assessment", {})
    q = incident.get("quantification")
    meta, ann = fjson(incident, "ogi_meta"), fjson(incident, "ogi_ann")
    marked = bool(ann.get("ranges") or ann.get("frames")) and ann.get("ogi_clip_id") == meta.get("ogi_clip_id")
    docs = [n for n in DOCS if (DATA_DIR / "docs" / n).is_file()]
    records = read_json("maintenance", "history.json")["records"]
    rate = q and q.get("rate_kg_ch4_h")
    return [
        {"id": "source", "label": "Likely source", "value": a.get("asset_id") if a.get("result") == "likely_source" else None,
         "detail": f"Ranked first of {len(cands.get('candidates', []))} candidates, unconfirmed",
         "provenance": "hypothesis", "screen": "locate.html"},
        {"id": "rate", "label": "Emission rate", "value": f"{rate} kg CH₄/h" if rate is not None else None,
         "detail": (f"P10–P90 {q['interval']['low']}–{q['interval']['high']}" if rate is not None
                    else "Not estimated yet"), "provenance": "model_estimated" if rate is not None else "pending",
         "screen": "quantify.html"},
        {"id": "detection", "label": "Camera observation", "value": incident["camera_id"] if marked else None,
         "detail": ("Plume region marked, " + ("reviewed" if ann.get("reviewed_by") else "not yet reviewed")) if marked
                   else "No plume region marked", "provenance": "manual_annotation" if marked else "pending",
         "screen": "detection.html"},
        {"id": "docs", "label": "Procedures", "value": f"{len(docs)} documents",
         "detail": f"Procedures and checklist, plus {len(records)} maintenance records",
         "provenance": "synthetic", "screen": None},
    ]


def work_order_view(incident):
    wo = incident.get("work_order")
    q = incident.get("quantification")
    _, missing = make_draft(incident)
    view = {"generator": GENERATOR, "can_draft": not missing, "missing": missing, "inputs": draft_inputs(incident),
            "nvidia_inference": status()["nvidia_inference"]}
    if not wo:
        return {**view, "draft": None, "versions": [], "flags": {}, "submitted": None, "stale": False,
                "review": None, "reviews": []}
    latest = wo["versions"][-1]
    stale = latest["draft"]["basis"]["quantification_run_at"] != (q.get("run_at_utc") if q else None)
    rv = latest_review(wo)
    return {**view, "draft": {**latest["draft"], "fields": latest["fields"], "version": latest["version"],
                              "hash": latest["hash"], "status": "Approved" if rv and rv["decision"] == "approve" else "Draft"},
            "versions": [{k: v[k] for k in ("version", "hash", "created_at", "author", "changed")} for v in wo["versions"]],
            "flags": wo["flags"], "submitted": wo["submitted"], "stale": stale, "review": rv, "reviews": wo["reviews"]}


def incident_or_404(incident_id):
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def add_version(incident, draft, fields, author, changed):
    wo = incident.setdefault("work_order", {"versions": [], "flags": {}, "submitted": None, "reviews": []})
    check_unlocked(wo)
    v = {"version": len(wo["versions"]) + 1, "draft": draft, "fields": fields, "author": author,
         "changed": changed, "created_at": now_utc(), "hash": version_hash(fields, draft["sections"])}
    wo["versions"].append(v)
    # A new version replaces what the reviewer would see, so any earlier submission no longer applies.
    wo["submitted"] = None
    audit(incident, "draft_version", author, f"v{v['version']}: {', '.join(changed)}", v["version"], v["hash"])


@app.get("/incidents/{incident_id}/work-order")
def get_work_order(incident_id: str):
    return work_order_view(incident_or_404(incident_id))


@app.post("/incidents/{incident_id}/work-order/draft")
def post_work_order_draft(incident_id: str):
    incident = incident_or_404(incident_id)
    require_triggered(incident)
    run_draft(incident)
    return work_order_view(incident)


def run_draft(incident, note=None):
    t0, at = time.perf_counter(), now_utc()
    draft, missing = make_draft(incident)
    ms = elapsed_ms(t0)
    tool = f"build_draft: fixed rule template {GENERATOR['version']}"
    if missing:
        add_span(incident, "work_order", at, ms, tool=tool, model=None, model_version=GENERATOR["version"],
                 result=f"Not drafted. Missing: {'; '.join(missing)}", status="blocked")
        raise HTTPException(status_code=409, detail={"missing": missing})
    procs = [e for e in draft["evidence"] if e["kind"] == "procedure"]
    add_span(incident, "procedure_passages", at, None,
             tool="parse_doc + asset-id keyword match (no NeMo Retriever, no embeddings)", model=None, model_version=None,
             evidence=[{"label": g, "path": next(e["uri"].split("#")[0] for e in procs if e["group"] == g), "screen": "plan.html"}
                       for g in dict.fromkeys(e["group"] for e in procs)],
             result=f"{len(procs)} passages cited from {len({e['group'] for e in procs})} documents",
             note=" ".join(filter(None, ["Runs inside the work order draft call; not timed separately.", note])))
    add_span(incident, "work_order", at, ms, tool=tool, model=None, model_version=GENERATOR["version"],
             evidence=[{"label": "Source candidates", "path": frel(incident, "candidates"), "screen": "locate.html"},
                       {"label": "Emission estimate", "path": "POST /incidents/{id}/quantify", "screen": "quantify.html"},
                       {"label": "Maintenance history", "path": "maintenance/history.json", "screen": None}],
             result=f"{sum(len(v) for v in draft['sections'].values())} claims, {len(draft['evidence'])} cited sources", note=note)
    fields = draft.pop("fields")
    old = incident.get("work_order")
    add_version(incident, draft, fields, f"{GENERATOR['id']} {GENERATOR['version']}", ["Generated"] if not old else ["Regenerated"])
    # Flags point at claims of the replaced text, so they go with it.
    incident["work_order"]["flags"] = {}


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
    check_unlocked(wo)
    cid = body.get("claim_id")
    ids = {c["id"] for cs in wo["versions"][-1]["draft"]["sections"].values() for c in cs}
    if cid not in ids:
        raise HTTPException(status_code=400, detail="Unknown claim")
    latest = wo["versions"][-1]
    if body.get("flagged", True):
        wo["flags"][cid] = {"note": (body.get("note") or "").strip() or None, "by": OPERATOR, "at": now_utc()}
        audit(incident, "claim_flagged", OPERATOR, f"{cid} flagged as unsupported", latest["version"], latest["hash"])
    elif wo["flags"].pop(cid, None):
        audit(incident, "claim_unflagged", OPERATOR, f"{cid} flag cleared", latest["version"], latest["hash"])
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
    check_unlocked(wo)
    rv = latest_review(wo)
    if rv:  # returned by the reviewer: the same text cannot go back unchanged
        raise HTTPException(status_code=409, detail=f"v{latest['version']} was returned by {rv['reviewer']}; "
                                                    "edit or regenerate it before resubmitting.")
    # Submission only queues the draft for engineer review. It approves nothing and operates nothing.
    wo["submitted"] = {"version": latest["version"], "hash": latest["hash"], "at": now_utc(), "by": OPERATOR,
                       "open_flags": sorted(wo["flags"])}
    audit(incident, "submitted_for_review", OPERATOR, f"v{latest['version']} submitted, {len(wo['flags'])} flagged claims",
          latest["version"], latest["hash"])
    return work_order_view(incident)


# ----- Screen 6: Approve -----
# A named engineer decides on the submitted version. Approval records a decision only; no endpoint
# here or anywhere else in this backend can operate plant equipment.

DECISIONS = {"approve": "Approved", "request_changes": "Changes requested", "reject": "Rejected"}


@app.post("/incidents/{incident_id}/review")
def post_review(incident_id: str, body: dict = Body(...)):
    incident = incident_or_404(incident_id)
    wo = incident.get("work_order")
    decision = body.get("decision")
    reviewer = (body.get("reviewer") or "").strip()[:80]
    reason = (body.get("reason") or "").strip() or None
    if decision not in DECISIONS:
        raise HTTPException(status_code=400, detail="Unknown decision")
    if not reviewer:
        raise HTTPException(status_code=400, detail="Enter the reviewer's name")
    if decision != "approve" and not reason:
        raise HTTPException(status_code=400, detail="A reason is required")
    rv = latest_review(wo)
    if rv:  # duplicate click or a second tab
        raise HTTPException(status_code=409, detail=f"Already decided: {DECISIONS[rv['decision']]} by {rv['reviewer']} at {rv['at']}.")
    sub = wo and wo["submitted"]
    if not sub:
        raise HTTPException(status_code=409, detail="No draft is waiting for review")
    if body.get("version") != sub["version"] or body.get("hash") != sub["hash"]:
        raise HTTPException(status_code=409, detail=f"The submitted draft changed (now v{sub['version']}). Reload.")
    q = incident.get("quantification")
    if decision == "approve" and wo["versions"][-1]["draft"]["basis"]["quantification_run_at"] != (q.get("run_at_utc") if q else None):
        raise HTTPException(status_code=409, detail="The emission estimate changed after this draft was made. Request changes instead.")
    rv = {"decision": decision, "version": sub["version"], "hash": sub["hash"], "reviewer": reviewer, "reason": reason,
          "at": now_utc(), "open_flags": sub["open_flags"]}
    wo["reviews"].append(rv)
    wo["submitted"] = None
    audit(incident, f"review_{decision}", reviewer, DECISIONS[decision] + (f": {reason}" if reason else ""),
          sub["version"], sub["hash"])
    return work_order_view(incident)


@app.get("/incidents/{incident_id}/audit")
def get_audit(incident_id: str):
    return {"events": incident_or_404(incident_id).get("audit", [])}


# ----- Screen 7: Report -----
# One snapshot (app/report.py) feeds the page and every export, so an export matches the UI.

def make_report(incident):
    src = {"ogi_meta": fjson(incident, "ogi_meta"), "ogi_ann": fjson(incident, "ogi_ann"),
           "candidates": fjson(incident, "candidates"), "site": read_json("scenario", "site.json"),
           "files": {**FILES[incident["local_incident_id"]], "weather": WEATHER},
           "plume": read_json("carbon_mapper", "verified_plume.json"),
           "review": latest_review(incident.get("work_order")), "generator": GENERATOR, "status": status()}
    return build_report(incident, src, now_utc())


@app.get("/incidents/{incident_id}/report")
def get_report(incident_id: str):
    incident = incident_or_404(incident_id)
    return {**make_report(incident), "exports": incident.get("report_exports", [])}


@app.post("/incidents/{incident_id}/report/export")
def post_report_export(incident_id: str, body: dict = Body(...)):
    incident = incident_or_404(incident_id)
    fmt = body.get("format")
    if fmt not in ("json", "md", "pdf"):
        raise HTTPException(status_code=400, detail="Unknown format")
    report = make_report(incident)
    # PDF is the browser's print of the same page; the backend only records that it was exported.
    incident.setdefault("report_exports", []).append({"format": fmt, "hash": report["report_hash"],
                                                      "status": report["status"], "at": report["generated_at"]})
    audit(incident, "report_exported", OPERATOR, f"{report['status'].title()} report exported as {fmt.upper()}, hash {report['report_hash']}")
    name = f"{incident_id}_report_{report['report_hash']}"
    if fmt == "json":
        return Response(json.dumps(report, indent=2, ensure_ascii=False), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if fmt == "md":
        return Response(to_markdown(report), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    return {"recorded": True, "hash": report["report_hash"]}


# ----- Screen 8: Glass box -----
# Spans are recorded only by code that actually ran (quantify, draft). Steps with no agent, model or
# service behind them are listed with the reason they did not run. Nothing here is decorative.

NO_GUARDRAIL = "Not evaluated: NeMo Guardrails not connected"
STEPS = [("camera_event", "Camera event received"), ("equipment_mapping", "Equipment mapping queried"),
         ("graph", "Graph relationships retrieved"), ("procedure_passages", "Procedure passages retrieved"),
         ("emission_estimate", "Emission estimate calculated"), ("work_order", "Work order drafted"),
         ("approval", "Human approval required")]


def elapsed_ms(t0):
    return round((time.perf_counter() - t0) * 1000, 1)


def execution_location():
    if status()["dell_edge"] == "running":
        return "Dell PowerEdge XR7620 (reported by the backend)"
    return f"Local backend process ({platform.system()}), not the Dell edge server"


def add_span(incident, step, started_at, duration_ms, tool, model, model_version, evidence=(), result="",
             component="Atelic backend", note=None, status="ok"):
    spans = incident.setdefault("spans", [])
    spans.append({"id": f"S{len(spans) + 1}", "step": step, "started_at": started_at, "duration_ms": duration_ms,
                  "component": component, "tool": tool, "model": model, "model_version": model_version,
                  "provider": "Atelic (local code)", "location": execution_location(), "evidence": list(evidence),
                  "result": result, "guardrail": NO_GUARDRAIL, "note": note, "status": status})


@app.get("/incidents/{incident_id}/agent-runs")
def get_agent_runs(incident_id: str):
    incident = incident_or_404(incident_id)
    spans = incident.get("spans", [])
    ann = fjson(incident, "ogi_ann")
    not_run = {
        "camera_event": "No camera event reached the backend in this run. The OGI clip is a replay"
                        + (f"; its plume region was drawn by hand ({frel(incident, 'ogi_ann')})." if ann.get("ranges") or ann.get("frames") else "."),
        "equipment_mapping": "No agent queried the camera-to-equipment mapping. The source candidates "
                             f"come from a manual ranking file ({frel(incident, 'candidates')}).",
        "graph": "No agent queried the knowledge graph. Neo4j is not connected; the Locate screen reads the site graph from JSON files.",
        "procedure_passages": "Not run yet. Passages are retrieved when a work order is drafted on Plan.",
        "emission_estimate": "Not run yet. Starts with Run estimate on Quantify.",
        "work_order": "Not run yet. Starts with Draft work order on Plan.",
    }
    wo = incident.get("work_order")
    rv = latest_review(wo)
    human = [e for e in incident.get("audit", []) if e["event"] == "submitted_for_review" or e["event"].startswith("review_")]
    approval = ("approved" if rv and rv["decision"] == "approve" else "returned" if rv
                else "waiting" if wo and wo["submitted"] else "not_reached")
    steps = []
    for key, label in STEPS:
        mine = [s for s in spans if s["step"] == key]
        if key == "camera_event" and incident.get("triggered"):
            steps.append({"id": key, "label": label, "status": "manual", "spans": [], "at": incident["triggered_at"],
                          "reason": f"Alert raised at {incident['triggered_at']} by the presenter's Trigger event control on "
                                    "the Camera screen (POST /trigger). No camera system or vision model sent it."})
            continue
        if key == "approval":
            steps.append({"id": key, "label": label, "status": approval, "spans": [], "human": human,
                          "reason": {"not_reached": "No draft has been submitted for review yet.",
                                     "waiting": "Waiting for a named engineer on the Approve screen."}.get(approval),
                          "guardrail": "Backend rule: a work order is approved only by a named reviewer; no auto-approval."})
        else:
            steps.append({"id": key, "label": label, "status": "ran" if mine else "not_run", "spans": mine,
                          "reason": None if mine else not_run[key]})
    st = status()
    return {
        "steps": steps,
        "span_count": len(spans),
        "hardware": {
            "dell_edge": st["dell_edge"], "location": execution_location(),
            "nvidia_inference": st["nvidia_inference"],
            "gpu": None,  # no GPU metrics are collected; never shown as a number
            "orchestrator": None, "retriever": None, "guardrails": None,
        },
        "safety": "No physical control commands are enabled.",
    }
