"""Screen 5 draft work order, built by a fixed rule template from the incident's evidence.

No language model runs here. When NIM is connected, a Nemotron drafter replaces build_draft()
with the same response shape. Every claim cites evidence ids; a claim without citations must
be a hypothesis or a gap, never stated as fact.
"""
import hashlib
import json
import re
from pathlib import Path

GENERATOR = {
    "id": "rule_template",
    "version": "plan-template-v0",
    "model": None,
    "note": "Draft assembled by a fixed rule template from the cited sources. No language model was used.",
}


def parse_doc(path: Path, rel: str):
    """Markdown procedure -> (doc id, title, passages). A passage is one list item."""
    lines = path.read_text().splitlines()
    head = next((l[2:] for l in lines if l.startswith("# ")), path.stem)
    doc_id, _, title = head.partition(":")
    section, passages = "", []
    for n, line in enumerate(lines, 1):
        if line.startswith("## "):
            section = line[3:].strip()
        m = re.match(r"\s*(?:\d+\.|- \[ \]|-)\s+(.*)", line)
        if m:
            passages.append({"id": f"{doc_id}#{len(passages) + 1}", "section": section, "text": m.group(1).strip(),
                             "uri": f"{rel}#L{n}"})
    return doc_id.strip(), title.strip(), "\n".join(lines), passages


def version_hash(fields, sections):
    blob = json.dumps({"fields": fields, "sections": sections}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def build_draft(candidates, quant, detections, history, doc_paths):
    """Returns (draft, missing). draft is None when the minimum inputs are missing."""
    assessment = candidates.get("assessment", {})
    asset = assessment.get("asset_id") if assessment.get("result") == "likely_source" else None
    if not candidates.get("candidates"):
        return None, ["Source candidates (Locate stage)"]

    sample = candidates.get("status") == "sample"
    cand_prov = "simulated" if sample else candidates.get("provenance", "hypothesis")
    evidence, claims = {}, {}

    def ev(eid, label, kind, prov, source, excerpt=None, uri=None, group=None):
        # group = the source document the UI files this item under (one card per group).
        evidence[eid] = {"id": eid, "label": label, "kind": kind, "provenance": prov, "source": source,
                         "excerpt": excerpt, "uri": uri, "group": group or label}
        return eid

    def claim(section, text, prov, cites=()):
        cites = [c for c in cites if c in evidence]
        if not cites and prov not in ("hypothesis", "gap"):
            prov = "hypothesis"  # nothing to trace it to
        cid = f"C{sum(len(v) for v in claims.values()) + 1}"
        claims.setdefault(section, []).append({"id": cid, "text": text, "provenance": prov, "cites": cites})

    loc = ev("LOC", f"Locate candidate ranking ({candidates.get('method', 'unknown method')})", "candidates",
             cand_prov, "skid/candidates.json",
             "Ranked candidates: " + ", ".join(c["asset_id"] for c in candidates["candidates"]),
             group="Locate candidate ranking")
    top = next((c for c in candidates["candidates"] if c["asset_id"] == asset), None)

    # ----- What we observed -----
    meta, ann = detections["metadata"], detections["annotations"]
    if (ann.get("ranges") or ann.get("frames")) and ann.get("ogi_clip_id") == meta.get("ogi_clip_id"):
        reviewed = f"reviewed by {ann['reviewed_by']}" if ann.get("reviewed_by") else "not yet reviewed"
        det = ev("DET", f"Plume region on OGI clip {meta['ogi_clip_id']}", "detection", "manual_annotation",
                 "ogi/plume_annotations.json", f"Plume box drawn on the replayed clip ({reviewed}).")
        claim("observed", f"A gas plume region is marked on the {meta.get('ogi_clip_id')} OGI replay ({reviewed}).",
              "manual_annotation", [det])
    if top:
        claim("observed", f"Locate ranks {asset} first of {len(candidates['candidates'])} candidates.", cand_prov, [loc])
        for e in top.get("supporting", []):
            claim("observed", e["label"] + ".", cand_prov, [ev(e["id"], e["label"], "candidate_evidence",
                  cand_prov, "skid/candidates.json", f"Supports {asset} (source record {e['source_id']}).",
                  group="Locate candidate ranking")])
    rate = quant.get("rate_kg_ch4_h") if quant else None
    if rate is not None:
        iv = quant["interval"]
        q = ev("QUANT", f"Emission estimate ({quant['model_version']}, {quant['model_note']})", "quantification",
               "model_estimated", "POST /incidents/{id}/quantify",
               f"{rate} kg CH₄/h, P10–P90 {iv['low']}–{iv['high']} kg/h, run {quant['run_at_utc']}. "
               f"Inputs: {quant['input_provenance']} sensor traces.")
        claim("observed", f"Estimated release rate {rate} kg CH₄/h (P10–P90 {iv['low']}–{iv['high']}), "
              f"from simulated sensor traces.", "model_estimated", [q])

    # ----- What remains uncertain -----
    if asset:
        claim("uncertain", f"{asset} is not confirmed as the source in the field.", "hypothesis", [loc])
    else:
        claim("uncertain", "Locate did not settle on a likely source.", "gap", [loc])
    if top:
        for e in top.get("contradicting", []):
            claim("uncertain", e["label"] + ".", cand_prov, [ev(e["id"], e["label"], "candidate_evidence",
                  cand_prov, "skid/candidates.json", f"Contradicts {asset} (source record {e['source_id']}).",
                  group="Locate candidate ranking")])
        for m in top.get("missing", []):
            claim("uncertain", f"Missing: {m}.", "gap", [loc])
    others = [c["asset_id"] for c in candidates["candidates"] if c["asset_id"] != asset]
    if others:
        claim("uncertain", f"{', '.join(others)} remain open candidates.", "hypothesis", [loc])
    if rate is None:
        claim("uncertain", "Release rate not estimated (Quantify has no result).", "gap")
    else:
        claim("uncertain", f"The rate assumes the source is {quant['source_asset_id']} and its interval excludes "
              "stability-class and geometry error.", "model_estimated", ["QUANT"])

    # ----- Procedures: recommended inspection, isolation, safety -----
    docs = [parse_doc(p, rel) for p, rel in doc_paths]
    procedure = next((d for d in docs if asset and asset in d[2] and any(p["section"] == "Steps" for p in d[3])), None)
    checklist = next((d for d in docs if "- [ ]" in d[2]), None)

    def cite_passage(doc, p):
        return ev(p["id"], f"{doc[0]} · {p['section'] or doc[1]}", "procedure", "synthetic", p["uri"].split("#")[0],
                  p["text"], p["uri"], group=f"{doc[0]} · {doc[1]}")

    if procedure:
        for p in procedure[3]:
            if p["section"] == "Steps":
                text = p["text"]
                for r in history["records"]:  # pull the cited history record in as its own evidence
                    if r["record_id"] in text:
                        ev(r["record_id"], f"{r['record_id']} · {r['asset_id']} · {r['date']}", "maintenance",
                           history["provenance"], "maintenance/history.json", r["summary"], group="Maintenance history")
                cites = [cite_passage(procedure, p)] + [r["record_id"] for r in history["records"] if r["record_id"] in text]
                claim("inspection", text, "synthetic", cites)
    else:
        claim("inspection", f"No inspection procedure on file for {asset or 'the unresolved source'}.", "gap")
    for c in candidates["candidates"]:
        if c["asset_id"] != asset and c.get("missing"):
            claim("inspection", f"If no release is found at {asset}, check {c['asset_id']}: {c['missing'][0][:1].lower() + c['missing'][0][1:]}.",
                  "hypothesis", [loc])

    if checklist:
        for p in checklist[3]:
            claim("isolation", p["text"], "synthetic", [cite_passage(checklist, p)])
    else:
        claim("isolation", "No isolation checklist on file.", "gap")

    safety = [(procedure, p) for p in (procedure[3] if procedure else []) if p["section"] == "Permits"]
    safety += [(checklist, p) for p in (checklist[3] if checklist else []) if re.search(r"gas test|LEL", p["text"], re.I)]
    for d, p in safety:
        claim("safety", p["text"], "synthetic", [cite_passage(d, p)])
    if not safety:
        claim("safety", "No permit or gas-test requirement found in the procedures on file.", "gap")

    # ----- Work order fields (editable; Priority left for the engineer) -----
    problem = f"Possible methane release near {asset}. Source unconfirmed." if asset else "Possible methane release. Source unresolved."
    if rate is not None:
        problem += f" Model-estimated rate {rate} kg CH₄/h from simulated inputs."
    fields = {
        "asset": asset,
        "problem": problem,
        "proposed_inspection": (f"Inspect {asset} per {procedure[0]} ({procedure[1]})." if procedure
                                else "No procedure on file. Engineer to define."),
        "priority": None,
        "required_permits": [p["text"] for d, p in safety if p["section"] == "Permits"],
    }
    draft = {
        "assessment": (f"Possible release near {asset}. Physical confirmation is required." if asset
                       else "Source unresolved. Physical confirmation is required."),
        "sections": claims,
        "evidence": list(evidence.values()),
        "basis": {"quantification_run_at": quant.get("run_at_utc") if quant else None,
                  "candidates_status": candidates.get("status")},
    }
    return {"fields": fields, **draft}, []
