# Backend handoff

Frontend ownership remains with the teammate. No frontend files changed in this milestone. Start the backend using README.md; local API is http://127.0.0.1:8000 and interactive docs are /docs. Exported OpenAPI: contracts/openapi.json. A frontend dev proxy can forward a same-origin /api prefix to this backend; production CORS is deliberately not wide open.

## Read-only investigation

- GET /health: actual graph/historian/document/workflow service status; model configuration is distinct from verified execution; Dell always unverified.
- GET /facility and /facility/process-model: canonical geometry and assumed-input solved process outputs.
- GET /incidents/INC-001?as_of_seconds=760: synthetic replay projection, not persisted workflow state.
- GET /incidents/INC-001/investigation?as_of_seconds=760&asset_id=XV-2104: candidates, real graph context, selected asset, real InfluxDB sensor history and actual deterministic tool timings. Fields origin_type/backend/clock must remain visible. A replay projection is distinct from durable workflow state.
- GET /incidents/INC-001/measurements?as_of_seconds=760&window_seconds=300: inclusive end time, null missing samples, QC, units and canonical observation IDs. Never convert null to zero or carry old charts across asset/scenario switches. Replay is bounded to 0..1199 seconds before review.
- GET /assets/XV-2104/documents?query=inspection: Qdrant retrieval with citations, synthetic origin, operational_approval=false and lexical score, not confidence.
- GET /datasets: summary/source registry; complete per-file inventory lives in data/downloaded_manifest.json.

## Recorded source access

GET /videos lists original clip metadata; GET /videos/MOV_1242/content streams local original bytes with Range support (206/416); GET /videos/MOV_1242 returns pending annotations, checksum and playback URL. Original codec is MPEG-4 Part 2 for these files; browser compatibility/transcoding has not been validated. Do not assume nominal frame rate is constant; use presentation times. No generated video or detector output is substituted.

GET /recorded-replays exposes separate SCN-004/INC-004 preview metadata and a verified grayscale-scene observation only. Those IDs are not added to /incidents or historian, because gas/source labels remain unverified. GET /videos/MOV_1242/clock?scenario_id=SCN-004&scenario_seconds=185 maps to 185 clip seconds, explicitly illustrative and not a shared capture timestamp. No synthetic incident clock is represented as the recording's real acquisition time. All gas annotations remain pending. A pending label cannot produce a confirmed detection/source/overlaid polygon.

## Calculate and freeze evidence

POST /incidents/INC-001/quantify JSON {"candidate_asset_id":"XV-2104","as_of_seconds":760}. Candidate must have camera support at that clock. Returns status estimated or rate_unavailable, method/version, kg/h, reason, input IDs, assumptions and sensitivity_kg_h. Sensitivity is not a confidence interval. This is a same-family synthetic demonstration, not independently field validated.

POST /incidents/INC-001/workflow/open JSON {"candidate_asset_id":"XV-2104","as_of_seconds":760,"idempotency_key":"open-001"}. Fetches actual graph, InfluxDB, Qdrant and calculation results, freezes them in PostgreSQL and advances through observed/localized_unconfirmed to quantified_or_unavailable (version 3). Later replay scrubbing cannot change this snapshot. Reopening a different window under the same incident/key is a conflict; use an explicit new incident lifecycle rather than silently replacing evidence. GET /incidents/INC-001/workflow reads the durable record. This endpoint does not approve anything.

## Draft, review, report

Use the returned version for every mutation. Each separate mutation needs a new idempotency_key; identical retries reuse the same key and original expected_version. Conflicts return 409 rather than overwriting another review.

1. POST /incidents/INC-001/draft-plan JSON {"expected_version":3,"idempotency_key":"draft-001","mode":"model"}. Requires actual provider credentials/model and successful bounded tool calls. Missing provider returns 503 and saves no draft. Explicit mode deterministic builds a cited template with source_origin=deterministic, never an AI result. Call this only after workflow/open. The frozen snapshot is the tool scope. Concurrent same-key model requests return 202 in_progress while one reserved attempt runs; retry the same body/key to retrieve the saved response. Failed/interrupted reserved attempts require a new key for an intentional retry. Citations must belong to evidence actually delivered by executed tools, not merely exist in the snapshot.
2. POST /incidents/INC-001/submit-review JSON {"expected_version":4,"idempotency_key":"submit-001"}.
3. POST /incidents/INC-001/review JSON {"expected_version":5,"idempotency_key":"review-001","draft_version":1,"decision":"approve","rationale":"Named engineer review rationale"}, with Authorization: Bearer <server-provisioned human demo credential>. Also supports reject/request_changes. Body identity or role fields are forbidden; identity comes from verified credentials. A model tool never receives the human token. No physical action exists.
4. POST /incidents/INC-001/report JSON {"expected_version":6,"idempotency_key":"report-001"}, authenticated as above. Requires approved state. GET /incidents/INC-001/report (JSON) or ?format=pdf exports the frozen issued report. Rejected/pending incidents cannot issue reports.
5. GET /incidents/INC-001/audit returns chain-verified durable events and its explicit tamper limitations.
6. GET /incidents/INC-001/recovery-preview?as_of_seconds=1260 requires human credentials and approved/reported state. It exposes only precomputed synthetic recovery, never a claim that a real repair/action occurred.

Versions above illustrate a fresh incident without revisions. Request changes returns plan_drafted and requires a newer draft before resubmission; a new draft increments draft_version and clears the old review. Rejected incidents are terminal in this implementation. User-supplied arbitrary work-order text/review identity/model_run data is not accepted through these routes.

## Failure/UI rules

404 unknown canonical source/asset/incident; 422 invalid body/reference/clock/unsupported source candidate; 401 invalid human credential; 409 stale version/key/lifecycle conflict; 503 unavailable graph/historian/retrieval/workflow/model. Never present a failed result as an empty successful investigation or invented zero emissions. Preserve alternatives, synthetic/measured/recorded distinctions and unconfirmed attribution. Authenticate at a production gateway/identity provider before any non-local deployment; current services bind loopback.

No genuine model invocation or Dell deployment has been verified. No reviewed recorded-video gas event or independent measured-data inversion has passed acceptance yet.


## Executed pretrained plume detection (new)

The frontend remains teammate-owned. Original footage can now be displayed with actual executed model masks:

- GET /videos/MOV_1242/playback: H264 display copy,320x240, with every source frame PTS preserved. Original inference still uses the untouched source file.
- GET /videos/MOV_1242/detections?run_id=gasvid-reference-threshold-001:60 actual samples at1Hz covering185..245source seconds. Source SHA, model revisions/threshold/runtime and all positive/no-alert frames are included. This is executed inference replay, not live-camera tracking or ground-truth labels.
- Each prediction includes pts_seconds, zero-based decoded frame_id, prediction0/1, uncalibrated score, normalized_boxes, plume_extent_polygons, mask_file/overlay_file/input_file/difference_file, latency_ms and null source_origin_polygon/asset_id. Do not use a drifting plume extent as the origin or convert it into a synthetic valve attribution.
- GET /detection/runs/<run-id>/assets/masks/<filename> returns a raw0/255 PNG at source dimensions. overlays returns a colored diagnostic. Map coordinates to the actual video image area, preserving4:3 aspect/letterboxing. Use video.currentTime and causal prior sample PTS, show prediction age, and never imply unsampled frames were inferred.
- POST /videos/MOV_1242/detect with JSON {"start_seconds":210,"end_seconds":225,"sample_interval_seconds":5} starts actual offline-cached model execution, returns202/status_url. Poll GET /detection/runs/<run-id>; unavailable environment returns503, active job409, invalid bounds422, failed worker is explicit. A request covers at most60seconds with sample interval1..30seconds. Threshold0.06 is fixed from upstream GasVid code; no client prompt/code/path injection.
- GET /detection/runs/simgas-diagnostic-002 exposes the separate80-frame synthetic panel diagnostic. The40test frames from2video IDs gave82.5% frame accuracy,74.1% recall,71.8% pooled pixel IoU. This is not real-GasVid accuracy; never label it that in the UI. Read DETECTION_REPORT.md for misses, sample size, ROI, source-family and access limitations.

The source metadata endpoint still correctly says annotations pending: an executed model is not an independent reviewer. The canonical incident workflow remains the separate synthetic facility scenario until explicit source/camera mappings and real-video labels exist. NVIDIA LLM access and Dell deployment remain unverified. Median local diagnostic inference was about 0.62 seconds/frame, not full15fps live inference.

## Source-time mask freshness (additive API)

`GET /videos/MOV_1242/detection-at?run_id=gasvid-reference-threshold-001&source_seconds=200.1&max_age_seconds=0.25`

Use source-video seconds, not incident replay seconds or wall-clock time. Render `prediction` only when `status=fresh`; stale/not_analyzed/outside_run return `prediction=null` and mean unknown. `prediction_age_seconds` is source-time age, not live compute latency. Exact model timestamp and source binding are retained. Max age is a display policy in(0,1]seconds, default0.25;1Hz runs intentionally have gaps. Never interpret no-alert as no gas. Existing `/detections` full replay response is unchanged. The MP4 artifact still holds masks; the teammate-owned UI must opt into this endpoint to apply expiry. No UI changes were made.

## Nemotron model draft (configured)
LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b is set server-side, so POST /incidents/{id}/draft-plan with mode "model" now performs genuine hosted NVIDIA inference (about 1–3.5 min end to end on the hosted endpoint; show a pending state and retry the same key on 202). Display `work_order.plan.model_run` and `evidence_backed_draft.activity` in the Agent Activity drawer; label deployment "hosted_endpoint", never Dell/on-prem. /health reports nvidia=configured_not_verified by design; the verified run is recorded under docs/evidence/nemotron/. Re-verify with `.venv/bin/python scripts/nemotron_smoke.py`.

## Monitor → Detect → full flow (backend contract for the UI)

The demo now starts in a monitoring state on recorded footage and flows into the incident stages. All of it is backend; the UI only plays the video and polls.

1. `GET /monitor` – config: `playback_url` (`/videos/MOV_1242/playback`), monitored clip interval 125–305 s, model metadata, labels. Start the H264 player at `clip_start_seconds` (125 s, GasVid class 0: a metered 5.3 g/h release below camera detection).
2. `GET /monitor/status?clip_seconds=<video.currentTime>` – poll about once per second. Uses only predictions at or before that time. `state`: `monitoring_no_alert` → `possible_plume` (some positives, policy not met) → `alert` (≥3 positives in last 5 one-second samples; latched). `latest_sample` gives `mask_url`/`overlay_url` for a fresh sample only. Before alert: show “No plume detected in this interval.” Never present no-alert as proof of no gas.
3. On `alert`: `POST /monitor/incidents` → `{incident_id: "INC-001-M###", event, investigation_url}`. Each demo run gets a fresh durable incident ID, so the primary INC-001 audit is never consumed. Show “Methane plume observed by CAM-01” and the button “Investigate possible source”.
4. Locate: `GET /incidents/{id}/investigation?as_of_seconds=<alert.scenario_seconds>` – candidates XV-2104/F-205 (unconfirmed), graph context with the enriched maintenance history, sensor trends. Before the alert time the same call returns `no_observation`.
5. Quantify: `POST /incidents/{id}/quantify {candidate_asset_id, as_of_seconds}`. Right after onset it returns `rate_unavailable / non_steady_plume`; advance the replay clock (e.g. +10 s) until `estimated` (≈670 s, 0.26 kg/h; ≈0.279 kg/h by 850 s against the metered 0.2777). Show the reason, not zero.
6. `POST /incidents/{id}/workflow/open` at that clock, then draft-plan (mode `model` = Nemotron, ≈1–2.5 min; show progress, retry same key on 202), submit-review, authenticated review, report — unchanged from the sections above.

Clock mapping (illustrative, shown in `clock.video_clock_mapping`): clip 185 s ↔ scenario 600 s. Video is recorded METEC footage of a different scene; the origin region used for candidates is the illustrative synthetic mapping, never the drifting plume mask. `scripts/monitor_flow_smoke.py` runs this whole sequence against real services in a disposable schema.

## Server-side orchestrator (supersedes client-driven steps 4–6 of the monitor flow above)

`POST /monitor/incidents` now registers the incident **and starts the deterministic orchestrator** (body optional: `{"plan_mode":"model"|"deterministic","plan_fallback":"none"|"deterministic","orchestrate":true}`). The UI no longer sequences stages:

1. Poll `GET /incidents/{id}/orchestration` (~1 s). `status`: `running` → `awaiting_human_review` | `failed` | `interrupted`; `current_step` ∈ detect, locate, quantify, freeze, plan, submit. `events` is the ordered, append-only trace for the Agent Activity drawer / booth Screen B: `{sequence, recorded_at, step, kind, detail}` with kinds `started`, `completed`, `failed`, `tool_call` (Neo4j/InfluxDB during Locate), `attempt` (each Quantify clock step), `agent_activity` (every Nemotron turn and tool call, streamed live), `attempt_failed`, `fallback`.
2. Sequence: Detect (model alert) → Locate (top illustrative-overlap candidate; alternatives kept) → Quantify (+10 s replay steps while `non_steady_plume` or the location comparison is undecided, max 300 s; other refusals are final and the flow continues with rate unavailable) → Freeze (workflow/open) → Plan (Nemotron, 2 attempts) → Submit → stops at `pending_review`.
3. Human step unchanged: `POST /incidents/{id}/review` with the engineer bearer credential, then report. The orchestrator never reviews, approves, reports or acts on equipment.
4. Failure: `failed` with `result.detail`; nothing is fabricated. Restart with `POST /incidents/{id}/orchestration` (same options or `{"plan_fallback":"deterministic"}`); completed steps replay idempotently. Deterministic fallback is labeled `source_origin=deterministic`, never AI. A run is reported `interrupted` after 15 min without progress (e.g. backend restart) and can be restarted.
5. Plan rule: a Nemotron draft is accepted only after all six evidence tools ran (one nudge, then rejection), on top of contract/citation validation with one repair turn.

`POST /incidents/{id}/orchestration` also works for fixture incidents (e.g. INC-001) but consumes that incident's durable lifecycle. Schema migration: owner `initialize()` adds `orchestration_runs` (mutable status) and `orchestration_events` (append-only, trigger-protected); re-run `scripts/provision_workflow_role.py` for runtime grants.

## Full story: investigation agent, engineer Q&A, revision, repair verification, closure (additive)

The orchestrator now continues past the first review. Every human action below needs the engineer bearer credential; each one is logged in the trace (`step: "human"`) and wakes the agent work it unblocks. The run `status` tells the UI who acts next:

| `status` | Workflow state | Who acts next |
|---|---|---|
| `running` | any | agent (show `current_step` and the streaming trace) |
| `awaiting_human_review` | `pending_review` | engineer: review, or ask a question |
| `awaiting_report` | `approved` | engineer: `POST /incidents/{id}/report` |
| `awaiting_repair_record` | `reported` / `follow_up_required` | field crew works; engineer records it |
| `awaiting_closure` | `verification_ready` | engineer: close or keep open, or ask a question |
| `closed` / `rejected` / `failed` | – | nobody / restart a failed run |

Steps: `detect → locate → quantify → freeze → investigate → plan → submit`, then follow-ups `answer`, `revise` (+ `submit`), `verify`.

- **Investigate (new agent step).** Nemotron reads all six evidence tools and returns `record.investigation.findings.content`: `summary`, `findings[{kind: supporting|contradiction|gap|context, statement, evidence_ids}]`, `candidate_assessment[{asset_id, assessment, evidence_ids}]`, `open_questions`. Show contradictions/gaps prominently: this is the WO-008/WO-009 "aha". The planner then drafts from these findings (+ candidates, procedures, emissions). Manual route: `POST /incidents/{id}/investigate {mode, expected_version, idempotency_key}`.
- **Ask for more information.** `POST /incidents/{id}/questions {question, expected_version, idempotency_key}` (allowed in `pending_review` and `verification_ready`). The answer agent replies from frozen evidence only and never changes the draft: `record.questions[i] = {id, question, asked_by, status: pending|answered, answer.answer.content: {answer, claims[], cannot_determine_from_evidence, suggested_follow_up[]}}`. Re-fetch the record before reviewing: an answer bumps `version`.
- **Request changes.** Existing `POST /incidents/{id}/review` with `decision: "request_changes"` and the feedback as `rationale`. The orchestrator runs `revise`: the planner gets a `get_review_feedback` tool (feedback, previous draft, answered questions), saves draft v2 with `work_order.plan.revision_of`, and resubmits. Show the v1/v2 diff.
- **Repair record → verify.** After approval (report optional), `POST /incidents/{id}/repair {work_performed, expected_version, idempotency_key}` is a human's account of field work; the system performs no physical action. The verifier compares simulated post-repair readings (scenario 1260–1500 s) with the pre-alert baseline: `record.verification = {recovery_check: {status: background_restored|residual_signal|insufficient_data, sensors[], post_repair_rate}, recommendation: {content: {recommendation: close|keep_open, summary, claims, residual_risks}, source_origin}, consistency}`. `consistency=conflicts_with_deterministic_check` means the agent disagreed with physics; surface it.
- **Close.** `POST /incidents/{id}/close {decision: close|keep_open, rationale, expected_version, idempotency_key}`. `keep_open` → `follow_up_required` (record another repair).
- **Report** now includes `investigation` and `questions`; `GET /incidents/{id}/report` keeps working after repair/closure.

Monitoring profiles (`GET /monitor/profiles`; add `?profile=` to `/monitor` and `/monitor/status`, `profile` to the `POST /monitor/incidents` body; default `leak` is unchanged):
- `leak` – SCN-001, as before.
- `no_leak` – SCN-002 control on clip 20–184 s (executed run `monitor-mov1242-020-184-001`, 164 samples, zero positives). Never alerts; registering returns 409.
- `wind_outage` – same footage as `leak`, SCN-003 telemetry with wind missing from 600 s. Quantify ends `rate_unavailable / missing_wind_or_methane`; the plan proceeds without a rate. Incidents `INC-003-M###`.

Recorded run: `GET /story/recorded-run` serves the last saved live story (`scripts/story_run.py --save`), labeled `playback: recorded_not_live`. Use it only as a stage fallback and keep the label visible. See `docs/STORY_RUNBOOK.md` for the beat-by-beat script.

Migration: owner `initialize()` adds the append-only `incident_artifacts` table (investigations, questions, answers, repair records, verifications, closures); re-run `scripts/provision_workflow_role.py`.

## Quantify: labeled location fits and camera agreement (additive)

`POST /incidents/{id}/quantify` (and the frozen `snapshot.quantification`) now labels every assumed source location in `candidate_fits[].fit_status`: `best_fit`, `comparable_fit`, `poor_fit`, `cannot_test`, or `not_assessed` when the window itself is unusable. Only best/comparable fits carry `rate_kg_h`; never display a rate for the others. New `telemetry_attribution`: `{status: agrees_single|agrees_ambiguous|partly_agrees|disagrees|no_consistent_location|not_assessed, best_fit_asset_id, camera_candidates[{candidate_asset_id, fit_status, rate_kg_h, residual_rms_ppm, error_ratio_to_best}], consistent_outside_camera_view[], note}`. Show it as "Sensors agree with the camera: XV-2104 explains the readings; F-205 fits about 5x worse". Requesting a contradicted candidate (e.g. F-205 at 850 s) returns `rate_unavailable / candidate_inconsistent_with_readings`. Orchestrator `quantify` attempt events now include `attribution` and `best_fit_asset_id`, and the step continues until the comparison is decisive (≈850 s in SCN-001 at the camera-matched 277.7 g/h: until then pipe P-202, just downwind and outside the camera view, also fits).

## One coherent story: camera-matched rate, plant history, answer key (additive)

The synthetic plant now releases the **metered rates behind the recorded footage** (GasVid MOV_1242, Wang et al. 2020 Table 1): 5.3 g/h (class 0, below camera detection) before 600 s and 277.7 g/h (class 1) from 600 s, at XV-2104. SCN-002 (the `no_leak` profile, now titled "Control: release below camera detection") releases 5.3 g/h throughout. Profile IDs are unchanged.

- `GET /monitor/footage?profile=` – what the camera recorded: METEC site, 2017-07-13, FLIR GF-320 at 4.6 m, separator on pad 1 (inferred), per-segment metered rate (`segments[]`), citation, and `segment_timing_basis` (the 185 s boundary is an unverified workbook reading). Not agent evidence.
- `GET /monitor/status` adds `metered_release` `{class, metered_rate_g_h, ci95_g_h, label}` for the clip second: show "On camera: METEC metered release 277.7 g/h" next to the feed.
- `GET /incidents/{id}/plant-history?include_series=true` – 47 days of 5-minute plant history before the incident (cutoff = scenario start): `analysis` (episodes with evidence IDs, `episodes_by_week`, `episode_start_hour_utc`, `quiet_stretches` with observability, `location_consistency`, `observability`) and `series.points[]` (hourly max ppm above shared background per sensor) for a chart. Synthetic; wind and background variability derived from measured METEC 2024 data.
- The frozen snapshot holds `long_term_history`; agents read it through the new tool `get_long_term_history` (cited as `history:…` / `history-episode:EP-…`). The investigator must call it; the deterministic findings template adds one history finding.
- `GET /incidents/{id}/reveal` – answer key, **409 until the report is issued** (states reported → closed) so it cannot anchor review: footage metered rate, frozen estimate, `error_percent`, attribution. Never an agent input.
- Orchestrator quantify wait default is now 300 s (`quantify_max_wait_seconds`). A final refusal (e.g. wind lost) is no longer replaced by an earlier window's estimate.

## Knowledge graph: field reports, commitments, operations, history, incident memory (additive)

The plant graph grew from 50 nodes / 115 edges to **230 / 691** (`GET /graph/summary`; the separate METEC benchmark graph is excluded). New node labels: `FieldReport` (7), `Commitment` (6), `LoadChange` (95), `SensorEpisode` (64), and `IncidentRecord` (written at closure). New edges: `DOCUMENTS`, `ON_ASSET` (reports, load changes), `COMMITTED_TO`, `FULFILLED_BY`, `FOLLOWS_UP`, `REVIEWS`, `REPEATS_SURVEY_OF`, `SAME_EQUIPMENT_AS`, `DETECTED_BY`, `CONSISTENT_WITH`, `AFTER_LOAD_CHANGE`, `CONCERNS`, `CONSIDERED`.

- `GET /assets/{id}/context` (and the frozen snapshot) now adds `field_reports`, `commitments` (with `status` as of the replay time: a follow-up done later shows as `open`), `work_order_links` and `prior_incidents` (only incidents that occurred before the replay time).
- Snapshot adds the union over camera candidates: `field_reports`, `commitments`, `work_order_links`, `prior_incidents`, each with an `evidence_id` (`field_report:FR-008`, `commitment:WO-008-C2`, `incident_record:…`). The `search_maintenance` agent tool returns them.
- Plant history adds `load_change_alignment` (57 of 64 episodes start within 90 min after a K-201 load change, against 12.5% expected by chance) and `minutes_after_load_change` per episode; evidence `operations:K-201:load-change-log`.
- `POST /incidents/{id}/close` with `decision=close` returns `graph_memory` `{status: written|failed, node}`: the incident (asset, candidates, estimate, approved checks, repair, verification, closure) becomes an `IncidentRecord`. A failed write never blocks closure. `GET /assets/{id}/incident-memory` lists them for people (unfiltered). Memory survives graph reseeding; rehearsal/test schemas clean up their own memory.
- Field reports are also indexed in Qdrant (`kind=field_report`).
