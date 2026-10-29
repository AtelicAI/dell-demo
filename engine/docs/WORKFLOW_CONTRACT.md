# Durable advisory workflow

`backend/app/workflow.py` is framework independent; parent owns HTTP integration,
PostgreSQL provisioning, dependencies, and startup. No equipment-control API exists.

Set `WORKFLOW_DATABASE_URL`, `ENGINEER_TOKEN` (at least 24 random characters), and
`ENGINEER_ID` on the server. Never expose these through browser configuration or logs.
Environment loading is the parent's responsibility. Run `scripts/init_workflow.py`
with the project interpreter after loading the environment. This creates schema
`atelic`, version-one tables and immutable-record triggers, then replaces demo
credentials with a SHA-256 token hash and engineer identity. Re-running initialization
is safe for this schema version; future incompatible schemas require explicit migrations.
The demo authenticator has one human credential; rotation invalidates the old token.
Use a high-entropy token because the stored hash is deliberately not a password KDF.

## Integration API

```python
from app.workflow import WorkflowStore, deterministic_plan, report_pdf, WorkflowError
store = WorkflowStore()  # WORKFLOW_DATABASE_URL; no implicit fallback
r = store.create_incident(incident_id, snapshot, evidence_references,
                          idempotency_key=request_key)
r = store.transition(incident_id, 'localized_unconfirmed',
                     expected_version=r['version'], idempotency_key=request_key)
# Then transition to quantified_or_unavailable using a new key/version.
r = store.save_draft(incident_id, deterministic_plan(text),
                     expected_version=r['version'], idempotency_key=request_key)
r = store.submit_review(incident_id, expected_version=r['version'],
                        idempotency_key=request_key)
r = store.review(incident_id, 'approve', rationale, r['draft_version'],
                 authorization=authorization_header,
                 expected_version=r['version'], idempotency_key=request_key)
r = store.create_report(incident_id, expected_version=r['version'],
                        idempotency_key=request_key)
pdf_bytes = report_pdf(r['report'])
```

Every separate mutation needs its own key. Mutations return the complete durable
incident with integer `version`; creation starts at 1. Reads: `get_incident(id)`,
`get_audit(id)`, `verify_audit(id)`. Map `WorkflowError.status_code` and `.reason`
to HTTP errors; never expose DB connection exceptions verbatim. Validate HTTP bodies
strictly and forbid extra identity/role fields. Pass the Authorization header only;
reviewer identity comes exclusively from the hashed credential table. Never give
agents the engineer token. This is a local human demo credential, not proof of human
presence; a production identity provider should replace it for stronger guarantees.

The lifecycle is observed → localized_unconfirmed → quantified_or_unavailable →
plan_drafted → pending_review → approved/rejected. Only approved can become reported.
Request changes returns plan_drafted; a revision increments draft_version and clears
the prior review. Rejected incidents are terminal. Direct transitions cannot cross
review or draft boundaries. Missing/invalid bearer credentials produce 401;
missing incidents 404; missing plan provenance and invalid reviews 422; stale versions,
invalid transitions, draft mismatches and idempotency mismatches 409 with explicit
reason strings. Authentication also runs on review retries.

Snapshot/evidence JSON is frozen at creation and copied into each immutable work
order. Supply complete serializable investigation records and stable evidence
references (including source/version/URI as appropriate); this module does not fetch
or verify upstream evidence. Drafts contain `advisory_only=true` and
`equipment_control=false`. JSON must be finite and serializable. Deterministic text
uses `source_origin=deterministic`, `generation_method=deterministic_template`, and
no model_run. Do not label it AI generated. Future genuine model output uses
`source_origin=llm` with model_run containing provider, model, run_id, generated_at.
Validation requires this provenance but cannot independently attest a provider ran;
only trusted server-side upstream code should supply model runs.

## Transactions and privileges

Mutations hold an incident-specific PostgreSQL transaction advisory lock plus an
existing row lock. Expected versions prevent lost updates. Request key, fingerprint,
response, incident mutation, immutable draft/review/report, and audit event commit
atomically. Same key and exact request (including expected version and authenticated
reviewer) replays the saved response. A changed request with the same key conflicts.
Locks also serialize racing incident creation; hash collisions merely serialize
unrelated operations. No global audit deletion or truncation is used.

Audit is per-incident sequence/hash chained over previous hash plus canonical event
UTF-8 JSON. Events contain actor, action, timestamp, request and resulting record.
UPDATE, DELETE, TRUNCATE on audit, drafts, reviews and reports are rejected by
statement triggers, even for empty tables. Verification detects chain inconsistency,
but cannot detect privileged replacement with a fully recomputed chain or removal
of a valid tail without an externally anchored head. This is not DBA tamper-proof.

Parent should use a separate schema-owner migration DSN and runtime application
role. Run initialization/provisioning with owner credentials; never initialize as
part of runtime startup. Grant runtime schema USAGE; SELECT/INSERT on audit,
drafts, reviews, reports, requests; SELECT/INSERT/UPDATE on incidents; SELECT on
credentials. Do not grant UPDATE/DELETE/TRUNCATE on immutable tables, schema CREATE,
role membership in the owner, or trigger management. Revoke PUBLIC schema CREATE
as part of deployment hardening. Runtime must not be table owner/superuser.
`configure_engineer` needs owner/provisioner DELETE/INSERT on credentials and must
not be exposed through an HTTP route. Trigger DDL deliberately belongs to initialize.

## Reports and verification

Reports are frozen structured JSON with approved review and exact work-order snapshot;
`report_pdf` optionally imports reportlab and returns PDF bytes. Parent must supply
only the stored issued report to this renderer and enforce authorization on endpoints.
A renderer alone does not assert authenticity of arbitrary caller-supplied JSON.

Run `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_workflow.py`.
Integration tests require explicit WORKFLOW_DATABASE_URL and use random isolated
`atelic_test_*` schemas, dropped afterward; they never reuse or erase `atelic` audit.
They cover concurrency, rollback on conflicts, authenticated reviews, revision freeze,
idempotency, restart persistence, rejection/report boundaries, and immutable triggers.


## Integrated review and inference reservation refinements
A request_changes review records revision_required_after; submit_review rejects the unchanged draft until save_draft creates a newer version. The operational draft_jobs table atomically reserves model attempts by incident/key and request fingerprint, with state/version validation before inference. Concurrent retries poll via HTTP 202; successful retries return the stored draft without another provider call. A failed or expired attempt requires an explicit new request key; provider output is never fabricated. Runtime gets SELECT/INSERT/UPDATE on draft_jobs, which is operational mutable state, not immutable evidence. Migration owner still initializes the schema; run provision_workflow_role.py after this migration. Citation checks now require executed-tool delivery, and the server freezes camera/detection references separately from asset-existence graph facts.
