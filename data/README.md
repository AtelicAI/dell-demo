# Demo data

All files here are either **synthetic** (Atelic-authored for the representative skid) or **placeholders** waiting for real, verified data. Placeholders have `"status": "pending"` and null values, and the UI shows the PRD's missing-data state for them.

| File | Screen | Provenance | Status | Replace with |
|---|---|---|---|---|
| `carbon_mapper/verified_plume.json`, `plume.png` | 1 Overview | provider_reported | fetched (plume `tan20260923t085028c30s4001-A`, a **CO₂** plume; licence pending) | Run `python3 scripts/fetch_carbon_mapper.py [plume_id [source_name]]` (needs Pillow) |
| `ogi/source_metadata.json` | 2 Detect | experimental_setpoint | **pending licence** (clip MOV_2566 set; file facts from `scripts/ogi_inspect.py`) | Set `status: verified` once the licence is confirmed |
| `ogi/plume_annotations.json` | 2 Detect | manual_annotation | **placeholder** | Hand-drawn or real model output on the clip |
| `ogi/source_clip.mp4` | 2 Detect | — | local only (gitignored) | `gasvid-visibility-clean.mp4`: 5 s enhanced-visibility MOV_2566, plume colourised in the video (`plume_rendering: burned_in`, no browser heat map). Crop the title bar: `ffmpeg -i gasvid-visibility-clean.mp4 -vf crop=640:480:0:32 -c:v libx264 -crf 16 -pix_fmt yuv420p -r 15 -an data/ogi/source_clip.mp4`, then run `scripts/ogi_inspect.py` |
| `ogi/source_clip_raw.mp4` | — | — | local only (gitignored), unused | Previous raw cut, to switch back: `avconvert -s MOV_2566.mp4 -p PresetHighestQuality -o data/ogi/source_clip.mp4 --start 190 --duration 290`, then restore the old plume region and drop `plume_rendering` |
| `ogi/frame_times.json` | 2 Detect | — | generated | Run `python3 scripts/ogi_inspect.py` after changing the clip |
| `skid/equipment.json` | 3 Locate | synthetic | draft | Team-reviewed asset list |
| `skid/layout.json` | 3 Locate | synthetic | draft | Team-reviewed 2D layout |
| `skid/camera_rois.json` | 2, 3 | manual_illustrative | draft, polygons empty | Polygons drawn on the approved clip |
| `skid/camera_wall.json` | 2 Detect | simulated | **sample** (CAM-02…04 loop recorded METEC clips, picked as plume-free) | Team-reviewed camera list; real feeds if any |
| `ogi/cam02_MOV_2571.mp4`, `cam03_MOV_2583.mp4`, `cam04_MOV_2570.mp4` | 2 Detect | — | local only (gitignored) | From the GasVid Drive folder, cut and converted: `avconvert -s MOV_2571.mp4 -p PresetHighestQuality -o data/ogi/cam02_MOV_2571.mp4 --start 0 --duration 280` (MOV_2583: 300 s, MOV_2570: 230 s) |
| `skid/candidates.json` | 3 Locate | simulated | **sample (mock)** | Graph agent output over Neo4j |
| `maintenance/history.json` | 3, 5 | synthetic | draft | Team-authored history |
| `scenario/site.json` | all | synthetic | draft | — |
| `scenario/release_truth.json` | 4 | synthetic | draft | — (simulation input, never shown as measured) |
| `scenario/geometry.json` | 4 | synthetic | **draft** (assumed positions, heights, stability) | Team-reviewed skid geometry |
| `scenario/sensors.csv`, `weather.csv` | 4 | synthetic | generated (GD-01 from the Gaussian plume model in `backend/app/plume.py`) | Run `python3 scripts/gen_scenario.py` |
| `docs/maintenance_procedure.md`, `docs/isolation_checklist.md` | 5 | synthetic | draft | Team-authored procedures |

Not stored as files on purpose: work orders, approvals, the audit log and agent traces. These are produced by the backend at runtime.
