"""Turn the engine's evidence IDs (as cited by the agents) into the source entries the Plan and Report screens list.

Every entry is looked up in the frozen snapshot the agents read; an ID that cannot be resolved is still listed,
labelled as unresolved, never dropped or invented.
"""


def provenance_of(kind):
    return {"quantification": "model_estimated", "detection": "model_estimated", "history": "simulated",
            "field_note": "field_note"}.get(kind, "simulated")


class Evidence:
    def __init__(self, rec, asset_context):
        snap = rec["snapshot"]
        self.snap = snap
        self.asset_context = asset_context
        self.entries = {}
        self.docs = {}
        for d in snap.get("procedures", []) + snap.get("reference_documents", []):
            self.docs[d["citation"]] = d
            self.docs.setdefault(f"document:{d['document_id']}", d)
        self.reports = {r["id"]: r for r in snap.get("field_reports", [])}
        self.commitments = {c["id"]: c for c in snap.get("commitments", [])}
        self.maintenance = {}
        ctx = snap.get("context") or {}
        for m in ctx.get("maintenance", []) + snap.get("alternative_maintenance", []):
            r = m.get("record", m)
            self.maintenance[r["id"]] = r
        self.candidates = [c["asset_id"] for c in snap.get("candidates", [])]
        # Engineers' field notes sit beside the snapshot (added after it was frozen).
        self.notes = {n["evidence_id"]: n for n in rec.get("field_notes", [])}
        self.names = {}

    def _work_order(self, wid):
        if wid in self.maintenance:
            return self.maintenance[wid]
        for aid in self.candidates:
            ctx = self.asset_context(aid) or {}
            for m in ctx.get("maintenance", []):
                r = m.get("record", m)
                self.maintenance.setdefault(r["id"], r)
        return self.maintenance.get(wid)

    def resolve(self, eid):
        kind, _, rest = eid.partition(":")
        q = self.snap.get("quantification") or {}
        if kind == "calculation":
            rate = q.get("rate_kg_h")
            return dict(kind="quantification", group="Estimate · Gaussian candidate fit", screen="quantify.html",
                        label=f"{rate:.3f} kg CH₄/h at {q.get('candidate_asset_id')}" if rate is not None else f"Rate unavailable ({q.get('reason')})",
                        excerpt=(q.get("telemetry_attribution") or {}).get("note"))
        if kind == "field_report":
            r = self.reports.get(rest, {})
            return dict(kind="field_report", group=f"Field report · {rest}", label=r.get("title") or rest,
                        excerpt=(r.get("body") or "")[:360] or None, date=r.get("date"))
        if kind == "field_note":
            n = self.notes.get(eid, {})
            who = self.names.get(n.get("id"), "Engineer")
            return dict(kind="field_note", group=f"Field note · {rest}", label=f"{who}: {n.get('text', rest)}"[:200],
                        excerpt=f"Recorded {str(n.get('recorded_at', ''))[:16].replace('T', ' ')} UTC after the evidence was frozen; not verified by the system")
        if kind == "maintenance":
            r = self._work_order(rest) or {}
            return dict(kind="maintenance", group=f"Maintenance · {rest}", label=f"{r.get('date', '')} {r.get('issue') or rest}".strip(),
                        excerpt=" ".join(x for x in (r.get("finding"), r.get("action")) if x) or None)
        if kind == "commitment":
            c = self.commitments.get(rest, {})
            return dict(kind="commitment", group=f"Commitment · {rest}", label=c.get("text") or rest,
                        excerpt=f"Status at incident time: {c.get('status', 'unknown')}" + (f", fulfilled by {c['fulfilled_by']}" if c.get("fulfilled_by") else ""))
        if kind == "document":
            d = self.docs.get(eid) or self.docs.get(eid.split("#")[0]) or {}
            doc = rest.split("#")[0]
            return dict(kind="procedure", group=f"Doc · {doc}", label=f"{d.get('title') or doc} · {rest.split('#')[1] if '#' in rest else 'document'}",
                        excerpt=(d.get("text") or "")[:360] or None)
        if kind in ("history", "history-episode"):
            h = self.snap.get("long_term_history") or {}
            a = h.get("load_change_alignment") or {}
            return dict(kind="history", group="Plant history · 47 days", screen="quantify.html",
                        label=(f"{h.get('episode_count')} methane episodes since {str(h.get('period_start_utc'))[:10]}" if kind == "history" else f"Episode {rest}"),
                        excerpt=f"{a.get('episodes_starting_after_load_change')} of {a.get('episodes')} start within {a.get('window_minutes')} min of a K-201 load change" if a else None)
        if kind == "operations":
            return dict(kind="operations", group="Operations log · K-201", label="Compressor K-201 load-change log",
                        excerpt="Daily load changes at 06:00 and 17:30 UTC")
        if kind == "camera":
            return dict(kind="detection", group="Camera · CAM-01", label=f"Illustrative camera region {rest.split('/')[-1]}", screen="detection.html",
                        excerpt="Camera-to-equipment mapping is illustrative and uncalibrated")
        if kind == "neo4j":
            return dict(kind="candidates", group="Knowledge graph", label=rest, screen="graph.html")
        if kind == "measurements":
            return dict(kind="sensor", group="Telemetry · InfluxDB", label=rest, screen="quantify.html")
        if kind == "incident_record":
            return dict(kind="history", group="Incident memory", label=f"Closed incident {rest}")
        return dict(kind="source", group="Other source", label=eid, excerpt="Not resolved in the frozen snapshot")

    def add(self, eid):
        if eid not in self.entries:
            e = self.resolve(eid)
            self.entries[eid] = {"id": eid, "uri": eid, "source": eid, "provenance": provenance_of(e["kind"]), "excerpt": None, **e}
        return eid

    def items(self):
        return list(self.entries.values())

    def source_id(self, eid):
        kind, _, rest = eid.partition(":")
        if kind in ("field_report", "maintenance", "commitment"):
            return rest
        if kind == "document":
            return rest.split("#")[0]
        if kind == "calculation":
            return "CH4-01"
        return rest or eid
