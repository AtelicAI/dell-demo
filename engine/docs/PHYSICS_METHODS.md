# Physics methods and integration APIs

## Process API

`app.process.solve_process(facility, config=None) -> dict` accepts a canonical
Pydantic Facility or dictionary. Optional config overrides keys from
`data/facility/process_model.json`. Unsupported topology, impossible boundary
flow and invalid physical parameters raise ValueError. It is deterministic and
has no timestamp, scenario, leak-event or random-number dependency.

The returned keys are `method`, `version`, `status`, `assumptions`, `nodes`,
`edges`, `compressor`, `reynolds_number`, `darcy_friction_factor`,
`mass_balance_residual_kg_s`, `max_mass_balance_residual_kg_s`, and `measurements`.
Measurements are a list of sensor_id, asset_id, unit, value, status dictionaries;
these are predictions, **not historian Measurement records**. Pressure uses bar(a),
temperature K, flow kg/h. Methane and wind values are null/not_modeled. No process
solve can infer a leak from these assumed boundaries.

The actual canonical IDs are V-101, K-201 and XV-2104. The canonical FLOWS_TO
graph is traversed from V-101 to P-203. L-VENT branches are explicitly assumed
closed (zero flow); isolated branch pressures/temperatures remain unknown.
The separator is an ideal dry-gas inlet boundary, with no liquid separation,
holdup, transients or chemical calculation. There is no invented calibration.

Inputs assume methane-like ideal gas, Z=1, R=518.3 J/(kg K), viscosity
11 microPa s, inlet 7 bar absolute/293.15 K and fixed 0.5 kg/s flow. Pipe internal
diameter is 0.1 m and roughness 45 micrometres. Edge lengths are ENU centre-to-centre
distances, minimum 1 m; these are assumed hydraulic lengths, not as-built piping.
Valve entry K=2; other fittings have no additional minor loss. Closed branches
have no atmospheric pressure boundary. Outlet pressure is **computed**, not a
second independently fixed pressure boundary.

For each edge, integrated isothermal Darcy gives
`p_up² - p_down² = (f L/D + K) m_dot² Z R T / A²`.
Darcy friction uses 64/Re below 2300 and Haaland above; the transitional regime
is approximate. The serial graph admits an exact sequential solution, so SciPy
is unnecessary for the process module. Mass residual is incoming minus outgoing
plus boundary injection minus boundary withdrawal, kg/s, at every asset.

The compressor raises suction pressure by 1.8. With gamma=1.3 and isentropic
efficiency 0.75, `T_hot = T_in [1 + (ratio^((gamma-1)/gamma)-1)/eta]`;
`cp=gamma R/(gamma-1)`, shaft power is `m_dot cp (T_hot-T_in)`. An ideal
external aftercooler brings discharge to 303.15 K; duty is
`m_dot cp (T_hot-303.15)`. Downstream pipes are isothermal at that temperature.
K-201 node and TT-201 represent the cooled compressor package outlet;
uncooled discharge temperature is separately returned in `compressor`.
PT-201 is package discharge pressure. This is an explicit assumed sensor tap
mapping, not verified instrumentation. No compressor curve, choke, surge,
real-gas EOS, acoustic, elevation or obstacle model is claimed.

These are assumed skid inputs. The model cannot be described as a calibrated
GasLib skid or validated against GasLib merely because source material was skimmed.

## Inverse API

`app.quantification.quantify(facility, measurements, candidate_asset_id, as_of_utc)
-> dict` accepts a canonical Facility/dict, iterable of public historian/scenario
Measurement objects/dicts, canonical candidate ID, and timezone-aware ISO timestamp
or datetime. Runtime never imports the forward dispersion module or accesses the
generator, ground_truth, private labels or any scenario truth rate.

Results always include `method=nonnegative-gaussian-candidate-fit`, `version=1.0`,
`status`, `rate_kg_h`, `unit=kg/h`, `candidate_asset_id`, `source_confirmed=false`,
`assumptions`, `input_ids` and `candidate_fits`. Successful status is `estimated`;
failures are `rate_unavailable`, null rate, and machine-readable `reason`.
Input IDs identify methane and corresponding wind observations actually used.
Candidate fits report all positioned canonical assets, each labeled with
`fit_status`: `best_fit` (lowest RMS fit error), `comparable_fit` (error within 2x
the best, or best + 3 median sensor uncertainties, and within the absolute fit
gate), `poor_fit` or `cannot_test` (no downwind sensor coverage). Only best and
comparable fits keep a `rate_kg_h`; a poor fit's number is not an estimate and is
nulled. When the whole window is unusable (any `rate_unavailable` reason other
than the one below) every fit is `not_assessed`. They are conditional hypotheses,
not a source confirmation or probability ranking. Reported rate is for the
requested candidate; if that location is a `poor_fit` the result is
`rate_unavailable / candidate_inconsistent_with_readings`.

`compare_candidates(calculation, camera_asset_ids)` (exposed as
`telemetry_attribution` on the quantify API) states whether the sensor fits agree
with the camera candidates: `agrees_single`, `agrees_ambiguous`, `partly_agrees`
(another location fits as well), `disagrees`, `no_consistent_location` or
`not_assessed`. SCN-001 releases the footage's metered 277.7 g/h (GasVid class
1). The first steady estimate (670 s, 0.26 kg/h) is `partly_agrees`: pipe P-202,
just downwind of XV-2104 and outside the camera view, stays within the noise-based
tolerance while the weaker plume is still building. From 850 s only XV-2104 is
consistent (0.279 kg/h); F-205 fits about 5x worse. The orchestrator keeps
advancing the replay clock past the first estimate while the comparison is
undecided, within a 300 s cap; a final refusal is never replaced by an earlier
window's estimate.

Only the last 60 seconds at or before as_of are considered. Future observations
are excluded before validation, and cannot influence a fit. No interpolation or
future wind lookahead occurs. Exact same-time good wind speed and direction are
required for usable methane. Missing/QC-rejected rows are excluded; units and
canonical attachments are checked, duplicate sensor/time rows and mixed scenarios
are rejected. ENU positions must be finite with nonnegative height. Units are
ppm methane, m/s wind, deg_from_north direction. Negative/nonfinite readings,
speed below 0.5 m/s or direction outside [0,360) fail. Wind coefficient of
variation above 0.3 or circular resultant below cos(15 degrees) fails as unstable.
At least three distinct sample times and two distinct downwind receptor locations
with unit response above 0.01 ppm/(kg/h) are required.

A separately implemented reflected Gaussian unit response uses meteorological
FROM direction, sigma_y=max(0.5,0.12x), sigma_z=max(0.4,0.08x), methane molecular
weight 0.01604 kg/mol and ambient 293.15 K/101325 Pa. SciPy `lsq_linear` fits
nonnegative rate and background bounded to 0..5 ppm, weighted by the inverse
canonical uncertainty with a 0.01 ppm floor. The two-column response must have
full rank. Both downwind locations must show enhancement above max(detection
limit, 3 times sensor uncertainty); otherwise there is no resolved rate,
including a no-leak control. RMS residual above max(0.5 ppm, 25% observed range)
rejects model mismatch. These are disclosed engineering gates, not validated
statistical tests. Non-good below_detection values are not treated as exact data.

Successful responses include residual RMS, fitted background, and
`sensitivity_kg_h`: minimum/maximum rates under wind speed factors 0.8, 1, 1.2
and direction offsets -10, 0, +10 degrees where geometric prerequisites remain
satisfied. This envelope is assumption sensitivity, **not** a confidence interval,
coverage guarantee or complete uncertainty budget. Sensor lag is not deconvolved;
only quasi-steady plateaus should be submitted. The API does not establish plume
stationarity from wind alone. Background bounds, dispersion, geometry, atmospheric
state, source height, lag and obstacles introduce additional unquantified error.

SciPy is required for inverse fitting; absent SciPy returns scipy_unavailable.
SciPy was installed in the project virtual environment; dependency manifest
changes belong to the parent integration task.

## Verification and limits

Run `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_process.py backend/tests/test_quantification.py -q`.
Tests check deterministic conservation, positive Darcy loss and flow scaling,
compressor thermodynamics, unsupported boundaries/topology, multiple synthetic
rates, ENU/direction rotation, no leak, missing/QC wind, invalid units, sparse
sensors/samples, low/unstable wind, geometry and future-measurement isolation.
Synthetic test observations use the existing public forward dispersion function;
runtime inverse code implements its own equation. Agreement demonstrates only
same-family synthetic consistency, **not independent physical accuracy**. No
obstacle-aware, controlled-release, real sensor, field calibration or statistical
uncertainty validation has been performed. No hidden truth is required by tests.

## Mass-conserving side withdrawals (process method version 1.1)

`solve_process(facility, {'withdrawals_kg_s': {'XV-2104': 2.4/3600}})`
represents an explicitly prescribed 2.4 kg/h side withdrawal. The default JSON
contains `withdrawals_kg_s: {}` and remains the public baseline. The caller owns
scenario selection and any private scenario archive; this module reads no
scenario truth. Withdrawals must map canonical **open-train** asset IDs to finite,
nonnegative numeric kg/s values (booleans are rejected). Their sum must be
strictly less than inlet mass flow. Unknown and closed-branch IDs are rejected,
even for zero withdrawal.

Flow enters a node, passes its compressor if present, then splits into the
specified side withdrawal and the remaining downstream flow. A withdrawal at
K-201 is therefore after compression/cooling; compressor power and cooling duty
use the actual compressor inlet flow. Each edge independently recomputes its
Reynolds number, Darcy friction and pressure loss from its own remaining flow.
A withdrawal at V-101 occurs after inlet injection; a withdrawal at P-203 occurs
before the remaining outlet boundary withdrawal.

Each open node adds `inlet_mass_flow_kg_s`, `outlet_mass_flow_kg_s`, and
`side_withdrawal_kg_s`. Each edge adds `reynolds_number` and
`darcy_friction_factor`. Top-level additions are `inlet_mass_flow_kg_s`,
`outlet_mass_flow_kg_s` (the computed final boundary withdrawal), and
`total_side_withdrawal_kg_s`. The existing top-level Reynolds/friction values
refer to the first edge for compatibility; inspect individual edges when flows
vary. Flow sensor predictions represent the local node's remaining outlet flow.

The revised residual at each node is incoming edge flow minus outgoing edge
flow plus inlet boundary injection minus local side withdrawal minus final
outlet boundary withdrawal. Thus inlet flow equals total side withdrawals plus
computed outlet flow. At XV-2104, 2.4/3600 kg/s leaves all upstream edges,
compressor power and FT-201's 1800 kg/h prediction unchanged; downstream edges
carry 0.4993333333 kg/s and the remaining outlet rate is 1797.6 kg/h.

This is a steady prescribed withdrawal, not a pressure-dependent orifice model
or transient depressurization simulation. Fixed inlet flow/pressure and fixed
compressor ratio yield unchanged upstream pressure and slightly reduced downstream
Darcy losses (slightly higher outlet pressure). No strong pressure transient is
invented to accompany a small Gaussian emission. Parent integration must supply
the same emission mass rate to its dispersion and process scenarios; baseline
public process responses should continue to omit scenario withdrawals.

Additional tests cover local residuals and per-edge flows for the 2.4 kg/h case,
unchanged upstream predictions, small steady pressure differences, upstream and
compressor/outlet withdrawals, invalid withdrawal inputs, and default isolation.


## Plant history (synthetic, measured-data parameters)

`scripts/calibrate_history.py` derives parameters from measured METEC 2024 data:
the sonic-anemometer minute series (time-shifted onto 2026-09-06 to 2026-10-23,
direction rotated -40 degrees so the dominant sector matches the plant's westerly
design wind, speeds unchanged) and the AERIS-2m minutes with no METEC release
active (diurnal profile and AR(1) variability, re-centred on 2.0 ppm).
`scripts/generate_history.py` then writes 5-minute averages for the five methane
sensors and the weather station. Each 5-minute value averages five one-minute
Gaussian evaluations with the measured minute winds (calm minutes clamped to the
0.5 m/s model floor), so wind meander within a slot is present in the data but
unresolved by the analysis. Hidden episodes follow the maintenance storyline:
XV-2104 packing weeps (8-20 g/h) at the 06:00 and 17:30 load changes from
15 Sept, stops after the WO-009 re-torque (26 Sept), returns on 6 Oct and grows
to about 70 g/h, with a 140 g/h episode at the 14 Oct load change (WO-012), and a
5.3 g/h weep from 06:00 on 23 Oct that continues into SCN-001. Episode truth is
in `ground_truth/history_truth.json` only.

`app/history.py` analyzes only the public averages before the scenario start.
Background is the lowest methane reading at each time (one shared background
field); a sensor exceeds when above max(detection limit, 3x robust MAD noise).
Episodes are at least two slots with gaps of at most one. Each episode is fitted
at every positioned asset with the live unit response (no background term, since
it is removed) and labeled with the same `label_fits` rule. Because 5-minute mean
wind cannot resolve meander, history only narrows the area: XV-2104, F-205 and
P-202 each explain most testable episodes, and no rate is reported. Observability
is the share of slots in which a 50 g/h release from a candidate would exceed a
sensor threshold (about 36 percent for XV-2104 and F-205); quiet stretches report
it so silence with the wind blowing away is not read as no leak.
