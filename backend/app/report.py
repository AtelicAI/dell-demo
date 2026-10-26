"""Screen 7 incident report, built from the run's real state. The page renders this snapshot and the
JSON / Markdown exports are made from the same dict, so an export always matches what the UI shows.

Every row carries a provenance. A row with nothing behind it is a gap, never a guessed value.
Simulated / synthetic provenance is kept in the JSON export but, like the other screens, not shown
as a badge on the page or in the Markdown export.
"""
import hashlib
import json

FOOTER = "OGMP 2.0-aligned demonstration fields. Not a certified operator submission."
DECISIONS = {"approve": "Approved", "request_changes": "Changes requested", "reject": "Rejected"}
LABELS = {
    "observed": "Observed", "provider_reported": "Provider reported", "experimental_setpoint": "Experimental setpoint",
    "model_estimated": "Model estimated", "simulated": "", "synthetic": "",
    "manual_annotation": "Manual annotation", "hypothesis": "Hypothesis", "approved": "Approved",
    "human_decision": "Human decision", "system": "System status", "gap": "Gap",
}


class Gap(str):
    """A missing value: the row shows this reason instead of a value."""


def row(label, value, prov, cites=(), note=None):
    if isinstance(value, Gap):
        value, note = None, str(value)
    return {"label": label, "value": value, "provenance": "gap" if value is None else prov,
            "cites": [c for c in cites if c], "note": note}


def build_report(incident, src, now):
    """src: the data files and live status the report reads (see main.make_report)."""
    meta, ann, cands, site, plume = src["ogi_meta"], src["ogi_ann"], src["candidates"], src["site"], src["plume"]
    q, wo, audit = incident.get("quantification"), incident.get("work_order"), incident.get("audit", [])
    latest = wo["versions"][-1] if wo else None
    rv = src["review"]
    approved = bool(rv and rv["decision"] == "approve")
    sample = cands.get("status") == "sample"
    cand_prov = "simulated" if sample else cands.get("provenance", "hypothesis")

    evidence = {}

    def ev(eid, label, kind, prov, source, screen=None):
        evidence.setdefault(eid, {"id": eid, "label": label, "kind": kind, "provenance": prov, "source": source,
                                  "screen": screen})
        return eid

    # Draft evidence first (procedures, maintenance records, candidate evidence), then the report's own.
    for e in (latest["draft"]["evidence"] if latest else []):
        ev(e["id"], e["label"], e["kind"], e["provenance"], e.get("uri") or e["source"],
           {"detection": "detection.html", "candidates": "locate.html", "candidate_evidence": "locate.html",
            "quantification": "quantify.html", "procedure": None, "maintenance": "locate.html"}.get(e["kind"]))
    marked = bool(ann.get("ranges") or ann.get("frames")) and ann.get("ogi_clip_id") == meta.get("ogi_clip_id")
    E_META = ev("OGI-META", f"OGI clip metadata {meta.get('ogi_clip_id')}", "video", meta.get("provenance"),
                "ogi/source_metadata.json", "detection.html")
    E_DET = ev("DET", f"Plume region on OGI clip {meta.get('ogi_clip_id')}", "detection", "manual_annotation",
               "ogi/plume_annotations.json", "detection.html") if marked else None
    E_LOC = ev("LOC", f"Locate candidate ranking ({cands.get('method', 'unknown method')})", "candidates", cand_prov,
               "skid/candidates.json", "locate.html")
    E_SITE = ev("SITE", "Site definition", "scenario", site.get("provenance", "synthetic"),
                "scenario/site.json", None)
    if q:
        E_Q = ev("QUANT", f"Emission estimate ({q['model_version']})", "quantification", "model_estimated",
                 "POST /incidents/{id}/quantify", "quantify.html")
        E_SENS = ev("SENS", f"Sensor traces {q['detector_id']} + {q['wind_sensor_id']}", "sensor", "synthetic",
                    "scenario/sensors.csv, scenario/weather.csv", "quantify.html")
        E_GEOM = ev("GEOM", "Draft skid geometry", "scenario", "synthetic", "scenario/geometry.json", "quantify.html")
    E_PLUME = ev("SAT", f"Carbon Mapper observation {plume.get('satellite_observation_id')}", "satellite",
                 plume.get("provenance") if plume.get("status") == "verified" else "gap",
                 "carbon_mapper/verified_plume.json", "index.html")

    gaps = []

    def gap(text):
        gaps.append(text)
        return Gap(text)

    # ----- 1 Incident summary -----
    assessment = (latest["draft"]["assessment"] if latest else
                  f"Possible release near {cands['assessment']['asset_id']}. Physical confirmation is required."
                  if cands.get("assessment", {}).get("result") == "likely_source" else None)
    facility = ", ".join(x for x in (site.get("facility_name"), site.get("facility_location")) if x)
    summary = [
        row("Incident", incident["local_incident_id"], "synthetic", [E_SITE]),
        row("Site", f"{facility} · {site.get('display_name')} ({site.get('facility_id')})" if facility
            else f"{site.get('display_name')} ({site.get('facility_id')})", "synthetic", [E_SITE]),
        row("Camera", incident["camera_id"], "synthetic", [E_SITE]),
        row("Replay window", f"{site.get('replay_start_utc')} to {site.get('replay_end_utc')}", "synthetic", [E_SITE]),
        row("Source status", incident["source_status"], "hypothesis", [E_LOC]),
        row("Assessment", assessment, "hypothesis", [E_LOC]),
    ]

    # ----- 2 Camera observation -----
    seg = meta.get("source_segment") or {}
    camera = [
        row("Recording", f"{incident['camera_id']} infrared" + (f" · {facility}" if facility else ""), meta.get("provenance"),
            [E_META], f"Licence {meta.get('license_status', 'unknown')}"),
        row("Segment", f"{seg.get('start')}–{seg.get('end')} of the original, {meta.get('frame_count')} frames"
            if seg else Gap(f"{meta.get('frame_count')} frames; which part of the original recording it covers is not recorded."),
            meta.get("provenance"), [E_META]),
        row("Detection method", (meta.get("detection_mode") or "").replace("_", " ").capitalize() or None, "manual_annotation", [E_META]),
    ]
    if meta.get("plume_rendering") == "burned_in":
        camera.append(row("Plume rendering", "Atelic detection model · pre-rendered", "model_estimated", [E_META]))
    if marked:
        rs = ann.get("ranges") or []
        frames = f"frames {rs[0]['start_frame']}–{rs[-1]['end_frame']}, {len(rs)} range{'s' if len(rs) != 1 else ''}" if rs else f"{len(ann['frames'])} frames"
        camera.append(row("Plume region", f"Marked on {frames}", "manual_annotation", [E_DET],
                          f"Drawn by {ann.get('annotator') or 'unknown'}; "
                          + (f"reviewed by {ann['reviewed_by']}" if ann.get("reviewed_by") else "not yet reviewed")))
    else:
        camera.append(row("Plume region", gap("No plume region is marked on the CAM-01 recording."), None, [E_META]))
    sp = meta.get("experimental_setpoint") or {}
    camera.append(row("Release setpoint", f"{sp['rate']} {sp['unit']}" if sp.get("rate") is not None
                      else gap("Controlled-release setpoint for the CAM-01 recording is not on file."), "experimental_setpoint", [E_META]))

    # ----- 3 Source candidates -----
    candidates = [row("Method", cands.get("method"), cand_prov, [E_LOC])]
    for i, c in enumerate(cands.get("candidates", []), 1):
        candidates.append(row(f"Rank {i} · {c['asset_id']}", " ".join(c.get("rationale", [])) or c["status"], cand_prov,
                              [E_LOC] + [e["id"] for e in c.get("supporting", []) if e["id"] in evidence],
                              f"Status: {c['status']}" + (f". Missing: {'; '.join(c['missing'])}" if c.get("missing") else "")))
    if not cands.get("candidates"):
        candidates.append(row("Candidates", gap("No source candidates on file."), None))
    candidates.append(row("Field confirmation", gap("The source has not been confirmed in the field."), None, [E_LOC]))

    # ----- 4 Emissions information -----
    if q:
        iv, qc = q["interval"], q["qc"]
        emissions = [
            row("Gas", "CH₄", "model_estimated", [E_Q]),
            row("Emission rate", f"{q['rate_kg_ch4_h']} kg CH₄/h", "model_estimated", [E_Q, E_SENS],
                "Estimated from sensor traces"),
            row("Uncertainty", f"P10–P90 {iv['low']}–{iv['high']} kg CH₄/h", "model_estimated", [E_Q], iv.get("basis")),
            row("Method", f"{q['model_version']} ({q['model_note']})", "model_estimated", [E_Q]),
            row("Source assumed", q["source_asset_id"], "hypothesis", [E_Q, E_LOC]),
            row("Inputs", f"{q['detector_id']} methane, {q['wind_sensor_id']} wind; "
                f"{q['data_window']['start_utc']} to {q['data_window']['end_utc']}", "synthetic", [E_SENS, E_GEOM]),
            row("Samples used", f"{qc['samples_used']} of {qc['samples_in_window']}", "model_estimated", [E_Q]),
            row("Estimate run", q["run_at_utc"], "model_estimated", [E_Q]),
        ]
        emissions += [row("Assumption", a, "model_estimated", [E_Q]) for a in q.get("assumptions", [])]
    else:
        emissions = [row("Emission rate", gap("Emission rate not estimated (Quantify has no result)."), None)]
    emissions.append(row("OGMP 2.0 reporting level", gap("OGMP 2.0 reporting level not assessed (field schema pending)."), None))

    # ----- 5 Recommended action -----
    if latest:
        f, d = latest["fields"], latest["draft"]
        claims = [c for cs in d["sections"].values() for c in cs]
        draft_ids = [e["id"] for e in d["evidence"]][:1]
        action = [
            row("Work order", f"v{latest['version']} · {latest['hash']}", "synthetic", draft_ids,
                f"{src['generator']['id']} {src['generator']['version']}. No language model was used."),
            row("Status", "Approved" if approved else "Draft", "approved" if approved else "system"),
            row("Asset", f["asset"], "hypothesis", [E_LOC]),
            row("Problem", f["problem"], "synthetic"),
            row("Proposed inspection", f["proposed_inspection"], "synthetic"),
            row("Priority", f["priority"] or gap("Work order priority not set."), "human_decision"),
            row("Required permits", " ".join(f["required_permits"]) or Gap("No permits listed."), "synthetic"),
            row("Draft content", f"{sum(len(v) for k, v in d['sections'].items() if k in ('inspection', 'isolation', 'safety'))} "
                f"recommended steps, {len(d['evidence'])} cited sources", "synthetic",
                note=f"{sum(1 for c in claims if c['provenance'] in ('hypothesis', 'gap'))} lines are hypotheses or gaps"),
            row("Flagged claims", str(len(wo["flags"])), "human_decision"),
            row("Limitation", "No equipment action will be executed.", "system"),
        ]
    else:
        action = [row("Work order", gap("No work order has been drafted."), None)]

    # ----- 6 Engineer decision -----
    if rv:
        decision = [
            row("Decision", DECISIONS[rv["decision"]], "approved" if approved else "human_decision"),
            row("Reviewer", rv["reviewer"], "human_decision", note="Name entered at review. No sign-in in this demo."),
            row("Decided at", rv["at"], "human_decision"),
            row("Draft reviewed", f"v{rv['version']} · {rv['hash']}", "human_decision"),
        ]
        if rv.get("reason"):
            decision.append(row("Reason", rv["reason"], "human_decision"))
        if not approved:
            gap("The draft was returned; a rejected draft cannot close the incident.")
    elif wo and wo["submitted"]:
        decision = [row("Decision", gap(f"Engineer review is not complete. v{wo['submitted']['version']} is waiting for review."), None)]
    else:
        decision = [row("Decision", gap("Engineer review is not complete."), None)]

    # ----- 8 Data provenance and limitations -----
    datasets = [
        row("satellite_observation_id", plume.get("satellite_observation_id"), E_PLUME and evidence[E_PLUME]["provenance"],
            [E_PLUME], f"{plume.get('provider')} {plume.get('gas')} observation. Regional context only; not linked to this incident."),
        row("ogi_clip_id", meta.get("ogi_clip_id"), meta.get("provenance"), [E_META], "Source recording behind CAM-01."),
        row("local_incident_id", incident["local_incident_id"], "synthetic", [E_SITE], "Incident record for this investigation."),
    ]
    limits = [
        row("Dell edge", "Running" if src["status"]["dell_edge"] == "running" else "Target deployment (not running on the edge server)",
            "system"),
        row("NVIDIA inference", {"local_nim": "Local NIM", "hosted_api": "Hosted API"}.get(src["status"]["nvidia_inference"],
            "Not connected in this run"), "system"),
        row("Record keeping", "Incident state and the audit log are held in memory and reset when the backend restarts.", "system"),
    ]

    sections = [
        {"id": "summary", "title": "Incident summary", "rows": summary},
        {"id": "camera", "title": "Camera observation", "rows": camera},
        {"id": "candidates", "title": "Source candidates", "rows": candidates},
        {"id": "emissions", "title": "Emissions information", "rows": emissions},
        {"id": "action", "title": "Recommended action", "rows": action},
        {"id": "decision", "title": "Engineer decision", "rows": decision},
        {"id": "audit", "title": "Audit timeline", "rows": []},
        {"id": "provenance", "title": "Data provenance and limitations", "rows": datasets + limits},
    ]
    # Number sources in reading order: first cited first, uncited ones last.
    order = {}
    for s in sections:
        for rw in s["rows"]:
            for c in rw["cites"]:
                order.setdefault(c, len(order))
    body = {
        "local_incident_id": incident["local_incident_id"],
        "status": "final" if approved else "draft",
        "status_text": "Final report. Work order approved by a named engineer." if approved
                       else "Draft report. Engineer review is not complete.",
        "sections": sections,
        "audit": audit,
        "evidence": sorted(evidence.values(), key=lambda e: order.get(e["id"], len(order))),
        "gaps": gaps,
        "footer": FOOTER,
    }
    # The hash covers the content, not the generation time or the export events themselves, so the
    # same state gives the same hash before and after exporting it.
    hashed = {**body, "audit": [e for e in audit if e["event"] != "report_exported"]}
    body["report_hash"] = hashlib.sha256(json.dumps(hashed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    body["generated_at"] = now
    return body


def to_markdown(r):
    out = [f"# Incident report: {r['local_incident_id']}", "",
           f"**{r['status_text']}**", "", f"Report hash `{r['report_hash']}` · generated {r['generated_at']}", ""]
    ev_no = {e["id"]: i for i, e in enumerate(r["evidence"], 1)}
    for i, s in enumerate(r["sections"], 1):
        out += [f"## {i}. {s['title']}", ""]
        if s["id"] == "audit":
            if not r["audit"]:
                out += ["No events recorded since the backend started.", ""]
            for e in r["audit"]:
                ver = f" (v{e['version']})" if e.get("version") else ""
                out.append(f"- `{e['at']}` **{e['event'].replace('_', ' ')}**{ver}: {e['detail']} (by {e['actor']})")
            out.append("")
            continue
        out += ["| Field | Value | Provenance | Sources |", "|---|---|---|---|"]
        for rw in s["rows"]:
            val = rw["value"] if rw["value"] is not None else "Not available"
            if rw.get("note") and rw["value"] is None:
                val = f"Not available: {rw['note']}"
            elif rw.get("note"):
                val += f" ({rw['note']})"
            cites = ", ".join(f"[{ev_no[c]}]" for c in rw["cites"] if c in ev_no)
            out.append(f"| {rw['label']} | {val.replace('|', '/')} | {LABELS.get(rw['provenance'], rw['provenance'] or '')} | {cites} |")
        out.append("")
    if r["gaps"]:
        out += ["## Gaps", ""] + [f"- {g}" for g in r["gaps"]] + [""]
    out += ["## Source evidence", "", "| # | Source | Kind | Provenance | Location |", "|---|---|---|---|---|"]
    for e in r["evidence"]:
        out.append(f"| {ev_no[e['id']]} | {e['label'].replace('|', '/')} | {e['kind']} | "
                   f"{LABELS.get(e['provenance'], e['provenance'] or '')} | `{e['source']}` |")
    out += ["", f"_{r['footer']}_", ""]
    return "\n".join(out)
