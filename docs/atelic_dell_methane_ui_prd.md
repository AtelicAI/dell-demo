# Atelic × Dell × NVIDIA: Methane Incident Intelligence
## UI Product Requirements Document
Version 1.1 | 22 October 2026 | Companion: `atelic_dell_methane_demo_spec.md`

## Product and provenance
Build a two-screen booth demo with six operator stages: **Detect → Locate → Quantify → Plan → Approve → Report**. Screen A, **Observe**, shows regional satellite context and local alert entry. Screen B, **Resolve**, shows camera footage, equipment investigation, emissions, maintenance, approval and report. Also support a normal single-screen app.

**Truth rule:** Carbon Mapper satellite observations and GasVid/METEC optical gas imaging (OGI) footage are **unrelated real datasets**. Equipment layout, camera mapping, historian, maintenance and work orders are **synthetic representative data**. Never imply the satellite plume was traced to the valve in the GasVid clip. Never label a synthetic emissions estimate as measured. Detection is assumed available from a camera system, so new ML training is optional.

**Minimum scope:** one regional plume (if verified), one real OGI clip, one representative skid with 8–12 assets, one camera ROI mapping, one replayed incident, one simulated sensor fixture, one emissions calculation or verified experimental setpoint, one draft work order, one human approval, one report.

### Global shell
Header text: `ATELIC | Methane Incident Intelligence`. Show `Dell Edge: Running / Target deployment`, `NVIDIA inference: Local NIM / Hosted API / Not connected`, `Replay time: [UTC]`, all based on actual execution. Incident strip: `INC-001 | Representative gas-handling skid | CAM-01 | Source unconfirmed | Illustrative scenario`.

Primary stepper: `Detect | Locate | Quantify | Plan | Approve | Report`, with `Not started / In progress / Ready for review / Completed / Blocked` statuses from backend state. Secondary `Agent Activity` drawer.

Persistent disclosure: `Composite demonstration. Satellite observations and METEC OGI footage are independent. Equipment, sensors and maintenance workflow are illustrative.` Distinguish `Observed`, `Provider reported`, `Experimental setpoint`, `Model estimated`, `Simulated`, `Hypothesis`, `Approved` in copy and badges. Never fabricate confidence, GPU usage or “live” processing.

## Screen 1: Overview / Observe
**Route:** `/overview`. **Goal:** Understand the regional observation and choose to explore a separate local example.

**Layout:** 65% geospatial map, 35% source details, minimal top navigation. Use cached licensed basemap or schematic offline.

**Exact copy:** H1 `Methane observations`. Intro `Review regional emissions observations and explore an illustrative equipment-level investigation.` Provider card `Carbon Mapper observation`; fields `Observed`, `Coordinates`, `Instrument`, `Provider-reported rate (kg CH₄/h)`, `Uncertainty`, `Source record`. CTA `Explore local investigation example`. Modal: `The camera footage comes from a separate METEC controlled-release experiment and does not depict this satellite-observed site.` Missing-data message: `Verified satellite observation details are unavailable. You can still explore the local investigation example.`

**Data source:** `carbon_mapper/verified_plume.json` (ID, coordinates, time, rate, uncertainty, instrument, URL, license); approved basemap; synthetic `scenario/site.json`. **Interactions:** select plume, open source, toggle footprint, enter local example. **Acceptance:** no implied matched footage or valve attribution; show only verified provider values.

## Screen 2: Detect / Camera
**Route:** `/incidents/:id/detection`. **Goal:** Inspect real OGI footage and its alert provenance.

**Layout:** 65% 16:9 video with plume overlay and scrubber, 35% detection metadata; bottom timeline.

**Exact copy:** H1 `Camera observation`. Intro `Review the infrared recording and the observation that initiated the investigation.` Player `CAM-01 | METEC controlled-release footage`. Status `Plume observed` or `Detection not confirmed`. Fields `Recording time`, `Source`, `Resolution`, `Frame rate`, `Detection method`, `Model/version`, `Confidence (if supplied)`, `Plume region`. Detection mode values `External detector alert`, `Local model inference`, `Recorded model output`, `Manually annotated replay`. Caveat `The plume image alone does not identify a particular component or quantify emissions.` CTA `Investigate possible source`.

**Data source:** `ogi/source_clip.mp4`, `ogi/source_metadata.json`, `ogi/plume_annotations.json` or detection API. Video is real; model inference only when executed; manually drawn masks explicitly marked. **Interactions:** play/pause, frame step, scrub, toggle mask/bbox/origin, inspect model/source. **Acceptance:** frame-accurate overlay, no fake confidence or live status.

## Screen 3: Locate / Source investigation
**Route:** `/incidents/:id/locate`. **Goal:** Identify plausible equipment with evidence and uncertainty.

**Layout:** left OGI frame with ROI, center interactive 2D skid (optional 3D), right ranked candidates; tabs `Evidence`, `Connections`, `Maintenance`, `Mapping assumptions`.

**Exact copy:** H1 `Investigate the likely source`. Intro `Compare the camera observation with equipment visibility, piping connections and available records.` Primary result `Likely source: VENT-01 (unconfirmed)` only if supported, otherwise `Source unresolved`. Candidate names `VENT-01 | Vertical release assembly`, `XV-104 | Isolation valve`, `P-101 | Outlet line`. Sections `Why this component?`, `Other possible sources`, `Evidence against`, `Missing information`, `Connected equipment`. Disclosure `Camera-to-equipment mapping is manually configured for this representative skid.` CTA `Review emissions estimate`.

**Data source:** synthetic `skid/equipment.json`, `skid/camera_rois.json`, Neo4j `CAN_OBSERVE` / `CONNECTED_TO` / `MONITORS` relationships, synthetic maintenance records. **Interactions:** selecting a candidate highlights its ROI, skid asset and graph path; inspect source and mapping version; permit unresolved outcome. **Acceptance:** all IDs valid; no fake calibrated confidence; graph facts separated from hypotheses.

## Screen 4: Quantify / Emissions
**Route:** `/incidents/:id/quantify`. **Goal:** Show available rate evidence and what can actually be estimated.

**Layout:** top result card, middle time-aligned sensor charts, right method/inputs panel, lower assumptions and QC.

**Exact copy:** H1 `Estimate methane emissions`. Intro `Quantification depends on available measurements and the assumptions of the selected method.` Result title by provenance: `Simulated emission-rate estimate`, `Controlled-release setpoint`, `Provider-reported satellite estimate`, or `Emission rate unavailable`. Fields `Rate (kg CH₄/h)`, `Uncertainty interval`, `Method`, `Data window`, `Model version`, `Input quality`, `Assumptions`. Charts `Methane concentration (ppm)`, `Wind speed (m/s)`, `Wind direction (° from north)`, `Process pressure (bar absolute)`. Warning `These sensor traces are simulated for the representative scenario, not recorded with GasVid.` Failure `Insufficient concentration, wind or source geometry data to estimate the release rate reliably.` CTA `Prepare engineering response`.

**Data source:** synthetic `scenario/sensors.csv`, `scenario/weather.csv`, `scenario/release_truth.json`, InfluxDB replay, deterministic Gaussian/pyELQ service `POST /quantify`. Verified GasVid setpoint may be shown as *experimental setpoint*, never inferred estimate. Carbon Mapper rate belongs to Overview only. **Interactions:** inspect method and QC, hover charts, view missing inputs, select only valid available methods. **Acceptance:** no rate without provenance/units; no invented uncertainty; insufficient data produces no numeric estimate.

## Screen 5: Plan / Engineering action
**Route:** `/incidents/:id/plan`. **Goal:** Prepare an evidence-backed, non-executable maintenance draft.

**Layout:** incident summary left, recommended checks/work order center, citations right, agent trace collapsed by default.

**Exact copy:** H1 `Prepare an engineering response`. Intro `Review evidence-backed checks and a proposed work order before any action is authorized.` Assessment `Possible release near VENT-01. Physical confirmation is required.` Sections `What we observed`, `What remains uncertain`, `Recommended inspection`, `Isolation prerequisites`, `Safety considerations`, `Evidence used`. Work order fields `Asset`, `Problem`, `Proposed inspection`, `Priority`, `Required permits`, `Status: Draft`. Warning `Illustrative procedures are not approved site operating instructions. No equipment action will be executed.` CTA `Submit for engineer review`.

**Data source:** Neo4j, Qdrant engineering documents, NeMo Retriever embedding/rerank if integrated, genuine Nemotron/NIM response, synthetic `docs/maintenance_procedure.md`, `docs/isolation_checklist.md`, incident and quantification results. **Interactions:** expand citations, edit/version draft, flag unsupported claim, submit. **Acceptance:** recommendations traceable to evidence or clearly hypotheses; no plant-control endpoint.

## Screen 6: Approve / Engineer review
**Route:** `/incidents/:id/review`. **Goal:** Explicit human decision on the draft, not equipment operation.

**Layout:** evidence summary, full draft, right reviewer decision rail and confirmation dialog.

**Exact copy:** H1 `Engineer review`. Intro `Confirm the evidence and proposed action. Approval does not operate equipment.` Sections `Incident evidence`, `Source uncertainty`, `Proposed work order`, `Procedure limitations`, `Reviewer decision`. Actions `Approve draft`, `Request changes`, `Reject draft`. Modal `Approve this illustrative work order draft? No physical plant action will occur.` Status `Approved by [reviewer] at [time]` or `Rejected: [reason]`.

**Data source:** Postgres/SQLite incident state, work-order version/hash, reviewer input, append-only audit events. **Interactions:** reviewer identity, confirmation, mandatory reason for rejection/changes, prevent stale/duplicate approval. **Acceptance:** never auto-approve; rejected drafts cannot close incident; transitions logged.

## Screen 7: Report / Audit
**Route:** `/incidents/:id/report`. **Goal:** Inspect and export a source-linked record.

**Layout:** document preview, evidence table, chronological audit timeline, export actions.

**Exact copy:** H1 `Incident report`. Intro `Review observations, estimates, assumptions and human decisions recorded during this investigation.` Sections `Incident summary`, `Camera observation`, `Source candidates`, `Emissions information`, `Recommended action`, `Engineer decision`, `Audit timeline`, `Data provenance and limitations`. Footer `OGMP 2.0-aligned demonstration fields. Not a certified operator submission.` Actions `Export PDF`, `Export Markdown`, `Export JSON`, `View source evidence`. Pending state `Draft report. Engineer review is not complete.`

**Data source:** backend incident snapshot, video manifest, ROI version, graph evidence, physics output, approval and audit log. **Interactions:** inspect evidence, export, view gaps. **Acceptance:** export matches UI; all simulated values labeled; no closed status before approval.

## Screen 8: Agent Activity / Glass box
**Route:** `/incidents/:id/activity` or side drawer. **Goal:** Show genuine Dell/NVIDIA/Atelic execution, not decorative AI animation.

**Layout:** chronological trace with timestamp/duration, tool details on click, small hardware panel.

**Exact copy:** H1 `Investigation activity`. Intro `Review the models, tools and evidence used during this incident.` Steps `Camera event received`, `Equipment mapping queried`, `Graph relationships retrieved`, `Procedure passages retrieved`, `Emission estimate calculated`, `Work order drafted`, `Human approval required`. Fields `Component`, `Model/version`, `Provider`, `Execution location`, `Tool`, `Duration`, `Evidence`, `Result`, `Guardrail decision`. Safety line `No physical control commands are enabled.` If absent `NVIDIA inference not connected in this run.`

**Data source:** actual orchestrator spans, Neo4j/Qdrant tool logs, NVIDIA API/NIM metadata, GPU metrics only when measured. **Interactions:** filter, expand, open cited evidence, replay. **Acceptance:** no invented tool calls, fake GPU load or unverified local NIM claim; do not expose secrets or hidden reasoning.

## Shared contracts and source provenance

```ts
type Provenance = 'provider_reported'|'experimental_setpoint'|'model_estimated'|'synthetic'|'manual_annotation'|'unknown';
type Stage = 'detect'|'locate'|'quantify'|'plan'|'approve'|'report';
type StageStatus = 'not_started'|'in_progress'|'ready_for_review'|'completed'|'blocked';
interface EvidenceRef { id:string; sourceId:string; label:string; uri?:string; provenance:Provenance; timestamp?:string; }
interface Measurement { value:number|null; unit:string; provenance:Provenance; method?:string; evidence:EvidenceRef[]; }
interface Candidate { assetId:string; status:'candidate'|'confirmed'|'excluded'; rationale:string[]; supporting:EvidenceRef[]; contradicting:EvidenceRef[]; }
```

Recommended APIs: `GET /incidents/:id`, `/detections`, `/candidates`, `/sensor-series`, `/quantification`, `/work-order`, `/audit`, `/agent-runs`; `POST /quantify`, `/work-order/draft`, `/review`, `/report/export`. Use one replay clock and a shared incident state across both screens. Agent tools must not see future replay data.

## UX and delivery acceptance

| Priority | Deliverable | Pass condition |
|---|---|---|
| P0 | Overview + provenance transition | Satellite and OGI clearly separate |
| P0 | OGI video | Correct playback, timestamp and overlays |
| P0 | 2D skid + graph + candidate list | Selection synchronizes camera, map and graph |
| P0 | Six-stage stepper | Backend-driven statuses and deterministic replay |
| P0 | Plan, review, report | Human approval, traceable export |
| P0 | Agent activity | Actual NVIDIA integration status and tool calls |
| P1 | Sensor charts + physics | Units, uncertainty and provenance visible |
| P1 | 3D skid | Same IDs as 2D, only after MVP stable |
| P2 | Full satellite API and advanced CV | Not critical path |

**Visual QA:** 1440×900 booth laptop and 1920×1080 display; readable at distance; no overlapping panels; sensible empty/error states; keyboard controls; charts labeled with units; accessible color contrast. Keep the OGI footage and source hypothesis visible together in Resolve booth mode. Avoid more than three simultaneous panes.

**One-week build order:** (1) Overview + OGI player + source labels; (2) skid/ROI/Neo4j selection; (3) simulated sensors and quantification result; (4) actual NVIDIA reasoning/tool path and citations; (5) plan/approval/report; (6) synchronized two-screen booth mode, offline fallback, three clean rehearsals.

**Final demo acceptance:** One operator can open a real OGI replay, inspect an unconfirmed source hypothesis on a representative skid, review a properly labeled simulated or experimental emissions result, generate a cited draft, approve it manually and export an honest report. A separate verified satellite observation is shown only as regional context.


## Addendum A: Knowledge graph UX and synchronization (required P0)

### Placement and navigation
The **primary interactive knowledge graph belongs on Screen 3, Locate**, alongside the equipment layout and OGI video. Provide `Equipment layout | Knowledge graph` tabs in the central investigation panel; the candidate list and evidence sidebar persist when switching tabs. The graph is a human-readable explanation of asset relationships, not a decorative force-directed visualization. Do not show the entire plant by default: start with the suspected asset and its one-hop neighborhood, with explicit `Expand upstream`, `Expand downstream`, and `Show linked sensors` controls.

On **Screen 5, Plan**, show a compact `Why this procedure applies` relationship path connecting the candidate asset to the retrieved maintenance/procedure source. On **Screen 8, Agent Activity**, show the actual graph-tool call, inputs, returned asset IDs and duration; never fabricate queries or tool outputs.

### Node, edge, and evidence model
- Node types: `Facility`, `EquipmentArea`, `Vessel`, `PipeSegment`, `Valve`, `Vent`, `Sensor`, `Camera`, `Incident`, `Document`, `WorkOrder`.
- Edge types: `CONTAINS`, `CONNECTED_TO`, `UPSTREAM_OF`, `CAN_OBSERVE`, `MONITORS`, `MENTIONS`, `INVOLVES`, `HAS_WORK_ORDER`. `CAN_OBSERVE` requires a documented camera visibility mapping, not an LLM guess.
- Every graph node exposes `asset_id`, `display_name`, `asset_type`, `source`, `provenance`, `mapping_version`, and optional `location`, `image_region`, `document_refs`.
- Graph edge detail shows relationship type, source record, provenance, and whether verified, reconstructed, or manually annotated.

### Exact Screen 3 UI text
- Tab names: `Equipment layout`, `Knowledge graph`.
- Graph panel title: `How the equipment is connected`.
- Helper: `Select an asset to inspect its connections, sensors, camera coverage and supporting records.`
- Selected-node panel: `Asset details`, `Connected equipment`, `Camera visibility`, `Measurements`, `Related documents`, `Source of relationship`.
- Source assessment: `Likely source (unconfirmed)` or `Source unresolved`.
- Mapping disclosure: `Equipment relationships and camera coverage are representative, manually configured demo data.`
- Empty state: `No verified relationship is available for this asset.`
- Missing camera mapping: `Camera coverage has not been calibrated. Equipment attribution is illustrative.`

### Interaction and acceptance
Selecting a valve in the graph highlights the same asset in the 2D/3D layout, corresponding ROI in the OGI player if present, and related candidate/evidence row. Selecting an asset in any of those views must update the other views by shared `asset_id`. The system must preserve the selected asset while switching tabs; clicking an edge opens its provenance. Graph topology cannot be silently changed by an agent. A graph query returning no evidence must produce an unresolved result, not an invented connection. Graph must remain usable with 8–12 representative assets at 1440×900 and in the two-screen booth mode.

**Backend/data contracts:** `GET /incidents/:id/graph?depth=1`, `GET /assets/:asset_id/relationships`, `GET /cameras/:camera_id/visibility`, `GET /assets/:asset_id/evidence`. Neo4j is the source for relationship queries; the skid JSON and manually verified camera ROI annotations are the authored sources for the synthetic graph. Qdrant/NeMo Retriever supplies document passages, not authoritative pipe connectivity.

## Addendum B: Facility map, satellite plume, and OGI video provenance (required P0)

| UI asset | Canonical source | UI field / handling | Known limitation |
|---|---|---|---|
| Regional plume record | [Carbon Mapper data portal](https://data.carbonmapper.org/) and [API documentation](https://api.carbonmapper.org/api/v1/docs) | Observation ID, coordinates, observation time, sensor/instrument, provider rate, uncertainty, source link | A satellite/aerial observation is **not** a camera observation or proof of a specific valve leak. Validate candidate Bahrain/GCC records before displaying numeric claims. |
| Facility overview basemap | Licensed imagery/tiles (MapLibre-compatible provider or permitted offline image) | Attribution, image date if available, coordinate reference and imagery license | Carbon Mapper plume imagery is not a detailed equipment CAD model; the basemap does not supply valve tags. |
| Local OGI camera footage | [Lang-Gas repository](https://github.com/weathon/Lang-Gas), original [GasVid/VideoGasNet research](https://eao.stanford.edu/publications/journal-articles/videogasnet-deep-learning-natural-gas-methane-leak-classification), and reported [GasVid folder](https://drive.google.com/drive/folders/1JKEMtCGPSq2IqGk4uXZP9A6gr8fyJsGC) | Clip ID, actual origin `METEC`, experimental recording date when verified, camera type, FPS/resolution from inspected file, usage rights | Footage is from controlled METEC releases, **not** the selected Carbon Mapper facility. Confirm availability, license and file metadata before booth use. |
| Optional synthetic gas footage | [SimGas](https://huggingface.co/datasets/weathon/sim_gas) | Clearly label `Synthetic plume rendering` | No real physical location or measured emission rate. |
| Reconstructed equipment skid | Atelic-authored `skid/equipment.json`, `skid/layout.json` | Asset tags, geometry, adjacency, simulated incident | Not a surveyed reconstruction of METEC or the Carbon Mapper site. |
| Camera-to-equipment map | Atelic-authored `skid/camera_rois.json` | Normalized image polygons, camera ID, asset ID, mapping method/version | Manual illustrative mapping unless physically calibrated. |

### Explicit navigation boundary
Screen 1 must display `Carbon Mapper satellite observation` and a button labeled `Explore representative on-site investigation`. On click, show this exact transition notice: **`This local investigation uses OGI footage recorded at METEC and a representative equipment model. It is not footage from the satellite-observed facility.`** Screen 2 must show `OGI footage: METEC controlled release` and the original clip source. Screen 3 must show `Representative equipment reconstruction` near the layout/graph. Never copy the satellite rate into the local incident's emissions estimate. Keep separate `satellite_observation_id`, `ogi_clip_id`, and `local_incident_id` identifiers.

### Required metadata shape
```ts
interface VisualSource {
  id: string;
  kind: 'satellite_plume'|'basemap'|'ogi_video'|'synthetic_video'|'equipment_layout';
  provider: string;
  sourceUrl?: string;
  observedAt?: string;
  facilityId?: string;
  licenseStatus: 'verified'|'pending'|'restricted';
  provenance: 'provider_reported'|'experimental_setpoint'|'synthetic'|'manual_annotation';
  attributionText: string;
}
interface CameraAssetRegion {
  cameraId: string;
  clipId: string;
  assetId: string;
  polygonNormalized: [number, number][];
  mappingMethod: 'manual_illustrative'|'calibrated';
  mappingVersion: string;
}
```

### Updated UI acceptance criteria
1. A user can distinguish real Carbon Mapper observation data from unrelated METEC footage without opening a help panel.
2. All visual assets show a source label and traceable source record, with license status checked before public presentation.
3. Knowledge graph and equipment layout use the same stable asset IDs, with bidirectional selection and synchronized highlights.
4. Camera-to-asset mapping shows whether it is manually annotated or calibrated, and supports an unresolved result.
5. Graph evidence is source-linked; no synthetic graph edge, facility location or leak-rate value is represented as observed fact.
6. Satellite emissions rates never populate the local OGI incident's quantification result.
