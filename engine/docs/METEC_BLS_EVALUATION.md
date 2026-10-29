# METEC multi-event rate evaluation: Gaussian vs bLs

Date: 2026-10-28. Offline developer evaluation; not a runtime route, not a field-accuracy claim.

On 16 isolated single-release METEC development events, a backward Lagrangian stochastic (bLs) model with estimated stability was within a factor of 2 of the recorded rate for 81% of events. Our current Gaussian plume managed 38%. This follows the protocol of [Mbua et al. 2025](https://amt.copernicus.org/articles/18/5687/2025/amt-18-5687-2025.html). **A pre-registered held-out test on 39 unseen days with other releases running failed:** bLs reached only 33% within ×2, still ahead of Gaussian at 25%, and both overestimated about 2–3×. A second pre-registered test, a joint multi-source fit with steady-wind gates on a different 10-sensor network, also failed for bLs (36% within ×2, median 2.78×). The pre-registered Gaussian comparator reached 61% (median 0.94) and the published MDLQ method 68%. A third pre-registered test, the same multi-source fit on our own single mast over six weeks, failed for every method (Gaussian 27% within ×2, bLs with flux-variance stability 32%; MDLQ's 10-sensor estimates 61% on the same windows). See the held-out, diagnosis, multi-source and mast sections below.

## Protocol

- **Events:** 66 releases of at least 20 min with no other release anywhere on site from 6 min before start to end (single-release single-point). Selection uses timing and geometry only.
- **Windows:** non-overlapping 5, 15 and 30 min chunks from start + 5 min to end − 2 min; ≥ 80% good minute coverage.
- **Wind gate:** angle between the window's vector-mean wind and the source-to-inlet bearing ≤ 5°, 10° or 20°.
- **Background:** 5th percentile of minute means in the 5 min before release; window minimum if that exceeds the window mean (paper's fallback).
- **Inlets:** AERIS 2 m and 4 m (one horizontal location). MGGA 3 m windows rarely pass coverage in the local 8 Hz subset.
- **Rate:** window-mean enhancement ÷ modelled unit response, per inlet. Recorded rates are read only for scoring.
- **Scoring:** per window (slope, R², median ratio, FAC2) and per event (median of that event's window ratios), because windows of one release are not independent.

## Results (15 min windows, event level)

| Method | Wind ±10°: median ratio | ±10°: within ×2 | Wind ±20°: median ratio | ±20°: within ×2 | ±20°: within 30% |
| --- | --- | --- | --- | --- | --- |
| Gaussian plume (current `response_ppm_per_kg_h`) | 0.32 | 33% | 0.77 | 38% | 19% |
| bLs, neutral | 0.68 | 42% | 0.74 | 69% | 31% |
| bLs, neutral, sonic σu/σv | 0.82 | 58% | 0.82 | 44% | 31% |
| bLs, σA stability (Golder L) | 1.27 | 75% | 1.26 | 81% | 56% |

Events: 12 at ±10°, 16 at ±20°. Recorded rates 0.32–3.47 kg/h. Full tables, including 5 and 30 min windows and window-level regressions: `docs/evidence/metec2024/multi_event/bls_scores.json` and `gaussian_scores.json`. Plot: `docs/evidence/metec2024/multi_event/bls_vs_gaussian.png`.

**Unchanged runtime estimator** (`app.metec.estimate`) on the same 15 min windows: 6 of 492 windows produced a rate; the rest were refused, mostly for insufficient coverage, low wind or no downwind response. Its two-inlet design rarely qualifies on this dataset.

## Findings

1. **Gaussian reads low on this dataset, not high as in the paper.** Our fixed spread coefficients (σy = 0.12x, σz = 0.08x) differ from the paper's EPA coefficients. Median ratio 0.3–0.8 depending on the wind gate.
2. **bLs is better on every robust metric.** The gain is largest beyond 25 m, where Gaussian and neutral bLs read about 2× low.
3. **Stability matters most.** 54 of 70 windows were daytime, mostly Pasquill A–B (unstable). Assuming neutral air under-mixes the plume and underestimates. The σA stability variant over-reads by about 25% instead.
4. **The worst misses are far, weak plumes.** 5S sources about 80 m away gave 0.005–0.03 ppm enhancements, at the noise floor. A minimum-enhancement gate would refuse these; it was not applied, to avoid tuning after seeing results.

## Important limitations

- **Stability is estimated, not measured.** The public `met.csv` `AT` column is a slow thermometer (σ ≈ 0.04 K day and night), so sonic heat flux and Obukhov length cannot be computed. The σA method (EPA-454/R-99-005 screening, simplified) plus Golder (1972) L is a standard but coarse substitute.
- **Small, unblinded sample.** 16 events, 0.3–3.5 kg/h, Feb–Mar 2024, one horizontal inlet location. The Gaussian baseline was scored before the bLs variants were defined; bLs variants were fixed before bLs scoring and none were tuned.
- **bLs is not cross-checked against WindTrax.** Internal checks pass (below), but an independent run of the authors' WindTrax configuration is still needed.
- **Not source localization.** All estimates are conditioned on the true release point. Multi-release periods are excluded.

## bLs model (`backend/app/bls.py`)

Thomson (1987) Gaussian inhomogeneous model with correlated u′w′, run backward in time (Flesch, Wilson and Yee 1995). It uses MOST surface-layer profiles, C0 = 3.1, dt = 0.025 TL, perfect reflection at z0 preserving the joint (u′, w′) distribution, and residence time in a 1 m × 1 m × 0.5 m box per source. Inputs: u* from double-rotated 3 m sonic covariances, L, z0 = 0.1 m (paper), and 50,000 particles per window (~2 s).

Verification (`backend/tests/test_bls.py`):

- **Well-mixed condition:** a uniform cloud stays uniform within 12%, w′ variance within 5% and u′w′ within 15%, in neutral, unstable and stable air.
- **Reciprocity:** backward response matches an independent forward simulation within 1–3% at 200,000 particles (within 15% in the test at 60,000).
- **Geometry and units:** response falls with distance and crosswind offset; frame and ppm conversions checked.

## Reproduce

```sh
.venv-detector/bin/python scripts/prepare_metec_turbulence.py     # ~5 min, 6.3 GB met.csv -> per-minute sonic moments
.venv-detector/bin/python scripts/evaluate_metec_multi_event.py   # windows + Gaussian + runtime estimator
.venv-detector/bin/python scripts/evaluate_metec_bls.py           # bLs variants, ~6 min on 12 workers
.venv-detector/bin/python scripts/plot_metec_bls.py
.venv-detector/bin/python scripts/evaluate_metec_heldout.py --mode heldout   # frozen held-out test
.venv-detector/bin/python scripts/diagnose_metec_heldout.py                   # post hoc diagnosis (needs pip install rdata)
.venv-detector/bin/python scripts/evaluate_mdlq_multisource.py estimate      # ~6 min on 12 workers, no labels
.venv-detector/bin/python scripts/evaluate_mdlq_multisource.py score         # refuses unless the freeze matches
.venv-detector/bin/python scripts/evaluate_mast_multisource.py estimate      # ~5 min on 13 workers, no labels
.venv-detector/bin/python scripts/evaluate_mast_multisource.py score
.venv/bin/python scripts/run_tests.py backend/tests/test_bls.py backend/tests/test_bls_footprint.py
```

## Pre-registered held-out test (failed)

The development result did not hold on unseen days. Cut-offs, held-out definition and pass criteria were frozen in `docs/evidence/metec2024/heldout/protocol.json` (frozen 2026-10-28T15:54:13Z; hashes of the protocol, `bls.py` and `metec.py` in `freeze.sha256`) before any held-out estimate existed. The test ran once.

- **Held-out days:** 39 UTC dates touched by no development release. No isolated single releases were left unseen, so targets are multi-release single-point releases: other releases may run, but any within 45° of the wind as seen from the inlet refuses the window.
- **Gates:** wind ≥ 1 m/s, u* ≥ 0.15 m/s, sector ≤ 20° (≤ 10° secondary), pre-release background must not exceed the window mean, enhancement ≥ max(0.05 ppm, 3 × background SD).
- **Volume:** 445 target releases → 15,655 window-inlet records → 28 passed all gates, 25 with every method → 12 events. Refusals: 10,067 coverage, 3,815 sector, 972 wind, 444 u*, 320 upwind neighbour, 8 signal, 1 background.

| Method (15 min, ±20°, 12 events) | Event median ratio | Within ×2 | Within 30% |
| --- | --- | --- | --- |
| Gaussian plume | 2.88 | 25% | 0% |
| bLs, σA stability (primary) | 2.12 | 33% | 8% |
| bLs, neutral | 1.69 | 25% | 17% |
| bLs, neutral, sonic σu/σv | 1.86 | 25% | 8% |

| Criterion | Target | Result |
| --- | --- | --- |
| S1 bLs FAC2 | ≥ 0.60 | 0.33, **fail** |
| S2 bLs beats Gaussian | FAC2 higher | 0.33 vs 0.25, pass |
| S3 bLs median ratio | 0.67–1.5 | 2.12, **fail** |

At ±10° (9 events), bLs σA had median ratio 0.86 and 44% within ×2; Gaussian had 0.48 and 33%. Development data re-scored under the same gates (6 events) passed all three criteria. That is expected and is not evidence. Files: `heldout_scores.json`, `heldout_windows.json`, `dev_scores.json`.

**First post hoc reading (superseded by the diagnosis below).** The overestimates are driven by five small releases (0.3–0.43 kg/h) at 79–94 m on pads 5S/5W, where the inlet saw 0.8–3 ppm. I first called that 10–100× more than physically possible and the excess unexplained. That was wrong: a stable night-time plume at 80 m can give ~1–30 ppm per kg/h. See the diagnosis.

## Diagnosis of the held-out failure (post hoc, not evidence)

`scripts/diagnose_metec_heldout.py` → `docs/evidence/metec2024/heldout/diagnosis.json`. Three hypotheses tested with minute data:

- **Release log errors (ruled out).** An independent copy of the METEC 2024 log (MDLQ ground truth, Daniels et al. 2026) matches ours minute for minute in every scored window. Rates differ by a constant 0.934, which is methane vs whole gas. No unlogged release overlaps a failing window.
- **Unlogged or persistent sources (ruled out).** With nothing logged as releasing, enhancement is 0.01–0.06 ppm. The excess only appears while releases run.
- **Neighbour plumes and pooling (supported).** Neighbours start and stop with the target, so timing alone cannot separate them. Within the release period, minutes with a neighbour straight upwind read ~1 ppm vs 0.3–0.8 ppm with only the target upwind. Even minutes with no source upwind read 0.05–0.6 ppm: gas pooling on site in light wind.
- **Wrong model inputs (supported; my earlier "ruled out" was wrong).** Every large failure had σθ 30–81° (the 15 min mean wind direction is meaningless), u*/U up to 0.57 (surface-layer values are ~0.1–0.2, so non-turbulent meandering inflates u*), and three were at night, where the σA screen forces neutral. Windows within ×2 had σθ ≤ 19° and u*/U ≤ 0.18.

## Pre-registered multi-source test on a second sensor network (failed for bLs)

The fixes from the diagnosis (wind-steadiness gates and a joint fit of all candidate sources) were frozen in `docs/evidence/metec2024/multisource/protocol.json` (2026-10-28T23:17:13Z; protocol, `bls.py`, `bls_footprint.py` and `metec.py` hashed in `freeze.sha256`). Estimates were then computed without labels, and the estimates file and script were hashed (23:24:47Z) before scoring once.

- **Data:** the public MDLQ sample (Daniels et al. 2026, MIT): 10 CMS sensors at 2 m around the METEC pads, 12–19 Feb 2024, 5 equipment-group candidates. **Semi-independent:** the sensors, geometry and multi-source fit are new, but the days overlap our development and held-out days, the sonic is the same, and the gates came from the diagnosis.
- **Method:** 30 min windows; per-sensor background (p05 over ±45 min); non-negative least squares of every sensor-minute on per-minute unit responses of all 5 candidates. bLs uses one footprint per window (`backend/app/bls_footprint.py`, verified against `bls.unit_response`).
- **Gates:** wind ≥ 1 m/s, u* ≥ 0.15 m/s, σθ ≤ 30°, u*/U ≤ 0.3, daytime. 344 windows → 74 pass → 56 scored source-windows.

| Method (primary, 56 source-windows) | Within ×2 | Median ratio | False attribution (> 0.2 kg/h on an idle group) | Site total within ×2 (33 windows) |
| --- | --- | --- | --- | --- |
| bLs, σA stability (primary) | 36% | 2.78 | 30% | 42% |
| bLs, neutral | 57% | 1.40 | 16% | 61% |
| Gaussian (runtime spreads, minute wind) | 61% | 0.94 | 11% | 67% |
| MDLQ published estimates (reference) | 68% | 1.23 | 3% | 67% |

Criteria: S1 FAC2 ≥ 0.5 **fail**, S2 median 0.67–1.5 **fail**, S3 beats Gaussian **fail**, S4 false attribution ≤ 0.2 **fail**. Full tables, including ungated and night windows: `scores.json`.

**Findings:**

1. **bLs with σA stability failed a second time.** It overestimates ~2.9× in σA classes A and B (Golder L −8 to −15 m) and 1.5× in class C. The development win came from the stability guess, and it does not carry over.
2. **The gates help every method.** Removing G4–G6 drops MDLQ from 68% to 42% within ×2 and moves the Gaussian median from 0.94 to 0.66.
3. **A multi-source Gaussian fit with steady-wind gates did well here.** It was a pre-registered comparator, so its score is fair, but choosing it as the method now is a post hoc pick among three. It needs one more frozen test on unseen data before any claim.

**What this means:** single-source rates break when other releases run, and 15 min means hide meandering wind. Refuse unsteady-wind windows (σθ > 30°, u*/U > 0.3, night), and fit all candidate sources together. Do not use the σA-stability bLs; without measured heat flux its stability is the weak link.

## Pre-registered six-week test on our own mast (failed)

The same gates and multi-source fit, frozen in `docs/evidence/metec2024/mast_multisource/protocol.json` (protocol, models and both earlier scripts hashed in `freeze.sha256`; the script and label-free `estimates.json` hashed before the single scoring run).

- **Data:** our AERIS 2 m, AERIS 4 m and MGGA 3 m inlets (one location, MGGA 9 m away), 10 Feb–28 Mar 2024 minus the MDLQ sample week. **New method, previously seen days:** these inlets and days were used in the single-release tests.
- **New stability input:** flux-variance, z/L from σw/u* at the 3 m sonic (Kaimal and Finnigan 1994), using measured turbulence rather than σθ.
- **Reference:** MDLQ's published full-experiment estimates (public in their repo, 5 Feb–28 Apr) on the same windows and candidates. They come from a 10-sensor network, so this is a benchmark, not like for like.
- **Windows:** 1912 → 1102 pass G1–G3 → 502 pass all gates → 213 with an identifiable candidate. 221 windows where our log and the MDLQ ground truth disagree on which groups release were excluded for every method. 132 scored source-windows.

| Method (primary) | Within ×2 | Median ratio | False attribution | Site total within ×2 |
| --- | --- | --- | --- | --- |
| Gaussian multi-source (primary) | 27% | 0.34 | 17% | 37% |
| bLs, flux-variance stability | 32% | 0.44 | 18% | 42% |
| bLs, σA stability | 31% | 0.67 | 22% | 39% |
| bLs, neutral | 32% | 0.44 | 18% | 42% |
| MDLQ published estimates, same windows (110 source-windows) | 61% | 0.75 | 4% | 63% |

Criteria: P1 Gaussian FAC2 ≥ 0.5 **fail**, P2 median 0.67–1.5 **fail**, P3 false attribution ≤ 0.2 pass. Q1 flux-variance beats σA passes only nominally (0.32 vs 0.31), Q2 **fail**, Q3 beats Gaussian passes.

**Findings:**

1. **One mast cannot do the multi-source fit.** The same Gaussian method that reached 61% on the 10-sensor network reached 27% here, reading about 3× low. MDLQ's 10-sensor estimates on the same half-hours reached 61%. Separating sources needs sensors that see them from different directions.
2. **Flux-variance stability gave nothing.** It found no unstable window, so it equals neutral bLs. Post hoc (not evidence): median σw/u* in gated windows is 0.97 (5–95%: 0.79–1.18), below the textbook neutral 1.25, while σA called 320 of 502 windows class A–C. A 3 m sonic among equipment sits in the roughness sublayer, where similarity relations do not hold; u* and σw there cannot give stability. Only measured heat flux (sonic temperature) can, preferably from a sonic mounted higher and clear of equipment.
3. **σA "unstable" classes on this site likely reflect meandering, not convection.** Both σw/u* and u*/U (median 0.12) say near-neutral turbulence in those windows.

## Next steps

1. ~~Pre-register gates and test on held-out days~~: done, failed (above).
2. ~~Diagnose the failure; pre-register steady-wind gates and a multi-source fit~~: done; bLs σA failed again, the multi-source Gaussian did well (above).
3. ~~Six-week test on our own mast with flux-variance stability~~: done, failed (above). The multi-source Gaussian is still unconfirmed; it needs a multi-sensor network.
4. The full MDLQ CMS dataset (337 releases, 5 Feb–30 Apr 2024) would confirm it but is only available on request; set aside by the user for now. TADI 2019 lacks published coordinates; TADI 2024 has no raw concentrations.
5. **Own controlled release** is now the main route: a 3D sonic with fast temperature output (measured heat flux), mounted clear of equipment; methane sensors at several positions around the source (at least 3 directions for multi-source fitting); accurate release log; co-located OGI camera to test detection and quantification together.
6. Optional: run the authors' WindTrax file (`supplement.zip` → `GPIM_bLs/WindTrax_bLs_Configuration_Single_Point.wxf`) to cross-check `bls.py`.
7. Do not present rate accuracy to Dell beyond "rough estimate, steady wind, daytime".
