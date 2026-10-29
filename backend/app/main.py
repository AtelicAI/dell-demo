"""UI API for the Atelic x Dell x NVIDIA methane demo.

The screens keep their own response shapes; every value comes from the engine (engine/): the camera alert
from executed model inference on recorded footage, candidates and the plant graph from Neo4j, telemetry from
InfluxDB, the emission estimate from the engine's Gaussian candidate fit, findings and work-order drafts from
the Nemotron agents, and the durable workflow record in PostgreSQL. This service adds only what the screens
need on top: the incident slot the presenter triggers, typed reviewer names, operator edits/flags/priority
before submission, and the report export log. Nothing here can operate plant equipment.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Cookie, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import engine
from app.evidence import Evidence

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
STATE_FILE = Path(os.getenv("UI_STATE_FILE", DATA_DIR.parent / ".ui-state" / "state.json"))
DEPLOYMENT = os.getenv("DEPLOYMENT_LABEL", "Local backend process")

app = FastAPI(title="Atelic x Dell x NVIDIA — Methane Incident Intelligence (UI API)")
# Same-origin behind the web proxy (/api); CORS stays open only for local development on :8000.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"])

STAGES = ["detect", "locate", "quantify", "plan", "approve", "report", "close"]
OPERATOR = "Operator (not signed in)"
AGENT_STEPS = ("investigate", "reinvestigate", "plan", "revise", "answer", "verify")
SLOT_DEFAULTS = {"INC-001": {"camera_id": "CAM-01", "profile": "leak"},            # event 1: MOV_1237, SCN-001
                 "INC-002": {"camera_id": "CAM-02", "profile": "wind_outage"}}     # event 2: MOV_1239, SCN-003 (wind lost)
REPORTED = ("reported", "repair_recorded", "verification_ready", "closed", "follow_up_required")
APPROVED_OR_LATER = ("approved",) + REPORTED

# ----- sign-in: one shared demo login; the web proxy asks /auth/check before serving any page or API call -----

LOGIN_USER = os.getenv("LOGIN_USER", "atelic")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "")
SESSION_SECRET = (os.getenv("SESSION_SECRET") or secrets.token_hex(32)).encode()
SESSION_HOURS = 12
COOKIE = "atelic_session"


def session_token(user, now=None):
    exp = int((now or time.time()) + SESSION_HOURS * 3600)
    body = f"{user}|{exp}"
    return f"{body}|{hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).hexdigest()}"


def session_user(token):
    try:
        user, exp, sig = (token or "").split("|")
    except ValueError:
        return None
    good = hmac.new(SESSION_SECRET, f"{user}|{exp}".encode(), hashlib.sha256).hexdigest()
    return user if hmac.compare_digest(sig, good) and int(exp) > time.time() else None


@app.post("/login")
def login(request: Request, body: dict = Body(...)):
    user, password = str(body.get("username") or ""), str(body.get("password") or "")
    user_ok = hmac.compare_digest(user.encode(), LOGIN_USER.encode())
    password_ok = hmac.compare_digest(password.encode(), LOGIN_PASSWORD.encode())
    ok = bool(LOGIN_PASSWORD) and user_ok and password_ok
    if not ok:
        time.sleep(1)  # slow down guessing
        raise HTTPException(status_code=401, detail="Wrong username or password")
    res = JSONResponse({"user": user})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    res.set_cookie(COOKIE, session_token(user), max_age=SESSION_HOURS * 3600, httponly=True, secure=secure, samesite="lax", path="/")
    return res


@app.post("/logout")
def logout():
    res = JSONResponse({"signed_out": True})
    res.delete_cookie(COOKIE, path="/")
    return res


@app.get("/auth/check")
def auth_check(atelic_session: str | None = Cookie(None)):
    user = session_user(atelic_session)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    return Response(status_code=200, headers={"X-Atelic-User": user})


# ----- presenter slot state (small, persisted so a restart keeps the current run) -----

_lock = threading.RLock()


def _blank(incident_id):
    return {"incident_id": incident_id, **SLOT_DEFAULTS[incident_id], "engine_id": None, "triggered_at": None,
            "dismissal": None, "audit": [], "versions": [], "drafts": {}, "flags": {}, "submitted": None, "tool_runs": {},
            "reviews": [], "exports": [], "names": {}, "seen_events": 0, "graph_memory": None}


def _load():
    try:
        saved = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        saved = {}
    return {i: {**_blank(i), **saved.get(i, {})} for i in SLOT_DEFAULTS}


SLOTS = _load()


def save():
    with _lock:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(SLOTS))
        tmp.replace(STATE_FILE)


def slot_or_404(incident_id):
    s = SLOTS.get(incident_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return s


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(s, event, actor, detail, version=None, hash_=None, at=None):
    s["audit"].append({"seq": len(s["audit"]) + 1, "at": at or now_utc(), "event": event, "actor": actor,
                       "detail": detail, "version": version, "hash": hash_})


def read_json(*parts):
    return json.loads((DATA_DIR.joinpath(*parts)).read_text())


def utc(t):
    return t[:19] + "Z" if t else None


# ----- engine views (short cache: the record carries the frozen snapshot and is large) -----

_cache = {}


def cached(key, fn, ttl=1.5):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.monotonic(), value)
    return value


def orchestration(s):
    if not s["engine_id"]:
        return None
    return cached(("orch", s["engine_id"]), lambda: engine.try_get(f"/incidents/{s['engine_id']}/orchestration"))


def record(s):
    if not s["engine_id"]:
        return None
    return cached(("rec", s["engine_id"]), lambda: engine.try_get(f"/incidents/{s['engine_id']}/workflow"))


def forget(s):
    for k in [k for k in _cache if len(k) > 1 and k[1] == s["engine_id"]]:
        _cache.pop(k, None)


def done_steps(orch):
    return {e["step"] for e in (orch or {}).get("events", []) if e["kind"] == "completed"}


def step_event(orch, step, kind="completed"):
    evs = [e for e in (orch or {}).get("events", []) if e["step"] == step and e["kind"] == kind]
    return evs[-1] if evs else None


def state_of(s):
    rec = record(s)
    return rec["state"] if rec else None


def evidence_for(rec, names=None):
    ev = Evidence(rec, lambda aid: cached(("ctx", aid), lambda: engine.try_get(f"/assets/{aid}/context"), ttl=300))
    ev.names = names or {}
    return ev


def scenario_start():
    def fetch():
        inc = engine.try_get("/incidents/INC-001", {"as_of_seconds": 0})
        t = inc and inc.get("clock", {}).get("source_timestamp_utc")
        return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None
    return cached(("scenario-start",), fetch, ttl=3600)


def at_seconds(sec):
    start = scenario_start()
    return (start + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ") if start and sec is not None else None


# ----- status, incidents, stages -----

@app.get("/health")
def health():
    return {"status": "ok", "engine": engine.get("/health")}


def engine_health():
    return cached(("health",), lambda: engine.get("/health"), ttl=30)


def nvidia_status():
    return "hosted_api" if engine_health().get("nvidia") in ("configured_not_verified", "verified") else "not_connected"


@app.get("/status")
def status():
    h = engine_health()
    mon = cached(("monitor",), lambda: engine.get("/monitor", {"profile": "leak"}), ttl=300)
    return {
        "dell_edge": "running" if os.getenv("DELL_EDGE_DEPLOYED") == "1" else "target_deployment",
        "nvidia_inference": nvidia_status(),
        "replay_time_utc": None,
        "replay_start_utc": at_seconds(mon["scenario_seconds_at_clip_start"]),
        "deployment": DEPLOYMENT,
        "services": {k: h.get(k) for k in ("graph", "historian", "historian_status", "documents", "workflow", "nvidia")},
    }


def latest_review(s):
    if not s["reviews"] or not s["versions"]:
        return None
    rv = s["reviews"][-1]
    return rv if rv["version"] == s["versions"][-1]["version"] else None


def stages_for(s):
    stages = [{"stage": x, "status": "not_started"} for x in STAGES]
    st = {x["stage"]: x for x in stages}
    if not s["engine_id"]:
        return stages
    if s["dismissal"]:
        for x in stages:
            x["status"] = "not_needed"
        st["detect"]["status"] = "completed"
        st["report"]["status"] = "completed" if any(e["status"] == "final" for e in s["exports"]) else "in_progress"
        return stages
    orch, state = orchestration(s), state_of(s)
    done, cur, status_ = done_steps(orch), (orch or {}).get("current_step"), (orch or {}).get("status")
    failed = status_ in ("failed", "interrupted")
    st["detect"]["status"] = "completed" if "detect" in done else "in_progress"
    st["locate"]["status"] = ("completed" if "locate" in done else "blocked" if failed and cur == "locate"
                              else "in_progress" if "detect" in done else "not_started")
    st["quantify"]["status"] = ("completed" if "quantify" in done else "blocked" if failed and cur == "quantify"
                                else "in_progress" if "locate" in done else "not_started")
    rv = latest_review(s)
    if state in APPROVED_OR_LATER:
        st["plan"]["status"] = st["approve"]["status"] = "completed"
        st["report"]["status"] = "completed" if state in REPORTED else "in_progress"
        st["close"]["status"] = "completed" if state == "closed" else "in_progress" if state in REPORTED else "not_started"
    elif state == "rejected":
        st["plan"]["status"], st["approve"]["status"] = "completed", "blocked"
    elif "quantify" in done:
        busy = status_ == "running" and cur in ("freeze", "investigate", "reinvestigate", "plan", "revise", "submit")
        st["plan"]["status"] = ("blocked" if failed else "ready_for_review" if s["submitted"] and not busy else "in_progress")
        st["approve"]["status"] = ("ready_for_review" if s["submitted"] else
                                   "blocked" if rv and rv["decision"] != "approve" else "not_started")
        st["report"]["status"] = "in_progress"
    return stages


def view(s):
    sync(s)
    orch = orchestration(s)
    rec = record(s)
    rate = quant_result(s)
    return {
        "local_incident_id": s["incident_id"], "incident_id": s["incident_id"], "site": "Gas-handling unit",
        "camera_id": s["camera_id"], "triggered": bool(s["engine_id"]), "triggered_at": s["triggered_at"],
        "dismissal": s["dismissal"],
        "source_status": ("No active alert" if not s["engine_id"] else "False alarm (dismissed)" if s["dismissal"]
                          else "Source unconfirmed"),
        "scenario": {"leak": "SCN-001", "wind_outage": "SCN-003"}.get(s["profile"], s["profile"]),
        "stages": stages_for(s),
        "engine": {"incident_id": s["engine_id"], "status": (orch or {}).get("status"),
                   "current_step": (orch or {}).get("current_step"), "state": rec and rec["state"],
                   "workflow_version": rec and rec["version"]},
        "rate_kg_ch4_h": rate and rate.get("rate_kg_ch4_h"),
    }


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    return view(slot_or_404(incident_id))


@app.get("/incidents")
def list_incidents():
    live = []
    for iid, s in SLOTS.items():
        v = view(s)
        q = quant_result(s)
        live.append({"incident_id": iid, "camera_id": s["camera_id"], "triggered": v["triggered"],
                     "clip_available": True, "triggered_at": s["triggered_at"], "stages": v["stages"],
                     "asset_id": selected_asset(s), "asset_provenance": "hypothesis",
                     "rate_kg_ch4_h": q and q.get("rate_kg_ch4_h"), "review": latest_review(s),
                     "dismissal": s["dismissal"], "engine_incident_id": s["engine_id"],
                     "frame_anchor": EVENTS[iid]["anchor"]})
    return {"live": live, "history": incident_history()}


def incident_history():
    """Closed incidents remembered in the knowledge graph (IncidentRecord nodes written at closure)."""
    plant = cached(("plant",), lambda: engine.get("/graph/plant"), ttl=10)
    rows = []
    for n in plant["nodes"]:
        if n["label"] != "IncidentRecord":
            continue
        p = n["props"]
        opened, closed = utc(p.get("occurred_at_utc")), utc(p.get("closed_at_utc"))
        rows.append({
            "incident_id": p["id"], "camera_id": "CAM-01", "asset_id": p.get("selected_asset_id"),
            "opened_utc": opened or closed or "", "closed_utc": closed,
            "outcome": "repaired", "outcome_label": "Repaired and verified",
            "summary": p.get("summary") or p.get("repair") or "Closed incident written to the knowledge graph at closure.",
            "decision": {"decision": "approve", "reviewer": "Engineer (authenticated)", "role": None, "reason": None},
            "work_order": None,
            "rate": f"{p['rate_kg_h']:.2f} kg CH₄/h (model estimate)" if isinstance(p.get("rate_kg_h"), (int, float)) else None,
            "timeline": [x for x in ({"at_utc": opened, "event": "Camera alert raised"},
                                     {"at_utc": closed, "event": "Closed by an engineer; written to graph memory"}) if x["at_utc"]],
        })
    return {"status": "verified", "provenance": "simulated", "incidents": sorted(rows, key=lambda r: r["opened_utc"], reverse=True)}


@app.get("/audit")
def all_audit():
    for s in SLOTS.values():
        sync(s)
    events = [{**e, "incident_id": iid} for iid, s in SLOTS.items() for e in s["audit"]]
    return {"events": sorted(events, key=lambda e: (e["at"], e["incident_id"], e["seq"]))}


# ----- sync: mirror engine progress into the slot (drafts, audit) -----

SYNC_EVENTS = {("detect", "completed"): ("agent_detect", "Atelic engine"), ("quantify", "completed"): ("quantification_run", "Atelic engine"),
               ("investigate", "completed"): ("agent_investigation", "Nemotron agent"), ("answer", "completed"): ("agent_answer", "Nemotron agent"),
               ("verify", "completed"): ("agent_verification", "Nemotron agent"),
               ("reinvestigate", "completed"): ("agent_reinvestigation", "Nemotron agent")}


def sync(s):
    if not s["engine_id"]:
        return
    with _lock:
        orch = orchestration(s)
        rec = record(s)
        changed = False
        events = (orch or {}).get("events", [])
        for e in events[s["seen_events"]:]:
            changed = True
            hit = SYNC_EVENTS.get((e["step"], e["kind"]))
            if not hit:
                continue
            d = e["detail"]
            text = {"detect": "Camera model alert registered with the engine",
                    "quantify": (f"{d['rate_kg_h']:.3f} kg CH₄/h at replay {d.get('as_of_seconds')} s (model estimated)" if d.get("rate_kg_h") is not None
                                 else f"Rate unavailable: {d.get('reason')}"),
                    "investigate": "Investigation agent reported findings",
                    "answer": "Agent answered an engineer question",
                    "reinvestigate": f"Investigation agent finished research round {d.get('round')}",
                    "verify": f"Verifier recommends {d.get('recommendation')} ({d.get('recovery_check')})"}[e["step"]]
            audit(s, hit[0], hit[1], text, at=utc(e["recorded_at"]))
        s["seen_events"] = len(events)
        if rec and rec.get("work_order"):
            wo = rec["work_order"]
            dv = wo["version"]
            if str(dv) not in s["drafts"]:
                changed = True
                s["drafts"][str(dv)] = build_draft(rec, wo, evidence_for(rec, s["names"]))
                fields = default_fields(rec, wo)
                prev = s["versions"][-1] if s["versions"] else None
                if prev and prev["fields"].get("priority"):
                    fields["priority"] = prev["fields"]["priority"]
                rev = wo["plan"].get("revision_of") or {}
                add_version(s, dv, fields, generator(rec)["author"],
                            [f"Revised by the planner after research round {rev['research_round']}"] if rev.get("research_round")
                            else ["Revised by the planner after review"] if rev else ["Generated"])
                s["flags"] = {}
        if changed:
            save()


def add_version(s, draft_version, fields, author, changed):
    draft = s["drafts"][str(draft_version)]
    v = {"version": len(s["versions"]) + 1, "draft_version": draft_version, "fields": fields, "author": author,
         "changed": changed, "created_at": now_utc(),
         "hash": hashlib.sha256(json.dumps([fields, draft["sections"]], sort_keys=True).encode()).hexdigest()[:12]}
    s["versions"].append(v)
    s["submitted"] = None
    audit(s, "draft_version", author, f"v{v['version']}: {', '.join(changed)}", v["version"], v["hash"])
    return v


# ----- presenter controls -----

@app.post("/incidents/{incident_id}/trigger")
def post_trigger(incident_id: str):
    """Register the camera alert with the engine; its orchestrator then runs Locate → Quantify → Investigate →
    Plan → Submit and stops for the engineer. The alert comes from executed model inference on the recorded
    footage (clip 194 s); this button only decides when the presenter starts the incident."""
    s = slot_or_404(incident_id)
    with _lock:
        if not s["engine_id"]:
            # One incident at a time: raising this camera resets any other raised incident.
            for other_id, o in list(SLOTS.items()):
                if other_id != incident_id and o["engine_id"]:
                    forget(o)
                    SLOTS[other_id] = _blank(other_id)
            res = engine.post("/monitor/incidents", {"profile": s["profile"], "plan_mode": "model",
                                                     "plan_fallback": os.getenv("PLAN_FALLBACK", "deterministic")}, human=True)
            s.update(engine_id=res["incident_id"], triggered_at=now_utc())
            ev = res["event"]
            audit(s, "alert_triggered", f"{s['camera_id']} model alert (OWLv2 + SAM2, 3 of 5 samples)",
                  f"{s['camera_id']} alert raised; engine incident {res['incident_id']} opened. Model alert on recorded "
                  f"GasVid footage at clip {ev.get('clip_time_seconds') or 0:.1f} s; agents started")
            save()
    return view(s)


@app.post("/incidents/{incident_id}/reset")
def post_reset(incident_id: str):
    """Back to normal operations. The engine's durable record of the previous run stays; the next trigger
    opens a new engine incident."""
    s = slot_or_404(incident_id)
    with _lock:
        forget(s)
        SLOTS[incident_id] = _blank(incident_id)
        save()
    return view(SLOTS[incident_id])


DISMISS_REASONS = {"steam": "Steam or water vapour", "exhaust": "Hot exhaust or engine heat", "reflection": "Reflection or sun glint",
                   "planned": "Planned venting or maintenance", "other": "Other"}


@app.post("/incidents/{incident_id}/dismiss")
def post_dismiss(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    if not s["engine_id"]:
        raise HTTPException(status_code=409, detail=f"No active incident: {s['camera_id']} has not raised an alert")
    if s["dismissal"]:
        raise HTTPException(status_code=409, detail=f"Already closed by {s['dismissal']['by']}")
    if state_of(s) in APPROVED_OR_LATER:
        raise HTTPException(status_code=409, detail="A work order is approved for this incident; it cannot be dismissed as a false alarm.")
    by, code, comment = (body.get("by") or "").strip(), body.get("reason"), (body.get("comment") or "").strip()
    if not by:
        raise HTTPException(status_code=400, detail="Enter your name")
    if code not in DISMISS_REASONS:
        raise HTTPException(status_code=400, detail="Choose a reason")
    if not comment:
        raise HTTPException(status_code=400, detail="Say what you saw")
    s["dismissal"] = {"by": by, "reason": code, "reason_label": DISMISS_REASONS[code], "comment": comment, "at": now_utc()}
    s["submitted"] = None
    audit(s, "incident_dismissed", by, f"Closed as a false alarm: {DISMISS_REASONS[code]}. {comment}")
    save()
    return view(s)


@app.post("/incidents/{incident_id}/reopen")
def post_reopen(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    by = (body.get("by") or "").strip()
    if not s["dismissal"]:
        raise HTTPException(status_code=409, detail="This incident is not closed")
    if not by:
        raise HTTPException(status_code=400, detail="Enter your name")
    d, s["dismissal"] = s["dismissal"], None
    audit(s, "incident_reopened", by, f"Reopened after it was closed as a false alarm by {d['by']}")
    save()
    return view(s)


# ----- Overview (Carbon Mapper satellite context, unchanged) -----

@app.get("/overview/plume")
def overview_plume():
    return read_json("carbon_mapper", "verified_plume.json")


@app.get("/overview/plume/image")
def overview_plume_image():
    path = DATA_DIR / "carbon_mapper" / "plume.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Plume image not cached")
    return Response(path.read_bytes(), media_type="image/png")


# ----- Camera -----

# Each event is one continuous recording: the Atelic infrared-visibility render (the plume as the model captured
# it, frame by frame) with the untouched original of the same frames for the slider. It plays from the start of
# the monitored window; the alarm latches from the executed detector samples (3 of the last 5 positive).
EVENTS = {
    "INC-001": {"atelic": "ogi/inc001_mov1237_atelic.mp4", "original": "ogi/inc001_mov1237_original.mp4",
                "quiet": "ogi/inc001_mov1237_quiet.mp4", "anchor": "top",
                "view": "Close view, 6.9 m from the release"},
    "INC-002": {"atelic": "ogi/inc002_mov1239_atelic.mp4", "original": "ogi/inc002_mov1239_original.mp4",
                "quiet": "ogi/inc002_mov1239_quiet.mp4", "anchor": "bottom",
                "view": "Wide view, 12.6 m from the release. A separate release from INC-001, recorded at a different time"},
}
# "anchor": which edge of the frame stays in view when a wide tile crops the 4:3 footage. MOV_1237's plume is near
# the top; MOV_1239 is filmed from farther back, so its stack and equipment sit low and a top crop cuts them off.
# "quiet": 60 s of the same camera's original infrared from the class-0 interval (5.3 g/h, below camera detection)
# with no positive detector sample (MOV_1237 40-100 s, MOV_1239 20-80 s). The wall loops it until that incident's
# alarm, so a camera never shows a detection before it has one.
FPS = 15.0


def footage(profile):
    return cached(("footage", profile), lambda: engine.get("/monitor/footage", {"profile": profile}), ttl=3600)


def event_timeline(profile):
    """Executed detector samples over the monitored window, the alarm moment (as the engine computes it) and the
    metered release segments, all in seconds from the start of the event clip."""
    def build():
        mon = engine.get("/monitor", {"profile": profile})
        det = engine.get(f"/videos/{mon['clip_id']}/detections", {"run_id": mon["run_id"]})
        t0, t1 = float(mon["clip_start_seconds"]), float(mon["clip_end_seconds"])
        preds = sorted((p for p in det["predictions"] if p.get("pts_seconds") is not None), key=lambda p: p["pts_seconds"])
        alert = (engine.get("/monitor/status", {"profile": profile, "clip_seconds": t1}).get("alert") or {})
        alert_t = alert["clip_seconds"] - t0 if alert.get("clip_seconds") is not None else None
        box = lambda b: [round(v, 4) for v in (b[0], b[1], b[2] - b[0], b[3] - b[1])]
        # Regions for the plume panel after the alarm: each executed sample's box until the next sample.
        ranges = []
        positives = [p for p in preds if p.get("prediction") and alert_t is not None and p["pts_seconds"] - t0 >= alert_t - 1e-6]
        for p, nxt in zip(positives, positives[1:] + [None]):
            start = int(round((p["pts_seconds"] - t0) * FPS))
            end = int(round(((nxt["pts_seconds"] if nxt else t1) - t0) * FPS)) - 1
            b = (p.get("normalized_boxes") or [None])[0]
            ranges.append({"start_frame": start, "end_frame": max(start, end), "bbox_normalized": box(b) if b else None,
                           "confidence": None, "source_seconds": round(p["pts_seconds"], 3)})
        segs = [{"start": round(x["start_seconds"] - t0, 3), "end": round(x["end_seconds"] - t0, 3),
                 "rate_g_h": x.get("metered_rate_g_h"), "label": x.get("label")} for x in footage(profile).get("segments", [])]
        monitor = {"clip_start_seconds": t0, "clip_end_seconds": t1, "alert_t": round(alert_t, 3) if alert_t is not None else None,
                   "samples": [{"t": round(p["pts_seconds"] - t0, 3), "positive": bool(p.get("prediction"))} for p in preds],
                   "policy": mon["alert_policy"], "segments": segs,
                   "note": "Executed detector samples on the recorded footage; the alarm latches when 3 of the last 5 are positive"}
        return {"mon": mon, "ranges": ranges, "monitor": monitor}
    return cached(("timeline", profile), build, ttl=3600)


@app.get("/incidents/{incident_id}/detections")
def get_detections(incident_id: str):
    s = slot_or_404(incident_id)
    a = event_timeline(s["profile"])
    mon, f = a["mon"], footage(s["profile"])
    model = mon["model"]
    m = a["monitor"]
    at = (m["alert_t"] or 0) + m["clip_start_seconds"]
    seg = next((x for x in f.get("segments", []) if x["start_seconds"] <= at < x["end_seconds"]), {})
    meta = {
        "status": "verified", "ogi_clip_id": mon["clip_id"], "origin": "METEC (GasVid)",
        "source_url": "https://purl.stanford.edu/gn716hm8958",
        "recording_date": f.get("recorded_date"), "camera_type": "FLIR GF-320",
        "imaging_distance_m": f.get("imaging_distance_m"),
        "resolution": "640x480 (Atelic view, 2x) · 320x240 original", "frame_rate": FPS, "variable_frame_rate": False,
        "duration_s": m["clip_end_seconds"] - m["clip_start_seconds"], "license_status": "verified", "provenance": "model_estimated",
        "plume_rendering": "burned_in",
        "source_segment": {"start_seconds": m["clip_start_seconds"], "end_seconds": m["clip_end_seconds"]},
        "rendering": "Atelic infrared plume visibility: SAM2 video tracking x reference-relative image evidence; not concentration",
        "detection_mode": "recorded_model_output",
        "model_version": f"{model['method']} (owlv2 + {model['id'].split('/')[-1]})" if model.get("id") else model.get("method"),
        "clip_window_seconds": [m["clip_start_seconds"], m["clip_end_seconds"]], "alert_policy": mon["alert_policy"],
        "metered_release": {"rate_g_h": seg.get("metered_rate_g_h"), "label": seg.get("label"), "class": seg.get("class"),
                            "basis": "GasVid metered release class for this footage (Wang et al. 2020, Table 1)"},
        "mapping_basis": mon["mapping_basis"],
        "frame_anchor": EVENTS[incident_id]["anchor"], "view_note": EVENTS[incident_id]["view"],
    }
    rec = record(s)
    ev = rec["snapshot"]["events"][0] if rec and rec["snapshot"].get("events") else None
    return {
        "camera_id": s["camera_id"], "metadata": meta,
        "annotations": {"status": "verified", "provenance": "model_estimated", "ogi_clip_id": mon["clip_id"],
                        "reviewed_by": None, "frames": [], "ranges": a["ranges"], "run_id": mon["run_id"],
                        "note": "Executed detector samples from the alarm on, each held until the next sample"},
        "triggered": bool(s["engine_id"]), "dismissal": s["dismissal"], "clip_available": True,
        "before_available": True, "original_available": True, "frame_times": None,
        "camera_wall": read_json("skid", "camera_wall.json"), "monitor": m,
        "alert": ev and {"clip_time_seconds": ev.get("clip_time_seconds"), "timestamp_utc": ev.get("timestamp_utc"),
                         "reference": (ev.get("provenance") or {}).get("reference")},
    }


CHUNK = 2 * 1024 * 1024


def serve_mp4(path, request):
    # Starlette's FileResponse has no Range support in this version; the player needs it to seek.
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
        end = min(end, size - 1, start + CHUNK - 1)
        if start > end:
            raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    with path.open("rb") as fh:
        fh.seek(start)
        body = fh.read(end - start + 1)
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(len(body)), "Cache-Control": "public, max-age=3600"}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(body, status_code=206 if rng else 200, media_type="video/mp4", headers=headers)


@app.get("/incidents/{incident_id}/clip")
def incident_clip(incident_id: str, request: Request):
    slot_or_404(incident_id)
    return serve_mp4(DATA_DIR / EVENTS[incident_id]["atelic"], request)


@app.get("/incidents/{incident_id}/clip/before")
def incident_clip_before(incident_id: str, request: Request):
    slot_or_404(incident_id)
    return serve_mp4(DATA_DIR / EVENTS[incident_id]["quiet"], request)


@app.get("/incidents/{incident_id}/clip/original")
def incident_clip_original(incident_id: str, request: Request):
    slot_or_404(incident_id)
    return serve_mp4(DATA_DIR / EVENTS[incident_id]["original"], request)


@app.get("/ogi/cams/{camera_id}")
def ogi_cam_clip(camera_id: str, request: Request):
    wall = read_json("skid", "camera_wall.json")
    cam = next((c for c in wall["cameras"] if c["camera_id"] == camera_id), None)
    feed = cam and cam.get("feed")
    name = feed.get("file") if isinstance(feed, dict) else None
    if not name or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="No clip for this camera")
    return serve_mp4(DATA_DIR / "ogi" / name, request)


# ----- Locate + knowledge graph (Neo4j through the engine) -----

ASSET_TYPE = {"separator": "Vessel", "compressor": "Compressor", "valve": "Valve", "flange": "Flange", "pipe": "PipeSegment",
              "vent": "Vent", "cooler": "Vessel"}
EDGE_TYPE = {"FLOWS_TO": "CONNECTED_TO", "ILLUSTRATES": "CAN_OBSERVE"}


def plant_graph():
    def build():
        g = engine.get("/graph/plant")
        fac = cached(("facility",), lambda: engine.get("/facility"), ttl=3600)
        ver = fac.get("geometry_version")
        src = "Neo4j (engine knowledge graph)"
        nodes, edges, areas = [], [], {}
        for n in g["nodes"]:
            p, label = n["props"], n["label"]
            base = {"asset_id": p["id"], "source": src, "provenance": "synthetic", "mapping_version": ver, "label": label}
            if label == "Asset":
                nodes.append({**base, "display_name": p.get("name"), "asset_type": ASSET_TYPE.get(p.get("kind"), "Vessel"),
                              "kind": p.get("kind"), "area": p.get("area"), "position_m": [p.get("x_m"), p.get("y_m"), p.get("z_m")]})
                if p.get("area"):
                    areas.setdefault(p["area"], []).append(p["id"])
            elif label == "Sensor":
                nodes.append({**base, "display_name": f"{(p.get('variable') or '').replace('_', ' ').capitalize()} sensor", "asset_type": "Sensor",
                              "measures": f"{p.get('variable')} ({p.get('unit')})"})
            elif label == "Camera":
                nodes.append({**base, "display_name": "OGI camera", "asset_type": "Camera"})
            elif label == "WeatherStation":
                nodes.append({**base, "display_name": "Weather station", "asset_type": "Sensor", "measures": "wind speed + direction"})
            elif label == "WorkOrder":
                nodes.append({**base, "display_name": p.get("issue") or p["id"], "asset_type": "WorkOrder", "date": p.get("date"),
                              "work_type": p.get("work_type"), "work_status": p.get("status"), "finding": p.get("finding"),
                              "action": p.get("action"), "follow_up": p.get("follow_up")})
            elif label == "FieldReport":
                nodes.append({**base, "display_name": p.get("title"), "asset_type": "FieldReport", "date": p.get("date"),
                              "report_type": p.get("report_type"), "author_role": p.get("author_role"), "excerpt": (p.get("body") or "")[:400]})
            elif label == "Commitment":
                nodes.append({**base, "display_name": p.get("text"), "asset_type": "Commitment", "work_status": p.get("status")})
            elif label == "Document":
                nodes.append({**base, "display_name": p.get("title"), "asset_type": "Document", "doc_kind": p.get("kind")})
            elif label == "IncidentRecord":
                nodes.append({**base, "display_name": f"Closed incident {p['id']}", "asset_type": "IncidentRecord",
                              "date": (p.get("closed_at_utc") or "")[:10]})
        for area, ids in areas.items():
            aid = "AREA-" + "".join(w[0] for w in area.split()).upper()
            nodes.append({"asset_id": aid, "display_name": area, "asset_type": "EquipmentArea", "source": src,
                          "provenance": "synthetic", "mapping_version": ver, "label": "Area"})
            edges += [{"type": "CONTAINS", "from": aid, "to": i, "source": src, "provenance": "synthetic", "method": "manually configured",
                       "mapping_version": ver} for i in ids]
        for e in g["edges"]:
            t, a, b = e["type"], e["from_id"], e["to_id"]
            if t == "ON_ASSET" and a.startswith("WO-"):
                t, a, b = "HAS_WORK_ORDER", b, a
            else:
                t = EDGE_TYPE.get(t, t)
            edges.append({"type": t, "from": a, "to": b, "source": src, "provenance": "synthetic",
                          "relationship_basis": (e.get("props") or {}).get("provenance"),
                          "method": "manual_illustrative" if t == "CAN_OBSERVE" else "graph relationship", "mapping_version": ver})
        positions = schematic(fac)
        cams = fac.get("cameras", [])
        rois = {"camera_id": cams[0]["id"] if cams else "CAM-01", "clip_id": "MOV_1242", "mapping_method": "manual_illustrative",
                "mapping_version": ver,
                "regions": [{"asset_id": r["asset_id"], "polygon_normalized": r["polygon"], "camera_id": c["id"]} for c in cams for r in c.get("regions", [])]}
        return {"nodes": nodes, "edges": edges, "layout": {"positions": positions, "mapping_version": ver},
                "camera_rois": rois, "store": "neo4j"}
    return cached(("graph-ui",), build, ttl=10)


def schematic(fac):
    """P&ID-style layout (0-100 %): the main gas line left to right, vent branches above their tee and the vent header
    above them, methane detectors on top at their physical position along the line, cameras below."""
    con = fac["connections"]
    main = [c for c in con if c["line_id"] == "L-201"]
    start = next(c["from_id"] for c in main if not any(d["to_id"] == c["from_id"] for d in main))
    order = [start]
    while True:
        nxt = [c["to_id"] for c in main if c["from_id"] == order[-1]]
        if not nxt:
            break
        order.append(nxt[0])
    step = 86 / max(1, len(order) - 1)
    pos = {a: {"x": 7 + i * step, "y": 72} for i, a in enumerate(order)}
    vent = [c for c in con if c["line_id"] != "L-201"]
    header = [a for a in {c["to_id"] for c in vent} if sum(c["to_id"] == a for c in vent) > 1]
    pending = [c for c in vent if c["from_id"] in pos and c["to_id"] not in header]
    while pending:
        c = pending.pop(0)
        if c["to_id"] in pos:
            continue
        p = pos[c["from_id"]]
        pos[c["to_id"]] = {"x": p["x"] if p["y"] == 72 else p["x"] + step, "y": 46}
        pending += [d for d in vent if d["from_id"] == c["to_id"] and d["to_id"] not in header]
    for h in header:
        feeders = [pos[c["from_id"]]["x"] for c in vent if c["to_id"] == h and c["from_id"] in pos]
        nearest = min(feeders, key=lambda x: abs(x - 50)) if feeders else 50
        pos[h] = {"x": nearest + step / 2, "y": 24}
    # Methane detectors: physical x mapped through the main line's physical spacing, kept apart.
    phys = {a["id"]: a["position"][0] for a in fac["assets"]}
    xs = [(phys[a], pos[a]["x"]) for a in order]
    def along(x):
        if x <= xs[0][0]:
            return xs[0][1]
        for (x0, p0), (x1, p1) in zip(xs, xs[1:]):
            if x <= x1:
                return p0 + (p1 - p0) * (x - x0) / ((x1 - x0) or 1)
        return min(97, xs[-1][1] + (x - xs[-1][0]) * step / 3)
    placed = []
    for sid, x in sorted(((s["id"], s["position"][0]) for s in fac["sensors"] if s["variable"] == "methane"), key=lambda v: v[1]):
        px = along(x)
        while any(abs(px - q) < 7 for q in placed):
            px += 7
        placed.append(px)
        pos[sid] = {"x": px, "y": 7}
    # Keep detectors on the page and at least 7 % apart, packing back from the right edge.
    row = sorted((v["x"], k) for k, v in pos.items() if v["y"] == 7)
    limit = 96
    for x, k in reversed(row):
        pos[k]["x"] = min(x, limit)
        limit = pos[k]["x"] - 7
    for i, c in enumerate(fac.get("cameras", [])):
        target = c["regions"][0]["asset_id"] if c.get("regions") else order[len(order) // 2]
        pos[c["id"]] = {"x": pos.get(target, {"x": 50})["x"] + (i - 0.5) * 12, "y": 94}
    return pos


@app.get("/incidents/{incident_id}/graph")
def get_graph(incident_id: str):
    s = slot_or_404(incident_id)
    g = plant_graph()
    # Locate shows what this incident's camera can see.
    regions = [r for r in g["camera_rois"]["regions"] if r.get("camera_id", s["camera_id"]) == s["camera_id"]]
    return {**g, "camera_rois": {**g["camera_rois"], "camera_id": s["camera_id"], "regions": regions}}


@app.get("/graph/summary")
def graph_summary():
    return engine.get("/graph/summary")


def selected_asset(s):
    rec = record(s)
    if rec:
        return rec["snapshot"].get("candidate_asset_id")
    e = step_event(orchestration(s), "locate")
    return e and e["detail"].get("selected_asset_id")


def subject(f, ids):
    """The candidate a finding is about: the first one its statement names (else the first one its evidence names)."""
    text = f["statement"]
    named = sorted((text.index(a), a) for a in ids if a in text)
    if named:
        return named[0][1]
    return next((a for a in ids for i in f.get("evidence_ids", []) if a in i), None)


@app.get("/incidents/{incident_id}/candidates")
def get_candidates(incident_id: str):
    s = slot_or_404(incident_id)
    empty = {"assessment": {"result": "unresolved"}, "candidates": [], "invalid_ids": []}
    if not s["engine_id"]:
        return {**empty, "status": "sample", "method": "Not run: no alert"}
    rec = record(s)
    if not rec:
        loc = step_event(orchestration(s), "locate")
        if not loc:
            return {**empty, "status": "pending", "method": "Locate agent running"}
        return {"status": "verified", "provenance": "model_estimated", "method": "Camera origin-region overlap (illustrative mapping)",
                "assessment": {"result": "likely_source", "asset_id": loc["detail"]["selected_asset_id"]}, "invalid_ids": [],
                "candidates": [{"asset_id": c["asset_id"], "status": "candidate",
                                "rationale": [f"Camera origin region overlaps {c['asset_id']} by {c['overlap_fraction']:.0%} (illustrative mapping, not a probability)."],
                                "supporting": [], "contradicting": [], "missing": ["Emission fit (Quantify agent running)", "Investigation agent findings"]}
                               for c in loc["detail"]["candidates"]]}
    snap = rec["snapshot"]
    q = snap.get("quantification") or {}
    fits = {f["candidate_asset_id"]: f for f in q.get("candidate_fits", [])}
    inv = ((rec.get("investigation") or {}).get("findings") or {}).get("content") or {}
    assess = {a["asset_id"]: a for a in inv.get("candidate_assessment", [])}
    ev = evidence_for(rec, s["names"])
    sensors = sorted({x["sensor_id"] for x in snap.get("measurements", []) if x["sensor_id"].startswith("CH4")}) or ["CH4-01"]
    out = []
    for c in snap["candidates"]:
        aid, fit, sup, con = c["asset_id"], fits.get(c["asset_id"], {}), [], []
        sup.append({"id": f"{aid}-cam", "source_id": "CAM-01", "provenance": "model_estimated",
                    "label": f"CAM-01 plume origin region overlaps {aid} by {c['overlap_fraction']:.0%} (illustrative mapping, not a probability)"})
        if fit.get("fit_status") in ("best_fit", "comparable_fit"):
            sup.append({"id": f"{aid}-fit", "source_id": sensors[0], "provenance": "model_estimated",
                        "label": f"Methane sensors {sensors[0]}–{sensors[-1]} fit a source at {aid}: {fit['fit_status'].replace('_', ' ')}"
                                 + (f", {fit['rate_kg_h']:.2f} kg/h" if fit.get("rate_kg_h") is not None else "")})
        elif fit.get("fit_status") == "poor_fit":
            con.append({"id": f"{aid}-fit", "source_id": sensors[0], "provenance": "model_estimated",
                        "label": f"A source at {aid} fits the sensors {fit.get('error_ratio_to_best', '?')}x worse than the best location"})
        for f in inv.get("findings", []):
            if subject(f, [x["asset_id"] for x in snap["candidates"]]) != aid:
                continue
            item = {"id": f"{aid}-f{len(sup) + len(con)}", "source_id": ev.source_id(f["evidence_ids"][0]) if f.get("evidence_ids") else aid,
                    "label": f"{'Contradiction' if f['kind'] == 'contradiction' else 'Gap' if f['kind'] == 'gap' else 'Finding'}: {f['statement']}",
                    "provenance": "agent_drafted"}
            (con if f["kind"] in ("contradiction", "gap") else sup).append(item)
        a = assess.get(aid)
        out.append({"asset_id": aid, "status": "candidate", "rationale": ([a["assessment"]] if a else []) + ([c["why"]] if c.get("why") else []),
                    "supporting": sup, "contradicting": con,
                    "missing": ["Field confirmation by an operator", "Calibrated camera-to-equipment mapping"]})
    # Graph nodes behind the story: everything the agents cited, plus the sensors used for the estimate.
    cited = [i for f in inv.get("findings", []) for i in f.get("evidence_ids", [])]
    cited += [i for a_ in inv.get("candidate_assessment", []) for i in a_.get("evidence_ids", [])]
    if s["versions"]:
        cited += [e["id"] for e in s["drafts"][str(s["versions"][-1]["draft_version"])]["evidence"]]
    related = sorted({ev.source_id(i) for i in cited} | set(sensors) | {"WO-" + i.split("WO-")[1][:3] for i in cited if "commitment:WO-" in i})
    return {"status": "verified", "provenance": "model_estimated", "related_ids": related,
            "method": "Camera overlap + Gaussian candidate fit + Nemotron investigation",
            "assessment": {"result": "likely_source", "asset_id": snap["candidate_asset_id"]},
            "candidates": out, "invalid_ids": [], "attribution": q.get("telemetry_attribution"),
            "investigation_summary": inv.get("summary"), "confirmed": False}


# ----- Quantify -----

def quant_result(s):
    """The frozen estimate, or the last attempt while the agent waits for a usable window."""
    if not s["engine_id"]:
        return None
    rec, orch = record(s), orchestration(s)
    q = rec["snapshot"].get("quantification") if rec else None
    done = step_event(orch, "quantify")
    if not q and not done:
        return None
    src = q or done["detail"]
    tool = (q or {}).get("primary_tool") or src.get("primary_tool") or "gaussian_candidate_fit"
    camera = next((t["output"] for t in (q or {}).get("tools", []) if t["id"] == "qogi" and t.get("output")), None)
    attempts = [e["detail"] for e in (orch or {}).get("events", []) if e["step"] == "quantify" and e["kind"] == "attempt"]
    base = {"method": tool, "primary_tool": tool,
            "model_version": f"{(q or {}).get('method', 'nonnegative-gaussian-candidate-fit')} v{(q or {}).get('version', '1.0')}",
            "model_note": TOOL_NOTE.get(tool, ""),
            "provenance": "model_estimated", "input_provenance": "synthetic",
            "source_asset_id": (q or {}).get("candidate_asset_id") or selected_asset(s),
            "run_at_utc": utc(done["recorded_at"]) if done else now_utc(),
            "attribution": src.get("telemetry_attribution"), "candidate_fits": (q or {}).get("candidate_fits", []),
            "tools": (q or {}).get("tools", []), "case": (q or {}).get("case"), "cross_checks": (q or {}).get("cross_checks") or [],
            "camera": camera,
            "attempts": [{"t": at_seconds(a["as_of_seconds"]), "as_of_seconds": a["as_of_seconds"], "status": a["status"],
                          "reason": a.get("reason"), "rate_kg_ch4_h": a.get("rate_kg_h"), "attribution": a.get("attribution"),
                          "tool": a.get("primary_tool"), "text": attempt_text(a)} for a in attempts]}
    rate = src.get("rate_kg_h")
    if rate is None:
        reason = src.get("reason")
        return {**base, "rate_kg_ch4_h": None, "failure": "Emission rate unavailable: no tool had a usable window.",
                "missing": [RATE_REASON.get(reason, (reason or "No usable window").replace("_", " "))]}
    as_of = ((q or {}).get("clock") or {}).get("scenario_seconds") or src.get("as_of_seconds")
    sens = src.get("sensitivity_kg_h") or {}
    samples = [{"t": a["t"], "used": a["status"] == "estimated", "rate_kg_ch4_h": a["rate_kg_ch4_h"]} for a in base["attempts"] if a["t"] and a["rate_kg_ch4_h"] is not None]
    n_inputs = len((q or {}).get("input_ids", []))
    basis = ("frame-to-frame spread over the camera window (25th–75th percentile), not a confidence interval" if tool == "qogi" else
             f"assumption sensitivity (wind speed ±{sens.get('wind_speed_fraction', .2):.0%}, direction ±{sens.get('wind_direction_degrees', 10)}°), not a confidence interval")
    steady = (q or {}).get("steady_state_check")
    if tool == "qogi" and camera:
        window = {"start_utc": at_seconds(as_of - 90), "end_utc": at_seconds(as_of)}
        qc_rows = [("Frames used", f"{camera['frames_used']} of {camera['frames_in_window']} in the 90 s window"),
                   ("Thermal contrast", f"{abs(camera['thermal_contrast_k']):.1f} K (background {camera['background_apparent_c']} °C, air {camera['atmospheric_c']} °C)"),
                   ("Plume area · speed", f"{camera['median_plume_area_m2']} m² · {camera['median_plume_speed_m_s']} m/s (median)"),
                   ("Distance · pixel", f"{camera['distance_m']} m · {camera['pixel_size_mm']} mm per pixel"),
                   ("Calibration", camera["reference_release"])]
    else:
        window = {"start_utc": at_seconds(as_of - 60), "end_utc": at_seconds(as_of)} if as_of else None
        qc_rows = [("Background", f"{round((q or {}).get('background_ppm') or 0, 2)} ppm, fitted with the rate"),
                   ("Readings used", f"{n_inputs} in the 60 s window"),
                   ("Fit residual", f"{(q or {}).get('residual_rms_ppm') or 0:.3f} ppm RMS"),
                   ("Window rule", "Agent re-ran every 10 s of replay until the plume was steady and the location comparison decisive")]
    if steady:
        qc_rows.append(("Steady plume", f"early {steady['early_half_rate_kg_h']:.3f} vs late {steady['late_half_rate_kg_h']:.3f} kg/h (within 30%)"))
    return {**base, "rate_kg_ch4_h": round(rate, 3), "as_of_seconds": as_of,
            "interval": {"low": round(sens["min"], 2), "high": round(sens["max"], 2), "basis": basis} if sens else None,
            "data_window": window, "assumptions": (q or {}).get("assumptions", []), "samples": samples,
            "qc": {"rows": qc_rows, "samples_used": camera["frames_used"] if tool == "qogi" and camera else n_inputs,
                   "steady_state": steady}}


TOOL_NOTE = {"gaussian_candidate_fit": "Plume model: one Gaussian fit per candidate location over every methane sensor and the measured wind",
             "bls": "Particle model through the measured turbulence and stability",
             "gaussian_puff": "Puff model: puffs carried by each second's wind, fitted over every methane sensor",
             "mdlq": "MDLQ method: puffs + spike-and-slab Bayesian over every visible source, leak probability for each",
             "cfd": "Flow solver: airflow around the equipment, methane transport from each candidate, rate fit",
             "qogi": "Camera: plume mass from the radiometric frame × plume speed from the footage; no anemometer"}
RATE_REASON = {"missing_wind_or_methane": "Weather mast down and no camera plume to measure",
               "waiting_for_camera_window": "Weather mast down; the camera plume never held steady within the wait limit",
               "non_steady_plume": "Plume never became steady within the wait limit"}


TOOL_SHORT = {"gaussian_candidate_fit": "Gaussian plume", "qogi": "Camera (QOGI)", "bls": "bLs", "gaussian_puff": "Gaussian puff",
              "mdlq": "MDLQ", "cfd": "CFD"}


def attempt_text(a):
    """One line per agent attempt for the activity log."""
    t, r = a.get("primary_tool"), a.get("reason")
    label = TOOL_SHORT.get(t, t or "Gaussian plume")
    mast_down = (a.get("tool_roles") or {}).get("gaussian_candidate_fit") == "blocked"
    if a["status"] == "estimated":
        extra = "".join(f"; {TOOL_SHORT.get(c['tool'], c['tool'])} {c['rate_kg_h']:.3f} kg/h ({c['ratio']:.2f}×, {'agrees' if c['agrees'] else 'disagrees'})"
                        for c in a.get("cross_checks") or [])
        att = a.get("attribution")
        decided = "" if att in (None, "agrees_single", "not_assessed") else f", candidates {att.replace('_', ' ')}: keep going"
        decided = decided.replace("candidates", "sensors: candidates")
        return f"{label}: {a['rate_kg_h']:.3f} kg/h{extra}{decided}"
    if r == "waiting_for_camera_window":
        return (f"Weather mast not reporting; {label} waiting for 90 s of steady plume footage" if mast_down
                else f"{label} tracking the plume; waiting for 90 s of steady footage")
    return f"{label}: {(r or a['status']).replace('_', ' ')}"


def reveal(s):
    if not s["engine_id"]:
        return None
    return cached(("reveal", s["engine_id"]), lambda: engine.try_get(f"/incidents/{s['engine_id']}/reveal"), ttl=5)


def release_check(s):
    key = reveal(s)
    if not key:
        return None
    rate = None
    for k in ("metered_rate_kg_h",):
        rate = rate or key.get(k) or (key.get("footage") or {}).get(k)
    if rate is None:
        g = key.get("metered_rate_g_h") or (key.get("footage") or {}).get("metered_rate_g_h")
        rate = g / 1000 if g else None
    return {"rate_kg_ch4_h": round(rate, 4) if rate else None, "source_asset_id": "XV-2104", "provenance": "experimental_setpoint",
            "error_percent": key.get("error_percent"), "answer_key": key}


def quant_methods(s):
    """Every rate tool with the agent's decision for this incident (or the catalog before it has run)."""
    q = quant_result(s)
    if q and q["tools"]:
        runs = s.get("tool_runs") or {}
        return [{**t, "engineer_run": runs.get(t["id"])} for t in q["tools"]]
    catalog = quantification_methods()["methods"]
    last = (q or {}).get("attempts") or []
    roles = {}
    if s["engine_id"]:
        att = [e["detail"] for e in (orchestration(s) or {}).get("events", []) if e["step"] == "quantify" and e["kind"] == "attempt"]
        roles = (att[-1].get("tool_roles") or {}) if att else {}
    return [{"id": m["id"], "label": m["label"], "connected": m["connected"], "role": roles.get(m["id"], "pending"),
             "reason": "Agent is reading the case" if last or roles else ("Waiting for the quantify agent" if s["engine_id"] else "No alert yet"),
             "output": None} for m in catalog]


def conditions(s, q):
    """When the rate can be trusted: the chosen tool's conditions, the sensors' view and the cross-checks, from the frozen window."""
    if not q:
        return None
    case = q.get("case") or {}
    camera_first = q.get("primary_tool") == "qogi"
    rows = []
    if camera_first:
        cam = q.get("camera") or {}
        steady = cam.get("steady_state_check") or {}
        rows += [
            {"id": "tracked", "label": "Plume tracked by the camera", "ok": cam.get("frames_used", 0) >= .5 * (cam.get("frames_in_window") or 1),
             "detail": f"{cam.get('frames_used', 0)} of {cam.get('frames_in_window', 0)} frames carry the detector's plume mask"},
            {"id": "contrast", "label": "Thermal contrast", "ok": abs(cam.get("thermal_contrast_k") or 0) >= 2,
             "detail": f"{abs(cam.get('thermal_contrast_k') or 0):.1f} K between the background and the air (needs at least 2 K)"},
            {"id": "steady", "label": "Camera plume steady", "ok": cam.get("status") == "estimated",
             "detail": (f"Early and late halves agree: {steady['early_half_rate_kg_h']:.3f} vs {steady['late_half_rate_kg_h']:.3f} kg/h" if steady else "Not assessed")},
            {"id": "geometry", "label": "Distance to the source", "ok": bool(cam.get("distance_m")),
             "detail": f"{cam.get('distance_m', '—')} m from {cam.get('camera_id', 'the camera')} to {q.get('source_asset_id')} (camera geometry)"},
        ]
    if case.get("wind") != "available":
        rows.append({"id": "mast", "label": "Weather mast", "ok": False,
                     "detail": "Not reporting: wind and sonic readings missing, so the sensor models cannot check the camera"})
        return {"rows": rows, "all_ok": all(r["ok"] for r in rows if r["id"] != "mast"),
                "note": "With the mast down the agent quantifies from the camera alone; the sensor checks return when the mast does"}
    w = q.get("data_window") or {}
    fits = q.get("candidate_fits") or []
    detected = max([f.get("detected_locations") or 0 for f in fits] or [0])
    att = q.get("attribution") or {}
    end = w.get("end_utc")
    local = f"{(int(end[11:13]) + 3) % 24:02d}:{end[14:16]}" if end else None  # Saudi Arabia, UTC+3
    sonic = case.get("sonic") or {}
    spread = case.get("wind_direction_spread_deg")
    rows += [
        {"id": "wind", "label": "Steady wind", "ok": bool(case.get("wind_steady")),
         "detail": f"{case.get('wind_speed_m_s')} m/s, direction spread {spread}° (needs at least 1 m/s, at most 30°)"},
        {"id": "sensors", "label": "Sensors downwind with signal", "ok": detected >= 2,
         "detail": f"{detected} methane sensors above their detection limit" if fits else "Not assessed"},
        {"id": "stability", "label": "Stability measured", "ok": bool(sonic.get("obukhov_m")),
         "detail": (f"Sonic anemometer: {case.get('stability', '').replace('_', ' ')} air, L = {sonic['obukhov_m']:+.0f} m, u* = {sonic.get('ustar_m_s', 0):.2f} m/s"
                    + (f" at {local} local time" if local else "") if sonic.get("obukhov_m") else "No sonic readings")},
        {"id": "attribution", "label": "Sensors single out the source", "ok": att.get("status") == "agrees_single",
         "detail": (f"Only {att.get('best_fit_asset_id')} explains the readings" if att.get("status") == "agrees_single"
                    else (att.get("status") or "not assessed").replace("_", " "))},
    ]
    if not camera_first:
        steady = (q.get("qc") or {}).get("steady_state") or {}
        rows.append({"id": "steady", "label": "Plume steady", "ok": bool(steady) and abs((steady.get("late_half_rate_kg_h") or 0) - (steady.get("early_half_rate_kg_h") or 0))
                     <= (steady.get("max_relative_change") or 0.3) * max(steady.get("early_half_rate_kg_h") or 1e-9, 1e-9),
                     "detail": (f"Early and late halves agree: {steady['early_half_rate_kg_h']:.3f} vs {steady['late_half_rate_kg_h']:.3f} kg/h" if steady else "Not assessed")})
    lead = "the camera" if camera_first else "the primary tool"
    for c in q.get("cross_checks") or []:
        rows.append({"id": f"check_{c['tool']}", "label": f"{c['label']} agrees", "ok": bool(c["agrees"]),
                     "detail": f"{c['rate_kg_h']:.3f} kg/h, {c['ratio']:.2f}× {lead} (agreement: 0.67–1.5×)"})
    return {"rows": rows, "all_ok": all(r["ok"] for r in rows),
            "note": "The camera gives the rate; the sensor models check it and pin the source. Each tool runs only inside the conditions it was built for"
                    if camera_first else "Each tool runs only inside the conditions it was built for; outside them the agent changes tool or gives no number"}


@app.get("/quantification/methods")
def quantification_methods():
    return cached(("qmethods",), lambda: engine.get("/quantification/methods"), ttl=3600)


@app.get("/incidents/{incident_id}/quantification")
def get_quantification(incident_id: str):
    s = slot_or_404(incident_id)
    orch = orchestration(s)
    progress = None
    if s["engine_id"] and not step_event(orch, "quantify"):
        att = [e["detail"] for e in (orch or {}).get("events", []) if e["step"] == "quantify" and e["kind"] == "attempt"]
        progress = {"running": True, "attempts": len(att), "last": att[-1] if att else None,
                    "text": (f"Quantify agent: attempt {len(att)} at replay {att[-1]['as_of_seconds']} s — "
                             f"{(att[-1].get('reason') or att[-1].get('attribution') or att[-1]['status']).replace('_', ' ')}") if att
                            else "Quantify agent starts after Locate"}
    q = quant_result(s)
    return {"result": q, "methods": quant_methods(s), "release_check": release_check(s), "progress": progress,
            "conditions": conditions(s, q) if not progress else None, "catalog": quantification_methods()}


@app.post("/incidents/{incident_id}/quantify")
def post_quantify(incident_id: str, body: dict = Body(default={})):
    s = slot_or_404(incident_id)
    if not s["engine_id"] or s["dismissal"]:
        raise HTTPException(status_code=409, detail="No open incident")
    method = body.get("method", "agent")
    if method == "experimental_setpoint":
        if not reveal(s):
            raise HTTPException(status_code=409, detail={"failure": "Answer key locked", "missing": ["Revealed after the report is issued"]})
    else:
        rec = record(s)
        if not rec:
            raise HTTPException(status_code=409, detail={"failure": "The quantify agent is still running", "missing": ["Wait for the frozen estimate"]})
        snap = rec["snapshot"]
        tool = None if method == "agent" else method
        # Runs the agent's choice, or the tool the engineer picked, at the frozen clock; the frozen record never changes.
        res = engine.post(f"/incidents/{s['engine_id']}/quantify",
                          {"candidate_asset_id": snap["candidate_asset_id"], "as_of_seconds": snap["clock"]["scenario_seconds"], "tool": tool})
        frozen = snap["quantification"].get("rate_kg_h")
        run = {"tool": res.get("primary_tool"), "status": res["status"], "reason": res.get("reason"), "rate_kg_h": res.get("rate_kg_h"),
               "ratio_to_frozen": round(res["rate_kg_h"] / frozen, 3) if res.get("rate_kg_h") and frozen else None,
               "run_at_utc": now_utc(), "by": OPERATOR}
        s.setdefault("tool_runs", {})[res.get("primary_tool") or method] = run
        audit(s, "quantification_run", OPERATOR, f"Ran {TOOL_SHORT.get(run['tool'], run['tool'])} at the frozen clock: {res['status']}"
              + (f", {res['rate_kg_h']:.3f} kg CH₄/h (model estimated)" if res.get("rate_kg_h") is not None else ""))
        save()
    return get_quantification(incident_id)


@app.get("/incidents/{incident_id}/sensor-series")
def get_sensor_series(incident_id: str):
    s = slot_or_404(incident_id)
    eid = s["engine_id"] or "INC-001"
    rec = record(s)
    as_of = rec["snapshot"]["clock"]["scenario_seconds"] if rec else 900
    win = min(as_of, 900)
    m = cached(("meas", eid, as_of), lambda: engine.get(f"/incidents/{eid}/measurements", {"as_of_seconds": as_of, "window_seconds": win}), ttl=60)
    fac = cached(("facility",), lambda: engine.get("/facility"), ttl=3600)
    var = {x["id"]: x["variable"] for x in fac["sensors"]}
    step = 10
    by = {}
    for r in m["measurements"]:
        if r["value"] is None or r["quality_flag"] != "good" or int(r["timestamp_utc"][17:19]) % step:
            continue
        by.setdefault(r["sensor_id"], []).append([utc(r["timestamp_utc"]), r["value"]])
    methane = {k: v for k, v in by.items() if var.get(k) == "methane"}
    peak = max(methane, key=lambda k: max(x[1] for x in methane[k])) if methane else None
    pick = lambda v: next((k for k in by if var.get(k) == v), None)
    wind_s, wind_d, sonic_l = pick("wind_speed"), pick("wind_direction"), pick("obukhov_length")
    # Pressure at the suspect equipment (the K-201 load step shows there), else the first pressure sensor.
    src = rec["snapshot"]["candidate_asset_id"] if rec else "XV-2104"
    pres = next((x["id"] for x in fac["sensors"] if x["variable"] == "pressure" and x["asset_id"] == src and x["id"] in by), pick("pressure"))
    series = {"methane_ppm": methane.get(peak, []), "wind_speed_m_s": by.get(wind_s, []),
              "wind_dir_deg_from_north": by.get(wind_d, []),
              "pressure_bara": [[t, v / 1e5 if v > 1000 else v] for t, v in by.get(pres, [])],
              "obukhov_m": by.get(sonic_l, [])}
    times = [p[0] for p in series["methane_ppm"]]
    return {"series": series, "sensor_of": {"methane_ppm": peak, "wind_speed_m_s": wind_s, "wind_dir_deg_from_north": wind_d, "pressure_bara": pres, "obukhov_m": sonic_l},
            "methane_by_sensor": methane, "provenance": "simulated", "interval_label": f"{step} s samples",
            "source": [f"InfluxDB ({m.get('backend')})"],
            "site": {"replay_start_utc": times[0] if times else None, "replay_end_utc": times[-1] if times else None}}


@app.get("/incidents/{incident_id}/plant-history")
def get_plant_history(incident_id: str):
    s = slot_or_404(incident_id)
    eid = s["engine_id"] or "INC-001"
    return cached(("hist", eid), lambda: engine.get(f"/incidents/{eid}/plant-history", {"include_series": "true"}), ttl=600)


# ----- Plan + Approve (work order from the Nemotron planner) -----

def generator(rec):
    plan = ((rec or {}).get("work_order") or {}).get("plan") or {}
    model = (plan.get("model_run") or {}).get("model") or "nvidia/nemotron-3-ultra-550b-a55b"
    if plan.get("source_origin") == "llm":
        short = model.split("/")[-1]
        return {"id": "nemotron-planner", "version": short, "kind": "llm", "author": f"Nemotron planner ({short})",
                "label": "Nemotron 3 Ultra · NVIDIA hosted endpoint",
                "note": "Drafted by the Nemotron planner from the investigation findings and the frozen evidence."}
    return {"id": "deterministic-template", "version": "engine", "kind": "deterministic", "author": "Deterministic template (engine fallback)",
            "label": "Deterministic template (model unavailable)",
            "note": "The model was unavailable; the engine built a cited template instead. Not AI output."}


def plan_content(wo):
    plan = wo["plan"]
    c = (plan.get("evidence_backed_draft") or {}).get("content")
    if c:
        return c
    try:
        return json.loads(plan.get("text") or "{}")
    except ValueError:
        return {}


def statement(x):
    return (x, []) if isinstance(x, str) else (x.get("statement", ""), x.get("evidence_ids", []))


def build_draft(rec, wo, ev):
    c = plan_content(wo)
    sections = {k: [] for k in ("observed", "uncertain", "inspection", "isolation", "safety")}

    def add(sec, prefix, item, prov):
        text, ids = statement(item)
        sections[sec].append({"id": f"{prefix}{len(sections[sec]) + 1}-d{wo['version']}", "text": text, "provenance": prov,
                              "cites": [ev.add(i) for i in ids if i]})
    for x in c.get("observed_evidence", []):
        add("observed", "O", x, "agent_drafted")
    for x in c.get("hypotheses", []):
        add("uncertain", "H", x, "hypothesis")
    for x in c.get("unresolved_questions", []):
        add("uncertain", "U", x, "gap")
    for x in c.get("recommended_checks", []):
        add("inspection", "I", x, "agent_drafted")
    for x in c.get("safety_prerequisites", []):
        text = statement(x)[0].lower()
        add("isolation" if "isolat" in text or "boundar" in text else "safety", "S", x, "agent_drafted")
    sel = rec["snapshot"]["candidate_asset_id"]
    inv = ((rec.get("investigation") or {}).get("findings") or {}).get("content") or {}
    hyp = statement((c.get("hypotheses") or [""])[0])[0] or (inv.get("summary") or c.get("title") or "").split(". ")[0]
    return {"title": c.get("title"), "assessment": f"Likely source {sel}, unconfirmed: {hyp.rstrip('.')}.",
            "sections": sections, "evidence": ev.items(), "engine_draft_version": wo["version"],
            "source_origin": wo["plan"].get("source_origin"), "revision_of": wo["plan"].get("revision_of"),
            "model_run": wo["plan"].get("model_run")}


def default_fields(rec, wo):
    c = plan_content(wo)
    checks = [statement(x)[0] for x in c.get("recommended_checks", [])]
    prereq = [statement(x)[0] for x in c.get("safety_prerequisites", [])]
    sel = rec["snapshot"]["candidate_asset_id"]
    return {"asset": sel, "priority": None, "problem": c.get("title") or f"Possible methane release near {sel}",
            "proposed_inspection": checks[0] if checks else None,
            "required_permits": [p for p in prereq if "permit" in p.lower()]}


def investigation_view(rec, names=None):
    inv = (rec or {}).get("investigation")
    if not inv:
        return None
    f = inv["findings"]
    c = f.get("content") or {}
    req = inv.get("research_request") or {}
    return {"round": inv.get("round", 1), "changes": c.get("changes", []), "focus": req.get("focus") or None,
            "requested_by": (names or {}).get(req.get("id"), "Engineer") if req else None,
            "earlier_rounds": len(rec.get("investigation_history", [])),
            "summary": c.get("summary"), "findings": c.get("findings", []), "open_questions": c.get("open_questions", []),
            "candidate_assessment": c.get("candidate_assessment", []), "source_origin": f.get("source_origin"),
            "model": f.get("model"), "deployment": f.get("deployment"), "recorded_at": utc(inv.get("recorded_at")),
            "replayed": bool(f.get("replay_of")),
            "tools": [a["tool"] for a in f.get("activity", []) if a["tool"] != "model_inference"]}


AGENT_TEXT = {"detect": "Registering the camera alert", "locate": "Locating candidate equipment in the knowledge graph",
              "quantify": "Waiting for a steady plume to estimate the rate", "freeze": "Freezing the evidence snapshot",
              "investigate": "Investigation agent reading the graph, sensors, 47-day history, maintenance and procedures",
              "plan": "Planner drafting the work order", "submit": "Submitting the draft", "answer": "Agent answering a question",
              "revise": "Planner revising the draft", "verify": "Verifier checking post-repair readings",
              "reinvestigate": "Investigation agent researching again with the field notes and answers so far"}


def agent_status(s):
    orch = orchestration(s)
    if not orch:
        return None
    running = orch["status"] == "running"
    step = orch.get("current_step")
    # The automatic chain's investigate/plan replay a recorded Nemotron run when one exists (engine/data/recorded_runs).
    recs = cached(("recordings",), lambda: engine.try_get("/model/recordings") or {}, ttl=30)
    replayed = running and f"{s['incident_id']}.{step}" in recs and not (step == "plan" and (record(s) or {}).get("draft_version"))
    return {"status": orch["status"], "current_step": step, "replayed": bool(replayed),
            "text": AGENT_TEXT.get(orch.get("current_step"), orch.get("current_step")) if running else None,
            "failed": orch["status"] in ("failed", "interrupted"), "detail": (orch.get("result") or {}).get("detail")}


def field_notes_view(s, rec):
    return [{"id": n["evidence_id"], "text": n["text"], "recorded_by": s["names"].get(n["id"], "Engineer"),
             "recorded_at": utc(n.get("recorded_at"))} for n in (rec or {}).get("field_notes", [])]


def research_view(s, rec):
    """Whether another research round can start, or one is under way."""
    if not rec:
        return None
    req = rec.get("research_request")
    return {"requested": bool(req), "round": req and req["round"], "focus": req and req["focus"],
            "requested_by": req and s["names"].get(req["id"], "Engineer"),
            "can_request": rec["state"] == "pending_review" and not req and bool(s["submitted"]),
            "next_round": len(rec.get("investigation_history", [])) + 2}


def questions_view(s, rec):
    out = []
    for q in (rec or {}).get("questions", []):
        a = (q.get("answer") or {}).get("answer") or {}
        c = a.get("content") or {}
        out.append({"id": q["id"], "question": q["question"], "asked_by": s["names"].get(q["id"], "Engineer"),
                    "status": q["status"], "asked_at": utc(q.get("recorded_at")), "answer": c.get("answer"),
                    "claims": c.get("claims", []), "cannot_determine": c.get("cannot_determine_from_evidence"),
                    "suggested_follow_up": c.get("suggested_follow_up", []), "source_origin": a.get("source_origin"), "model": a.get("model")})
    return out


def agent_activity(rec):
    draft = (((rec or {}).get("work_order") or {}).get("plan") or {}).get("evidence_backed_draft") or {}
    return [{"tool": a["tool"], "status": a.get("status"), "elapsed_ms": a.get("elapsed_ms"), "model": a.get("model")} for a in draft.get("activity", [])]


def draft_inputs(s, rec):
    q = quant_result(s)
    sel = selected_asset(s)
    snap = (rec or {}).get("snapshot", {})
    att = (q or {}).get("attribution") or {}
    inv = investigation_view(rec)
    rate = q and q.get("rate_kg_ch4_h")
    return [
        {"id": "source", "label": "Likely source", "value": sel, "provenance": "hypothesis", "screen": "locate.html",
         "detail": f"Top of {len(snap.get('candidates', []))} camera candidates · sensors {att.get('status', 'pending').replace('_', ' ')}" if sel else "Locate agent running"},
        {"id": "rate", "label": "Emission rate", "value": f"{rate:.2f} kg CH₄/h" if rate is not None else None, "screen": "quantify.html",
         "detail": (f"Sensitivity {q['interval']['low']}–{q['interval']['high']} kg/h" if rate is not None and q.get("interval")
                    else (q or {}).get("missing", ["Quantify agent running"])[0]),
         "provenance": "model_estimated" if rate is not None else "pending"},
        {"id": "detection", "label": "Camera observation", "value": s["camera_id"] if s["engine_id"] else None, "screen": "detection.html",
         "detail": "OWLv2 + SAM2 alert on recorded GasVid footage" if s["engine_id"] else "No alert", "provenance": "model_estimated" if s["engine_id"] else "pending"},
        {"id": "docs", "label": "Investigation", "value": f"{len(inv['findings'])} findings" if inv else None, "screen": None,
         "detail": (f"{sum(f['kind'] == 'contradiction' for f in inv['findings'])} contradictions, {sum(f['kind'] == 'gap' for f in inv['findings'])} gaps · "
                    f"{len(snap.get('field_reports', []))} field reports, {len(snap.get('commitments', []))} commitments, plant history") if inv else "Investigation agent running",
         "provenance": "agent_drafted" if inv else "pending"},
    ]


def work_order_view(s):
    sync(s)
    rec = record(s)
    rv = latest_review(s)
    base = {"generator": generator(rec), "can_draft": False, "missing": [] if s["engine_id"] else ["No alert raised yet (Detect screen)"],
            "inputs": draft_inputs(s, rec), "nvidia_inference": nvidia_status(), "agent": agent_status(s),
            "investigation": investigation_view(rec, s["names"]), "questions": questions_view(s, rec),
            "can_ask": bool(rec) and rec["state"] in ("pending_review", "verification_ready"),
            "field_notes": field_notes_view(s, rec), "research": research_view(s, rec),
            "engine_state": rec and rec["state"], "engine_incident_id": s["engine_id"], "agent_activity": agent_activity(rec)}
    if not s["versions"]:
        return {**base, "draft": None, "versions": [], "flags": {}, "submitted": None, "stale": False, "review": None, "reviews": s["reviews"]}
    latest = s["versions"][-1]
    draft = s["drafts"][str(latest["draft_version"])]
    approved = bool(rv and rv["decision"] == "approve")
    return {**base, "draft": {**draft, "fields": latest["fields"], "version": latest["version"], "hash": latest["hash"],
                              "status": "Approved" if approved else "Draft"},
            "versions": [{k: v[k] for k in ("version", "hash", "created_at", "author", "changed", "draft_version")} for v in s["versions"]],
            "flags": s["flags"], "submitted": s["submitted"], "stale": False, "review": rv, "reviews": s["reviews"]}


def wo_or_409(s):
    if not s["engine_id"]:
        raise HTTPException(status_code=409, detail=f"No active incident: {s['camera_id']} has not raised an alert")
    if s["dismissal"]:
        raise HTTPException(status_code=409, detail=f"Closed as a false alarm by {s['dismissal']['by']}. Reopen it on the Detect screen first.")
    sync(s)
    if not s["versions"]:
        raise HTTPException(status_code=409, detail="No draft yet: the agents are still working")
    return s["versions"][-1]


def check_unlocked(s):
    rv = latest_review(s)
    if rv and rv["decision"] == "approve":
        raise HTTPException(status_code=409, detail=f"Approved by {rv['reviewer']}; the draft is locked.")


@app.get("/incidents/{incident_id}/work-order")
def get_work_order(incident_id: str):
    return work_order_view(slot_or_404(incident_id))


@app.post("/incidents/{incident_id}/work-order/draft")
def post_work_order_draft(incident_id: str):
    """Restart failed agents (completed steps replay idempotently; the planner falls back to the labelled
    template). A healthy run drafts on its own."""
    s = slot_or_404(incident_id)
    orch = orchestration(s)
    if orch and orch["status"] in ("failed", "interrupted"):
        engine.post(f"/incidents/{s['engine_id']}/orchestration", {"plan_mode": "model", "plan_fallback": "deterministic"}, human=True)
        audit(s, "agents_restarted", OPERATOR, f"Agents restarted after: {(orch.get('result') or {}).get('detail') or orch['status']}")
        forget(s)
        save()
        return work_order_view(s)
    raise HTTPException(status_code=409, detail="The planner drafts automatically. Edit the fields here, or use Request changes "
                                                "on Approve: the planner then revises the draft.")


EDITABLE = ("problem", "proposed_inspection", "priority", "required_permits")


@app.post("/incidents/{incident_id}/work-order/edit")
def post_work_order_edit(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    latest = wo_or_409(s)
    check_unlocked(s)
    if body.get("base_version") != latest["version"]:
        raise HTTPException(status_code=409, detail=f"Draft changed since you opened it (now v{latest['version']}). Reload.")
    fields = dict(latest["fields"])
    for k, v in (body.get("fields") or {}).items():
        if k in EDITABLE:
            fields[k] = [x.strip() for x in v if x.strip()] if k == "required_permits" else (v.strip() or None if v else None)
    changed = [k for k in EDITABLE if fields[k] != latest["fields"][k]]
    if not changed:
        raise HTTPException(status_code=400, detail="No changes")
    with _lock:
        add_version(s, latest["draft_version"], fields, OPERATOR, changed)
        save()
    return work_order_view(s)


@app.post("/incidents/{incident_id}/work-order/flag")
def post_work_order_flag(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    latest = wo_or_409(s)
    check_unlocked(s)
    cid = body.get("claim_id")
    draft = s["drafts"][str(latest["draft_version"])]
    if cid not in {c["id"] for cs in draft["sections"].values() for c in cs}:
        raise HTTPException(status_code=400, detail="Unknown claim")
    if body.get("flagged", True):
        s["flags"][cid] = {"note": (body.get("note") or "").strip() or None, "by": OPERATOR, "at": now_utc()}
        audit(s, "claim_flagged", OPERATOR, f"{cid} flagged as unsupported", latest["version"], latest["hash"])
    elif s["flags"].pop(cid, None):
        audit(s, "claim_unflagged", OPERATOR, f"{cid} flag cleared", latest["version"], latest["hash"])
    s["submitted"] = None
    save()
    return work_order_view(s)


@app.post("/incidents/{incident_id}/work-order/submit")
def post_work_order_submit(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    latest = wo_or_409(s)
    if body.get("version") != latest["version"]:
        raise HTTPException(status_code=409, detail=f"Not the latest draft (now v{latest['version']}). Reload.")
    if not latest["fields"]["priority"]:
        raise HTTPException(status_code=409, detail="Set a priority before submitting")
    check_unlocked(s)
    if latest_review(s):
        raise HTTPException(status_code=409, detail=f"v{latest['version']} was returned by {latest_review(s)['reviewer']}; wait for the planner's revision or edit it.")
    st = state_of(s)
    if st != "pending_review":
        raise HTTPException(status_code=409, detail=f"The engine record is '{st}', not awaiting review yet. Wait for the agents to finish.")
    s["submitted"] = {"version": latest["version"], "hash": latest["hash"], "at": now_utc(), "by": OPERATOR, "open_flags": sorted(s["flags"])}
    audit(s, "submitted_for_review", OPERATOR, f"v{latest['version']} submitted, {len(s['flags'])} flagged claims", latest["version"], latest["hash"])
    save()
    return work_order_view(s)


DECISIONS = {"approve": "Approved", "request_changes": "Changes requested", "reject": "Rejected"}


@app.post("/incidents/{incident_id}/review")
def post_review(incident_id: str, body: dict = Body(...)):
    """A named engineer decides. The engine records it under the server-held engineer credential; the typed
    name, reason, priority, operator edits and flags go into the rationale. Request changes wakes the planner."""
    s = slot_or_404(incident_id)
    wo_or_409(s)
    decision, reviewer = body.get("decision"), (body.get("reviewer") or "").strip()[:80]
    reason = (body.get("reason") or "").strip() or None
    if decision not in DECISIONS:
        raise HTTPException(status_code=400, detail="Unknown decision")
    if not reviewer:
        raise HTTPException(status_code=400, detail="Enter the reviewer's name")
    if decision != "approve" and not reason:
        raise HTTPException(status_code=400, detail="A reason is required")
    rv = latest_review(s)
    if rv:
        raise HTTPException(status_code=409, detail=f"Already decided: {DECISIONS[rv['decision']]} by {rv['reviewer']} at {rv['at']}.")
    sub = s["submitted"]
    if not sub:
        raise HTTPException(status_code=409, detail="No draft is waiting for review")
    if body.get("version") != sub["version"] or body.get("hash") != sub["hash"]:
        raise HTTPException(status_code=409, detail=f"The submitted draft changed (now v{sub['version']}). Reload.")
    rec = record(s)
    latest = s["versions"][-1]
    fields = latest["fields"]
    edits = [k for k in EDITABLE if k != "priority" and fields.get(k) != default_fields(rec, rec["work_order"]).get(k)]
    flagged = [f"{cid}{': ' + f['note'] if f.get('note') else ''}" for cid, f in s["flags"].items()]
    rationale = (f"Reviewer {reviewer}: " + (reason or "Approved.") + f" Priority {fields.get('priority')}."
                 + (f" Operator edited: {', '.join(edits)}." if edits else "")
                 + (f" Claims flagged as unsupported: {'; '.join(flagged)}." if flagged else ""))
    engine.post(f"/incidents/{s['engine_id']}/review",
                {"expected_version": rec["version"], "idempotency_key": engine.key("review"), "draft_version": latest["draft_version"],
                 "decision": decision, "rationale": rationale[:5000]}, human=True)
    forget(s)
    rv = {"decision": decision, "version": sub["version"], "hash": sub["hash"], "reviewer": reviewer, "reason": reason,
          "at": now_utc(), "open_flags": sub["open_flags"]}
    s["reviews"].append(rv)
    s["submitted"] = None
    audit(s, f"review_{decision}", reviewer, DECISIONS[decision] + (f": {reason}" if reason else ""), sub["version"], sub["hash"])
    save()
    return work_order_view(s)


@app.post("/incidents/{incident_id}/questions")
def post_question(incident_id: str, body: dict = Body(...)):
    """Ask the agent about the frozen evidence. The answer arrives about a minute later and never changes the draft."""
    s = slot_or_404(incident_id)
    question, by = (body.get("question") or "").strip()[:2000], (body.get("asked_by") or "").strip()[:80] or "Engineer"
    if not question:
        raise HTTPException(status_code=400, detail="Type a question")
    rec = record(s)
    if not rec or rec["state"] not in ("pending_review", "verification_ready"):
        raise HTTPException(status_code=409, detail="Questions can be asked while a draft awaits review, or before closure")
    res = engine.post(f"/incidents/{s['engine_id']}/questions",
                      {"question": question, "expected_version": rec["version"], "idempotency_key": engine.key("q")}, human=True)
    qid = next((q["id"] for q in reversed(res.get("questions", [])) if q["question"] == question), None)
    if qid:
        s["names"][qid] = by
    audit(s, "question_asked", by, question[:200])
    forget(s)
    save()
    return work_order_view(s)


@app.post("/incidents/{incident_id}/field-notes")
def post_field_note(incident_id: str, body: dict = Body(...)):
    """An engineer's field observation, added beside the frozen evidence. Agents read it from then on."""
    s = slot_or_404(incident_id)
    text, by = (body.get("text") or "").strip()[:3000], (body.get("recorded_by") or "").strip()[:80] or "Engineer"
    if not text:
        raise HTTPException(status_code=400, detail="Type the field note")
    rec = record(s)
    if not rec or rec["state"] not in ("pending_review", "verification_ready"):
        raise HTTPException(status_code=409, detail="Field notes can be added while a draft awaits review, or before closure")
    res = engine.post(f"/incidents/{s['engine_id']}/field-notes",
                      {"text": text, "expected_version": rec["version"], "idempotency_key": engine.key("note")}, human=True)
    note = (res.get("field_notes") or [None])[-1]
    if note:
        s["names"][note["id"]] = by
    audit(s, "field_note_added", by, text[:200])
    forget(s)
    save()
    return work_order_view(s)


@app.post("/incidents/{incident_id}/research")
def post_research(incident_id: str, body: dict = Body(...)):
    """Another research round: the investigator reruns with the field notes and answers so far, then the planner
    revises the draft. The draft under review is withdrawn until the revision arrives, and needs approval again."""
    s = slot_or_404(incident_id)
    focus, by = (body.get("focus") or "").strip()[:2000], (body.get("requested_by") or "").strip()[:80] or "Engineer"
    rec = record(s)
    if not rec or rec["state"] != "pending_review" or not s["submitted"]:
        raise HTTPException(status_code=409, detail="A research round can start while a draft awaits review")
    if rec.get("research_request"):
        raise HTTPException(status_code=409, detail="A research round is already running")
    res = engine.post(f"/incidents/{s['engine_id']}/research",
                      {"focus": focus, "expected_version": rec["version"], "idempotency_key": engine.key("research")}, human=True)
    req = res.get("research_request") or {}
    if req.get("id"):
        s["names"][req["id"]] = by
    s["submitted"] = None
    audit(s, "research_requested", by, f"Research round {req.get('round')}" + (f": {focus[:180]}" if focus else ""))
    forget(s)
    save()
    return work_order_view(s)


@app.get("/incidents/{incident_id}/audit")
def get_audit(incident_id: str):
    s = slot_or_404(incident_id)
    sync(s)
    return {"events": s["audit"]}


# ----- Report, repair, verification, closure -----

def follow_up(s, rec):
    if not rec:
        return None
    st = rec["state"]
    v = rec.get("verification") or {}
    rc = v.get("recovery_check") or {}
    recm = (v.get("recommendation") or {}).get("content") or {}
    checks = []
    if s["versions"]:
        checks = [c["text"] for c in s["drafts"][str(s["versions"][-1]["draft_version"])]["sections"]["inspection"]]
    return {"state": st, "can_issue": st == "approved", "can_record_repair": st in ("approved", "reported", "follow_up_required"),
            "can_close": st == "verification_ready", "approved_checks": checks,
            "repair": rec.get("repair") and {"work_performed": rec["repair"]["work_performed"], "at": utc(rec["repair"]["recorded_at"])},
            "verification": (v or None) and {"recommendation": recm.get("recommendation"), "summary": recm.get("summary"),
                                             "residual_risks": recm.get("residual_risks", []), "claims": recm.get("claims", []),
                                             "recovery_check": rc.get("status"), "sensors": rc.get("sensors", []),
                                             "post_repair_rate": rc.get("post_repair_rate"), "consistency": v.get("consistency"),
                                             "warning": rc.get("warning"), "source_origin": (v.get("recommendation") or {}).get("source_origin")},
            "closure": rec.get("closure") and {"decision": rec["closure"]["decision"], "rationale": rec["closure"]["rationale"],
                                               "at": utc(rec["closure"]["recorded_at"])},
            "agent": agent_status(s), "graph_memory": s.get("graph_memory")}


def make_report(s):
    sync(s)
    rec, orch = record(s), orchestration(s)
    q = quant_result(s)
    latest = s["versions"][-1] if s["versions"] else None
    rv = latest_review(s)
    st = rec["state"] if rec else None
    final = st in REPORTED
    det = step_event(orch, "detect")
    inv = investigation_view(rec)
    fu = follow_up(s, rec) or {}
    key = release_check(s)
    ver = fu.get("verification") or {}

    def row(label, value, cites=(), note=None, prov=None):
        return {"label": label, "value": value, "cites": list(cites), "note": note, "provenance": prov}
    evidence = [{"id": "ev-video", "label": "GasVid MOV_1242, METEC metered release (2017-07-13)", "kind": "video", "provenance": "observed",
                 "source": "engine /videos/MOV_1242", "screen": "detection.html"},
                {"id": "ev-sensors", "label": "Methane and wind telemetry (InfluxDB)", "kind": "sensor", "provenance": "simulated",
                 "source": "engine /measurements", "screen": "quantify.html"},
                {"id": "ev-graph", "label": "Plant knowledge graph (Neo4j)", "kind": "scenario", "provenance": "simulated",
                 "source": "engine /graph/plant", "screen": "graph.html"}]
    if latest:
        evidence += [{k: e.get(k) for k in ("id", "label", "kind", "provenance", "source", "screen")}
                     for e in s["drafts"][str(latest["draft_version"])]["evidence"]]
    att = (q or {}).get("attribution") or {}
    sections = [
        {"id": "camera", "title": "Camera detection", "rows": [
            row("Camera", s["camera_id"] if s["engine_id"] else None, ["ev-video"]),
            row("Alert", f"Model alert at clip {det['detail']['clip_time_seconds']:.1f} s" if det else None, ["ev-video"],
                "OWLv2 + SAM2, executed on recorded footage", "model_estimated" if det else None),
            row("Raised at", s["triggered_at"]),
            row("Engine incident", s["engine_id"])]},
        {"id": "candidates", "title": "Likely source", "rows": [
            row("Likely source", selected_asset(s), ["ev-graph"], "Unconfirmed until a field check", "hypothesis"),
            row("Sensors vs camera", att.get("status", "").replace("_", " ") or None, ["ev-sensors"], att.get("note"), "model_estimated" if att else None),
            row("Investigation", inv and inv["summary"], [], inv and f"{len(inv['findings'])} findings by the investigation agent", "agent_drafted" if inv else None)]},
        {"id": "emissions", "title": "Emissions", "rows": [
            row("Rate", f"{q['rate_kg_ch4_h']:.3f} kg CH₄/h" if q and q.get("rate_kg_ch4_h") is not None else None, ["ev-sensors"],
                q and q.get("interval") and f"Sensitivity {q['interval']['low']}–{q['interval']['high']} kg/h, not a confidence interval",
                "model_estimated" if q and q.get("rate_kg_ch4_h") is not None else None),
            row("Method", q and q["model_version"]),
            row("Metered release on camera (answer key)", f"{key['rate_kg_ch4_h']:.4f} kg CH₄/h" if key and key.get("rate_kg_ch4_h") else None, ["ev-video"],
                f"Estimate error {key['error_percent']:+.1f}%" if key and key.get("error_percent") is not None else "Revealed after the report is issued",
                "experimental_setpoint" if key else None)]},
        {"id": "action", "title": "Work order", "rows": [
            row("Asset", latest and latest["fields"].get("asset")),
            row("Problem", latest and latest["fields"].get("problem")),
            row("Proposed inspection", latest and latest["fields"].get("proposed_inspection"), [], None, "agent_drafted" if latest else None),
            row("Priority", latest and latest["fields"].get("priority")),
            row("Drafted by", generator(rec)["label"] if latest else None, [], f"v{latest['version']} (planner draft {latest['draft_version']})" if latest else None)]},
        {"id": "decision", "title": "Engineer decision", "rows": [
            row("Reviewer", rv["reviewer"] if rv else None, [], None, "human_decision" if rv else None),
            row("Decision", DECISIONS[rv["decision"]] if rv else None),
            row("Decided at", rv["at"] if rv else None),
            row("Reason", rv and rv.get("reason"))]},
        {"id": "followup", "title": "Repair, verification and closure", "rows": [
            row("Repair record", (fu.get("repair") or {}).get("work_performed"), [], "Human account of field work; the system performs no physical action",
                "human_decision" if fu.get("repair") else None),
            row("Verifier recommendation", ver.get("recommendation"), [], ver.get("summary"), "agent_drafted" if ver else None),
            row("Post-repair readings", (ver.get("recovery_check") or "").replace("_", " ") or None, ["ev-sensors"], ver.get("warning"), "simulated" if ver else None),
            row("Closure", (fu.get("closure") or {}).get("decision"), [], (fu.get("closure") or {}).get("rationale"), "human_decision" if fu.get("closure") else None)]},
        {"id": "audit", "title": "Audit trail", "rows": []},
    ]
    gaps = [f"{sec['title']}: {r['label']}" for sec in sections for r in sec["rows"] if r["value"] is None]
    status_text = ("Closed by an engineer; the incident is written to the knowledge graph." if st == "closed"
                   else "Final: issued by the engine after engineer approval." if final
                   else "Approved. Export the report to issue it." if st == "approved"
                   else "Draft: not approved yet. Values may still change.")
    content = json.dumps([sections, [e["id"] for e in evidence]], sort_keys=True, default=str)
    return {"local_incident_id": s["incident_id"], "engine_incident_id": s["engine_id"], "status": "final" if final else "draft",
            "status_text": status_text, "closure": fu.get("closure"), "generated_at": now_utc(),
            "report_hash": hashlib.sha256(content.encode()).hexdigest()[:12], "sections": sections, "audit": s["audit"],
            "evidence": evidence, "gaps": gaps, "follow_up": fu, "answer_key": key and key.get("answer_key"),
            "footer": f"Atelic engine record {s['engine_id'] or '—'} · advisory only · no equipment control", "investigation": inv}


@app.get("/incidents/{incident_id}/report")
def get_report(incident_id: str):
    s = slot_or_404(incident_id)
    return {**make_report(s), "exports": s["exports"]}


def issue_report(s, actor):
    rec = record(s)
    if rec and rec["state"] == "approved":
        engine.post(f"/incidents/{s['engine_id']}/report", {"expected_version": rec["version"], "idempotency_key": engine.key("report")}, human=True)
        forget(s)
        audit(s, "report_issued", actor, "Final report issued by the engine (approved draft, frozen evidence)")


@app.post("/incidents/{incident_id}/report/export")
def post_report_export(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    fmt = body.get("format")
    if fmt not in ("json", "md", "pdf"):
        raise HTTPException(status_code=400, detail="Unknown format")
    issue_report(s, OPERATOR)  # the first export after approval issues the engine report
    report = make_report(s)
    s["exports"].append({"format": fmt, "hash": report["report_hash"], "status": report["status"], "at": report["generated_at"]})
    audit(s, "report_exported", OPERATOR, f"{report['status'].title()} report exported as {fmt.upper()}, hash {report['report_hash']}")
    save()
    name = f"{incident_id}_report_{report['report_hash']}"
    if fmt == "json":
        return Response(json.dumps(report, indent=2, ensure_ascii=False, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if fmt == "md":
        return Response(to_markdown(report), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    return {"recorded": True, "hash": report["report_hash"]}


def to_markdown(r):
    lines = [f"# Incident report {r['local_incident_id']} ({r['engine_incident_id']})", "", f"_{r['status_text']}_", ""]
    for sec in r["sections"]:
        lines += [f"## {sec['title']}", ""]
        if sec["id"] == "audit":
            lines += [f"- {e['at']} · {e['event']} · {e['actor']} · {e['detail']}" for e in r["audit"]]
        else:
            lines += [f"- **{x['label']}**: {x['value'] if x['value'] is not None else 'Not available'}" + (f" ({x['note']})" if x.get("note") else "")
                      for x in sec["rows"]]
        lines.append("")
    lines.append(f"Report hash {r['report_hash']} · {r['footer']}")
    return "\n".join(lines)


@app.post("/incidents/{incident_id}/repair")
def post_repair(incident_id: str, body: dict = Body(...)):
    """A human account of the field repair; the verifier agent then checks post-repair readings."""
    s = slot_or_404(incident_id)
    text, by = (body.get("work_performed") or "").strip(), (body.get("by") or "").strip()[:80]
    if not by:
        raise HTTPException(status_code=400, detail="Enter your name")
    if not text:
        raise HTTPException(status_code=400, detail="Describe the work performed")
    if not record(s) or record(s)["state"] not in ("approved", "reported", "follow_up_required"):
        raise HTTPException(status_code=409, detail="A repair can be recorded once the work order is approved")
    issue_report(s, by)
    rec = record(s)
    engine.post(f"/incidents/{s['engine_id']}/repair",
                {"work_performed": f"Recorded by {by}: {text}"[:5000], "expected_version": rec["version"], "idempotency_key": engine.key("repair")}, human=True)
    audit(s, "repair_recorded", by, text[:200])
    forget(s)
    save()
    return get_report(incident_id)


@app.post("/incidents/{incident_id}/close")
def post_close(incident_id: str, body: dict = Body(...)):
    s = slot_or_404(incident_id)
    decision, by, why = body.get("decision"), (body.get("by") or "").strip()[:80], (body.get("rationale") or "").strip()
    if decision not in ("close", "keep_open"):
        raise HTTPException(status_code=400, detail="Unknown decision")
    if not by or not why:
        raise HTTPException(status_code=400, detail="Enter your name and a reason")
    rec = record(s)
    if not rec or rec["state"] != "verification_ready":
        raise HTTPException(status_code=409, detail="Waiting for the verifier")
    res = engine.post(f"/incidents/{s['engine_id']}/close",
                      {"decision": decision, "rationale": f"{by}: {why}"[:5000], "expected_version": rec["version"], "idempotency_key": engine.key("close")}, human=True)
    s["graph_memory"] = res.get("graph_memory")
    audit(s, "incident_closed" if decision == "close" else "incident_kept_open", by, why[:200])
    forget(s)
    for k in (("plant",), ("graph-ui",)):
        _cache.pop(k, None)
    save()
    return get_report(incident_id)


# ----- Glass box -----

STEP_LABELS = [("camera_event", "Camera event received"), ("equipment_mapping", "Equipment mapping queried"),
               ("graph", "Graph relationships retrieved"), ("emission_estimate", "Emission estimate calculated"),
               ("investigation", "Investigation agent"), ("procedure_passages", "Procedure passages retrieved"),
               ("work_order", "Work order drafted"), ("approval", "Human approval required"),
               ("answer", "Engineer questions answered"), ("verification", "Repair verified"), ("closure", "Human closure")]
STEP_REASON = {"camera_event": "No alert yet. Trigger the event on the Detect screen.", "equipment_mapping": "Runs after the camera alert.",
               "graph": "Runs after the camera alert.", "emission_estimate": "Runs after Locate.",
               "investigation": "Runs after the estimate is frozen.", "procedure_passages": "Retrieved by the agents through retrieve_procedure.",
               "work_order": "Runs after the investigation.", "answer": "No question asked yet. Ask one on Approve.",
               "verification": "Runs after a repair is recorded on Report."}
HUMAN_EVENT = {"review": None, "question": "question_asked", "research": "research_requested", "report_issued": "report_issued", "repair_recorded": "repair_recorded", "closure": "incident_closed"}


def agent_runs(s):
    orch = orchestration(s) or {"events": []}
    rec = record(s)
    spans = {k: [] for k, _ in STEP_LABELS}
    n = 0

    def span(step, e, tool, result, model=None, ok=True, evidence=(), component="Atelic engine", provider="Atelic engine (local code)"):
        nonlocal n
        n += 1
        spans[step].append({"id": f"S{n}", "step": step, "started_at": e["recorded_at"], "duration_ms": e["detail"].get("elapsed_ms"),
                            "component": component, "tool": tool, "model": model, "model_version": None, "provider": provider,
                            "location": DEPLOYMENT, "evidence": list(evidence), "result": result,
                            "guardrail": "Read-only tools; agents stop at every human decision", "note": None, "status": "ok" if ok else "blocked"})
    for e in orch["events"]:
        d, st, kind = e["detail"], e["step"], e["kind"]
        if st == "detect" and kind == "completed":
            span("camera_event", e, "model_alert: OWLv2 + SAM2 on recorded footage",
                 f"Alert at clip {d.get('clip_time_seconds') or 0:.1f} s → replay {d.get('scenario_seconds')} s", model="owlv2-base + sam2.1-hiera-small",
                 evidence=[{"label": "Executed detection run", "path": d.get("evidence_reference", ""), "screen": "detection.html"}])
        elif st == "locate" and kind == "tool_call":
            span("graph", e, f"{d.get('tool')}: Neo4j", f"{d.get('asset_id')} context from {d.get('backend')}", ok=d.get("status") == "completed",
                 evidence=[{"label": "Knowledge graph", "path": f"neo4j:Asset/{d.get('asset_id')}", "screen": "graph.html"}])
        elif st == "locate" and kind == "completed":
            span("equipment_mapping", e, "camera_candidates: origin-region overlap",
                 ", ".join(f"{c['asset_id']} {c['overlap_fraction']:.0%}" for c in d.get("candidates", [])) + f" · selected {d.get('selected_asset_id')} (unconfirmed)",
                 evidence=[{"label": "Camera regions", "path": "facility/cameras.json", "screen": "locate.html"}])
        elif st == "quantify" and kind == "attempt":
            span("emission_estimate", e, "estimate_emissions: Gaussian candidate fit",
                 f"replay {d['as_of_seconds']} s · " + (f"{d['rate_kg_h']:.3f} kg/h, sensors {(d.get('attribution') or '').replace('_', ' ')}" if d.get("rate_kg_h") is not None
                                                        else f"{d['status'].replace('_', ' ')}: {d.get('reason')}"),
                 ok=d["status"] == "estimated", evidence=[{"label": "Telemetry window", "path": f"InfluxDB · 300 s to replay {d['as_of_seconds']} s", "screen": "quantify.html"}])
        elif kind == "agent_activity" and st in AGENT_STEPS:
            tool = d.get("tool")
            target = {"investigate": "investigation", "reinvestigate": "investigation", "plan": "work_order", "revise": "work_order",
                      "answer": "answer", "verify": "verification"}[st]
            if tool in ("retrieve_procedure", "search_documents"):
                target = "procedure_passages"
            model = tool == "model_inference"
            span(target, e, f"{tool}: {'Nemotron on the NVIDIA hosted endpoint' if model else 'engine tool over the frozen snapshot'}",
                 f"{st} · {d.get('status')}", model=d.get("model") if model else None, ok=d.get("status") == "completed",
                 component="Nemotron agent" if model else "Agent tool", provider=d.get("endpoint") or "Atelic engine (local code)")
    human = []
    for e in orch["events"]:
        if e["step"] == "human":
            d = e["detail"]
            human.append({"event": f"review_{d.get('decision')}" if e["kind"] == "review" else HUMAN_EVENT.get(e["kind"], e["kind"]),
                          "actor": "Engineer (authenticated)", "at": utc(e["recorded_at"]), "version": d.get("draft_version"),
                          "detail": d.get("rationale") or d.get("question") or d.get("work_performed") or d.get("decision")
                                    or (f"Research round {d['round']}" + (f": {d['focus']}" if d.get("focus") else "") if e["kind"] == "research" else "")})
    human += [{k: e[k] for k in ("event", "actor", "at", "version", "detail")} for e in s["audit"] if e["event"] == "submitted_for_review"]
    human.sort(key=lambda x: x["at"] or "")
    st = rec and rec["state"]
    approval = ("approved" if st in APPROVED_OR_LATER else "returned" if latest_review(s) else "waiting" if s["submitted"] else "not_reached")
    steps = []
    for key_, label in STEP_LABELS:
        if key_ == "approval":
            steps.append({"id": key_, "label": label, "status": approval, "spans": [],
                          "human": [h for h in human if h["event"].startswith(("review_", "submitted"))],
                          "reason": {"not_reached": "No draft has been submitted for review yet.", "waiting": "Waiting for a named engineer on the Approve screen."}.get(approval),
                          "guardrail": "Engine rule: only an authenticated human can approve; no auto-approval."})
        elif key_ == "closure":
            closed = st == "closed"
            steps.append({"id": key_, "label": label, "status": "approved" if closed else "waiting" if st == "verification_ready" else "not_reached",
                          "spans": [], "human": [h for h in human if h["event"] in ("report_issued", "repair_recorded", "incident_closed", "question_asked")],
                          "reason": None if closed else "Closure is a human decision after the verifier reports.",
                          "guardrail": "Engine rule: report, repair record and closure require the engineer credential."})
        else:
            steps.append({"id": key_, "label": label, "status": "ran" if spans[key_] else "not_run", "spans": spans[key_],
                          "reason": None if spans[key_] else STEP_REASON.get(key_)})
    h = engine_health()
    return {"steps": steps, "span_count": n,
            "hardware": {"dell_edge": status()["dell_edge"], "location": f"{DEPLOYMENT} (not the Dell edge server)",
                         "nvidia_inference": nvidia_status(), "gpu": None,
                         "orchestrator": "Atelic deterministic orchestrator", "retriever": f"Qdrant ({h.get('documents')})",
                         "guardrails": "Engine boundary: read-only agent tools, citation checks, human gates",
                         "graph": h.get("graph"), "historian": h.get("historian_status")},
            "engine_incident_id": s["engine_id"], "status": orch.get("status"), "current_step": orch.get("current_step"),
            "safety": "No physical control commands are enabled."}


@app.get("/incidents/{incident_id}/agent-runs")
def get_agent_runs(incident_id: str):
    return agent_runs(slot_or_404(incident_id))


@app.get("/story/recorded-run")
def recorded_run():
    """Stage fallback: the last saved live run, labelled recorded, not live."""
    return engine.get("/story/recorded-run")
