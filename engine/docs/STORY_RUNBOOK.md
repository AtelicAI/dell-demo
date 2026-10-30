# Demo story runbook

One incident, start to finish: a quiet camera, a leak whose real metered rate we know, six weeks of plant history, agents that investigate and draft, an engineer who questions and pushes back, a repair, a check that the leak is gone, and a human closing it. Backend only; the UI polls the endpoints below. Contract details: `BACKEND_HANDOFF.md` ("Full story" section).

## Say this up front (keep it on screen)
- The footage is recorded GasVid MOV_1242, a different physical scene from the plant. The camera-to-equipment mapping is illustrative.
- The footage is a real METEC controlled release (13 July 2017, FLIR GF-320 at 4.6 m) with a published metered rate; the synthetic plant releases that same rate at XV-2104. Only the rate is matched, not the scene.
- The site is an anonymous gas gathering station in open desert in Saudi Arabia's Eastern Province. The incident runs at night (20:00 UTC = 23:00 local); the footage was shot by day, which a thermal camera images the same way.
- Camera rates use GasVid grey levels as a stand-in for radiometric frames. Each camera's gain comes from a metered 277.7 g/h release it recorded; for CAM-01 that is the first 40 s of the same release the incident measures.
- The plant, its sensors (including a sonic anemometer on the weather mast), its maintenance history, its 47-day sensor history and the "repair" are synthetic. The history's wind and background variability come from measured METEC 2024 data.
- The agents run on hosted NVIDIA Nemotron (`nvidia/nemotron-3-ultra-550b-a55b`), not on Dell hardware.
- Agents never approve, close, issue reports or touch equipment. Every one of those is an authenticated engineer.

## Beats

| # | Beat | What the audience sees | Backend | Who | Typical time |
|---|---|---|---|---|---|
| 00 | Satellite view | Overview: a real Carbon Mapper methane plume from the oil and gas sector in the Eastern Province (Tanager-1, 9 December 2025, ≈630 kg/h from a small site; 1 detection in 18 passes), labelled regional context, not this site. Say: "Satellites catch super-emitters. The leak we're about to find is 0.28 kg/h, about 2,200× smaller and far below what orbit can see. That's why the station needs cameras and sensors on site." | UI `GET /api/overview/plume` (cached Carbon Mapper record) | – | 30 s |
| 0 | Control | `no_leak` feed: a real 5.3 g/h metered release the camera cannot see; no alert, nothing can be opened | `GET /monitor/status?profile=no_leak` (`metered_release`) | – | as long as you like |
| 1 | Calm | UI Camera: CAM-01 plays event 1 (GasVid MOV_1237, 6.9 m) as the Atelic render with the original-IR slider, from clip 175 s while the metered release is still below detection (5.3 g/h). CAM-03/04 loop class-0 footage. Press Trigger (T) to fast-forward to 6 s before the alarm | `GET /monitor/status` | – | up to 30 s |
| 2 | Alert | The release steps to 277.7 g/h at 199 s; the detector alarm latches at 204.1 s (3 of the last 5 samples, scenario 606 s) and the UI raises INC-001 in place: the same recording keeps playing | `POST /monitor/incidents` | – | instant |
| 2b | Event 2 (optional) | Trigger CAM-02: event 2 (MOV_1239, 12.6 m) alarms at 538.6 s on the weather-mast-outage scenario (SCN-003). The plant releases what the footage shows (713.1 g/h, then 958.8 g/h from clip 545 s). The mast stops reporting, so the quantify agent switches to camera quantification (QOGI), waits 80 s for a steady 90 s plume window and estimates ≈1.06 kg/h (answer key 958.8 g/h, +11%) | `profile=wind_outage` | – | instant |
| 3 | Locate | XV-2104 and F-205 both mapped; neither confirmed | trace `locate` | deterministic | <1 s |
| 4 | Quantify | Camera first: the agent reads the rate from CAM-01's radiometric plume (QOGI), waiting about 90 s for a steady window: 0.243 kg/h. The sensor models check it as the case allows: Gaussian plume 0.279 (1.15×) and MDLQ 0.278, with leak probability 1.0 for XV-2104 and ≤0.002 for every other source, by 846 s. With one source left: bLs 0.314 (1.29×, stable night air from the sonic) and CFD 0.296 (1.22×, flow solved around the separator and compressor). The rate chart shows every tool on one kg/h scale; click a row (or a tool's Run button) to run that tool on the frozen window | trace `quantify` (`primary_tool`, `tool_roles`, `cross_checks`) | physics | ~8 s (bLs + CFD) |
| 4b | History | 47 days of plant history: faint episodes at the load changes from 15 Sept, quiet for 10 days after the WO-009 re-torque, back from 6 Oct and growing | `GET /incidents/{id}/plant-history?include_series=true` | deterministic | 2 s |
| 5 | Investigate | Findings: WO-009 "verified" but the WO-008 OGI re-survey never happened, and the history shows the episodes returned 10 days later; WO-012 hissing between the two candidates; F-205 torque record missing | trace `investigate` | Nemotron | see below |
| 6 | Plan | Draft v1 with cited checks; waits for the engineer | trace `plan`, `submit` | Nemotron | see below |
| 7 | Ask | Engineer asks why XV-2104 still leads despite WO-009; agent answers with citations | `POST /incidents/{id}/questions` | engineer → Nemotron | see below |
| 8 | Push back | Engineer requests changes; agent revises to draft v2 and resubmits | `POST /incidents/{id}/review` `request_changes` | engineer → Nemotron | see below |
| 9 | Approve | Engineer approves v2 and issues the report (PDF) | `review` `approve`, `POST /report` | engineer | instant |
| 9b | Answer key | Metered 277.7 g/h vs the camera's 243 g/h (−12.5%); the sensor checks were 279 and 314 g/h. Only now, so it never anchored the review | `GET /incidents/{id}/reveal` | – | instant |
| 10 | Repair | Engineer records the field work done by the crew | `POST /incidents/{id}/repair` | engineer | instant |
| 11 | Verify | Physics check: readings back to the pre-incident baseline; agent recommends closing | trace `verify` | physics + Nemotron | see below |
| 12 | Close | Engineer closes; audit chain valid; the incident is written to the graph as memory for XV-2104 and F-205 | `POST /incidents/{id}/close` (`graph_memory`), `GET /assets/XV-2104/incident-memory` | engineer | instant |
| 13 | Contrast | `wind_outage` feed: weather mast down; sensor tools cannot run, the agent switches to the camera and still gets a rate (≈1.06 kg/h) | `POST /monitor/incidents` `profile=wind_outage` | – | one more investigate + plan |

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
