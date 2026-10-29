# Demo story runbook

One incident, start to finish: a quiet camera, a leak whose real metered rate we know, six weeks of plant history, agents that investigate and draft, an engineer who questions and pushes back, a repair, a check that the leak is gone, and a human closing it. Backend only; the UI polls the endpoints below. Contract details: `BACKEND_HANDOFF.md` ("Full story" section).

## Say this up front (keep it on screen)
- The footage is recorded GasVid MOV_1242, a different physical scene from the plant. The camera-to-equipment mapping is illustrative.
- The footage is a real METEC controlled release (13 July 2017, FLIR GF-320 at 4.6 m) with a published metered rate; the synthetic plant releases that same rate at XV-2104. Only the rate is matched, not the scene.
- The plant, its sensors, its maintenance history, its 47-day sensor history and the "repair" are synthetic. The history's wind and background variability come from measured METEC 2024 data.
- The agents run on hosted NVIDIA Nemotron (`nvidia/nemotron-3-ultra-550b-a55b`), not on Dell hardware.
- Agents never approve, close, issue reports or touch equipment. Every one of those is an authenticated engineer.

## Beats

| # | Beat | What the audience sees | Backend | Who | Typical time |
|---|---|---|---|---|---|
| 0 | Control | `no_leak` feed: a real 5.3 g/h metered release the camera cannot see; no alert, nothing can be opened | `GET /monitor/status?profile=no_leak` (`metered_release`) | – | as long as you like |
| 1 | Calm | `leak` feed from clip 125 s: "No plume detected in this interval" | `GET /monitor/status` | – | 60 s of footage |
| 2 | Alert | Plume appears; alert latches at clip ≈194 s (scenario 610 s). Caption: "On camera: METEC metered release 277.7 g/h" | `POST /monitor/incidents`, `GET /monitor/footage` | – | instant |
| 3 | Locate | XV-2104 and F-205 both mapped; neither confirmed | trace `locate` | deterministic | <1 s |
| 4 | Quantify | `non_steady_plume`, then ≈0.26 kg/h at 670 s with pipe P-202 still possible; by 850 s only XV-2104 fits, 0.279 kg/h | trace `quantify` | physics | <1 s |
| 4b | History | 47 days of plant history: faint episodes at the load changes from 15 Sept, quiet for 10 days after the WO-009 re-torque, back from 6 Oct and growing | `GET /incidents/{id}/plant-history?include_series=true` | deterministic | 2 s |
| 5 | Investigate | Findings: WO-009 "verified" but the WO-008 OGI re-survey never happened, and the history shows the episodes returned 10 days later; WO-012 hissing between the two candidates; F-205 torque record missing | trace `investigate` | Nemotron | see below |
| 6 | Plan | Draft v1 with cited checks; waits for the engineer | trace `plan`, `submit` | Nemotron | see below |
| 7 | Ask | Engineer asks why XV-2104 still leads despite WO-009; agent answers with citations | `POST /incidents/{id}/questions` | engineer → Nemotron | see below |
| 8 | Push back | Engineer requests changes; agent revises to draft v2 and resubmits | `POST /incidents/{id}/review` `request_changes` | engineer → Nemotron | see below |
| 9 | Approve | Engineer approves v2 and issues the report (PDF) | `review` `approve`, `POST /report` | engineer | instant |
| 9b | Answer key | Metered 277.7 g/h vs our estimate; error under 1%. Only now, so it never anchored the review | `GET /incidents/{id}/reveal` | – | instant |
| 10 | Repair | Engineer records the field work done by the crew | `POST /incidents/{id}/repair` | engineer | instant |
| 11 | Verify | Physics check: readings back to the pre-incident baseline; agent recommends closing | trace `verify` | physics + Nemotron | see below |
| 12 | Close | Engineer closes; audit chain valid; the incident is written to the graph as memory for XV-2104 and F-205 | `POST /incidents/{id}/close` (`graph_memory`), `GET /assets/XV-2104/incident-memory` | engineer | instant |
| 13 | Contrast | `wind_outage` feed: same footage, weather station down; no rate, and the plan says so | `POST /monitor/incidents` `profile=wind_outage` | – | one more investigate + plan |

Throughout, show `GET /incidents/{id}/orchestration` events as the Agent Activity drawer: every Nemotron turn and tool call appears as it happens, so the agent wait is something to watch, not dead air.

## Timing (live Nemotron Ultra, measured 2026-10-27)

| Step | Recorded run (2026-10-27 21:23 UTC, extended graph) | Earlier runs today |
|---|---|---|
| Alert → draft v1 pending review (quantify settles at 850 s, investigate + plan) | 285 s (investigate 93 s, plan 184 s) | 148–252 s |
| Answer a question | 76 s | 30–80 s |
| Revise after request_changes | 40 s | 30–107 s |
| Verify after repair record | 44 s | 32–84 s |
| Whole leak story, alert to closed (incl. graph memory write) | 7 min 34 s | 5 min 26 s – 7 min 36 s |
| Wind-outage contrast to pending review | 2 min 40 s | 1 min 36 s – 3 min 33 s |

Hosted-endpoint latency varied 2–3x between runs on the same day. One provider error per live run is normal and retried automatically (the trace shows `attempt_failed`). Each model request now has a hard 300 s wall-clock deadline, so a hung request becomes a retried attempt instead of a stalled run. Narrate over the streaming trace during agent steps, or cut between beats.

## Fallbacks
1. **Slow or unavailable model.** Start the incident with `{"plan_fallback": "deterministic"}`: after two failed model attempts each agent step uses a labeled template (`source_origin=deterministic`). Say so out loud; never present it as AI.
2. **No network at all.** Play `GET /story/recorded-run`, the last saved live run with every beat, trace and record. It is labeled `recorded_not_live`; keep that label visible.
3. **Run stuck.** `GET /incidents/{id}/orchestration` shows `failed` with the reason, or `interrupted` after 15 min without progress. `POST /incidents/{id}/orchestration` resumes; finished steps are not repeated.

## Rehearse
- Full live story in a disposable schema: `.venv/bin/python scripts/story_run.py` (add `--save` to refresh the recorded run, `--deterministic` for a free dry run in ~15 s).
- Live demo against the running backend uses the real engineer credential (`ENGINEER_TOKEN`), which must only be entered by the person playing the engineer, never stored in the browser build.
