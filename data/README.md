# Demo data

All files here are either **synthetic** (Atelic-authored for the representative skid) or **placeholders** waiting for real, verified data. Placeholders have `"status": "pending"` and null values, and the UI shows the PRD's missing-data state for them.

| File | Screen | Provenance | Status | Replace with |
|---|---|---|---|---|
| `carbon_mapper/verified_plume.json` | 1 Overview | provider_reported | **placeholder** | Team-verified Carbon Mapper record |
| `ogi/source_metadata.json` | 2 Detect | experimental_setpoint | **pending licence** (clip MOV_2566 set; file facts from `scripts/ogi_inspect.py`) | Set `status: verified` once the licence is confirmed |
| `ogi/plume_annotations.json` | 2 Detect | manual_annotation | **placeholder** | Hand-drawn or real model output on the clip |
| `ogi/source_clip.mp4` | 2 Detect | — | local only (gitignored) | GasVid MOV_2566 converted to H.264: `avconvert -s MOV_2566.mp4 -p PresetHighestQuality -o data/ogi/source_clip.mp4` |
| `ogi/frame_times.json` | 2 Detect | — | generated | Run `python3 scripts/ogi_inspect.py` after changing the clip |
| `skid/equipment.json` | 3 Locate | synthetic | draft | Team-reviewed asset list |
| `skid/layout.json` | 3 Locate | synthetic | draft | Team-reviewed 2D layout |
| `skid/camera_rois.json` | 2, 3 | manual_illustrative | draft, polygons empty | Polygons drawn on the approved clip |
| `skid/candidates.json` | 3 Locate | simulated | **sample (mock)** | Graph agent output over Neo4j |
| `maintenance/history.json` | 3, 5 | synthetic | draft | Team-authored history |
| `scenario/site.json` | all | synthetic | draft | — |
| `scenario/release_truth.json` | 4 | synthetic | draft | — (simulation input, never shown as measured) |
| `scenario/geometry.json` | 4 | synthetic | **draft** (assumed positions, heights, stability) | Team-reviewed skid geometry |
| `scenario/sensors.csv`, `weather.csv` | 4 | synthetic | generated (GD-01 from the Gaussian plume model in `backend/app/plume.py`) | Run `python3 scripts/gen_scenario.py` |
| `docs/maintenance_procedure.md`, `docs/isolation_checklist.md` | 5 | synthetic | draft | Team-authored procedures |

Not stored as files on purpose: work orders, approvals, the audit log and agent traces. These are produced by the backend at runtime.
