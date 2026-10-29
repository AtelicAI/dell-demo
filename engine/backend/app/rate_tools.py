"""Quantify agent's tool choice: read the case, pick the rate tool built for it, run cross-checks.

Deterministic rules, so the same inputs always give the same rate. Every tool in the registry gets a decision and a
reason, including the ones not used, so the engineer sees why this tool and not another.
"""
import json
import math
from datetime import datetime, timedelta
from functools import lru_cache
import numpy as np
from .bls import SurfaceLayer, ppm_per_kg_h, wind_frame
from .bls_footprint import footprint
from .data import DATA
from . import cfd, mdlq, puff, qogi

WINDOW_S = 60
STEADY_SPREAD_DEG = 30.
MAST_STALE_S = 5
BLS_REACH_M = 50.
STABLE_L_MAX_M = 200.   # 0 < L < 200 m: stable; |L| >= 200 m: near neutral; L < 0: unstable
SONIC = {'friction_velocity': 'ustar_m_s', 'obukhov_length': 'obukhov_m', 'sensible_heat_flux': 'heat_flux_w_m2'}


@lru_cache(maxsize=None)
def registry():
    return json.loads((DATA / 'quantification/methods.json').read_text())


def _time(x):
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace('Z', '+00:00'))


def window_rows(facility, rows, end):
    """Good readings in the last WINDOW_S, grouped by timestamp and variable."""
    sensors = {s.id: s for s in facility.sensors}
    end = _time(end)
    out = []
    for r in rows:
        s = sensors.get(r['sensor_id'])
        t = _time(r['timestamp_utc'])
        if s is None or not end - timedelta(seconds=WINDOW_S) <= t <= end:
            continue
        out.append((t, s, r))
    return out


def assess(facility, rows, end, attribution, candidates, camera_id):
    """What the agent knows about the case before choosing a tool."""
    win = window_rows(facility, rows, end)
    good = [(t, s, r) for t, s, r in win if r['quality_flag'] == 'good' and r['value'] is not None]
    by_var = {}
    for t, s, r in good:
        by_var.setdefault(s.variable, []).append(r['value'])
    # A mast that stopped reporting is down, even while older readings are still inside the window.
    last_wind = max((t for t, s, r in good if s.id.startswith('MET-')), default=None)
    if last_wind is None or _time(end) - last_wind > timedelta(seconds=MAST_STALE_S):
        by_var = {k: v for k, v in by_var.items() if k not in ('wind_speed', 'wind_direction', *SONIC)}
    speeds, dirs = by_var.get('wind_speed', []), by_var.get('wind_direction', [])
    spread = None
    if dirs:
        c, sn = np.mean(np.cos(np.radians(dirs))), np.mean(np.sin(np.radians(dirs)))
        spread = math.degrees(math.sqrt(max(0., -2 * math.log(max(1e-9, math.hypot(c, sn))))))
    sonic = {key: float(np.median(by_var[var])) for var, key in SONIC.items() if by_var.get(var)}
    L = sonic.get('obukhov_m')
    stability = ('not_measured' if L is None else 'unstable' if L < 0 else 'stable' if L < STABLE_L_MAX_M else 'near_neutral')
    camera = qogi.radiometry()['cameras'].get(camera_id) or {}
    consistent = (attribution or {}).get('consistent_camera_candidates') or []
    return dict(
        window_seconds=WINDOW_S,
        methane_sensors_reporting=len({s.id for t, s, r in good if s.variable == 'methane'}),
        wind='available' if speeds and dirs else 'missing',
        wind_speed_m_s=round(float(np.mean(speeds)), 2) if speeds else None,
        wind_direction_spread_deg=round(spread, 1) if spread is not None else None,
        wind_steady=spread is not None and spread <= STEADY_SPREAD_DEG,
        stability=stability, sonic=sonic or None,
        camera_candidates=len(candidates), candidates_consistent_with_readings=len(consistent),
        attribution=(attribution or {}).get('status'),
        camera_id=camera_id, camera_radiometric=bool(camera.get('radiometric_stream')),
        enclosed=registry()['site']['enclosed'])


@lru_cache(maxsize=16)
def _footprint(ustar, obukhov, z0, receptor_height, source_heights):
    return footprint(SurfaceLayer(ustar, obukhov, z0), receptor_height, source_heights, reach=BLS_REACH_M, cell=.5, particles=20000)


def bls_fit(facility, rows, asset_id, end, case):
    """Rate for one source from the sensors in the window, through the measured surface layer."""
    from scipy.optimize import lsq_linear
    base = dict(method='backward-lagrangian-stochastic-footprint', status='rate_unavailable', rate_kg_h=None, candidate_asset_id=asset_id)
    if not case['sonic'] or 'ustar_m_s' not in case['sonic'] or 'obukhov_m' not in case['sonic']:
        return {**base, 'reason': 'stability_not_measured'}
    site = registry()['site']
    source = next(a for a in facility.assets if a.id == asset_id).position
    win = [(t, s, r) for t, s, r in window_rows(facility, rows, end) if r['quality_flag'] == 'good' and r['value'] is not None]
    wind = {}
    for t, s, r in win:
        if s.variable.startswith('wind_'):
            wind.setdefault(t, {})[s.variable] = r['value']
    samples = [(s, r['value'], wind[t]['wind_direction']) for t, s, r in win
               if s.variable == 'methane' and {'wind_speed', 'wind_direction'} <= wind.get(t, {}).keys()]
    ustar, L, z0 = round(case['sonic']['ustar_m_s'], 2), float(round(case['sonic']['obukhov_m'])), site['surface_roughness_m']
    heights = tuple(sorted({round(a.position[2], 2) for a in facility.assets}))

    def fit(obukhov):
        fp = _footprint(ustar, obukhov, z0, site['receptor_height_m'], heights)
        resp = np.array([float(ppm_per_kg_h(fp.response(*wind_frame(source, s.position, d)), 293.15, 101325.)) for s, v, d in samples])
        y = np.array([v for s, v, d in samples])
        if (resp > .01).sum() < 3:
            return None
        design = np.column_stack((resp, np.ones(len(y))))
        solved = lsq_linear(design, y, bounds=([0, 0], [np.inf, 5]))
        return dict(rate_kg_h=float(solved.x[0]), background_ppm=float(solved.x[1]),
                    residual_rms_ppm=float(np.sqrt(np.mean((design @ solved.x - y) ** 2))))
    main = fit(L)
    if main is None:
        return {**base, 'reason': 'no_sensor_in_footprint'}
    variants = [v['rate_kg_h'] for v in (fit(float(round(L / 2))), fit(float(round(L * 2)))) if v]
    return {**base, **main, 'status': 'estimated', 'reason': None,
            'surface_layer': dict(ustar_m_s=ustar, obukhov_m=L, z0_m=z0, stability=case['stability']),
            'particles': 20000, 'sensitivity_kg_h': dict(min=min(variants + [main['rate_kg_h']]), max=max(variants + [main['rate_kg_h']]),
                                                         kind='obukhov_length_halved_and_doubled_not_confidence_interval')}


CAMERA_WAIT_REASONS = ('plume_not_tracked', 'camera_plume_not_steady')
AGREE = (.67, 1.5)
GAUSSIAN_ASSUMPTIONS_TAG = 'Steady open-terrain reflected Gaussian'


def decide(case, camera):
    """Role and reason for every registered tool. Camera first: the plume it sees gives the rate; sensor models check it."""
    wind_ok = case['wind'] == 'available'
    single = case['attribution'] == 'agrees_single'
    stable_txt = {'stable': 'stable night air', 'unstable': 'unstable daytime air', 'near_neutral': 'near-neutral air'}.get(case['stability'])
    spread = case['wind_direction_spread_deg']
    cam_ok = case['camera_radiometric'] and camera is not None and camera.get('reason') not in ('no_radiometric_stream', 'no_footage_at_this_time', 'low_thermal_contrast')
    d = {}
    if not case['camera_radiometric']:
        d['qogi'] = ('blocked', 'Camera does not stream radiometric frames')
    elif not cam_ok:
        d['qogi'] = ('blocked', (camera or {}).get('reason', 'no footage').replace('_', ' '))
    else:
        d['qogi'] = ('primary', 'The camera sees the plume and streams radiometric frames: rate straight from the image (plume mass × plume speed)'
                     + ('. The only tool that runs without the weather mast' if not wind_ok else ', independent of the wind'))
    if wind_ok:
        role = 'cross_check' if cam_ok else 'primary'
        d['gaussian_candidate_fit'] = (role, f"{case['methane_sensors_reporting']} methane sensors and steady wind (direction spread {spread:.1f}°): "
                                       + ("checks the camera's number and tells which candidate the readings support" if cam_ok else
                                          f"fit all {case['camera_candidates']} camera candidates and keep the ones the readings support"))
    else:
        d['gaussian_candidate_fit'] = ('blocked', 'Needs wind speed and direction; the weather mast is not reporting')
    if not wind_ok or not case['sonic']:
        d['bls'] = ('blocked', 'Needs the sonic anemometer for turbulence and stability; the weather mast is not reporting')
    elif not single:
        d['bls'] = ('waiting', 'Runs once the readings single out one source; candidates are still being separated')
    else:
        d['bls'] = ('cross_check', f"One source left, sensors within {BLS_REACH_M:.0f} m and {stable_txt} measured by the sonic "
                                   f"(L = {case['sonic']['obukhov_m']:+.0f} m): particle model checks the rate through the measured turbulence")
    d['gaussian_puff'] = (('not_needed', f'Wind direction steady (spread {spread:.1f}°, 30° allowed): puffs would trace the same plume as the Gaussian')
                          if wind_ok and case['wind_steady'] else
                          ('blocked', 'Needs wind to carry the puffs; the weather mast is not reporting') if not wind_ok else
                          ('cross_check', f'Wind turning (spread {spread:.0f}°): puffs follow the changing direction'))
    if not wind_ok:
        d['mdlq'] = ('blocked', 'Needs wind to carry the puffs; the weather mast is not reporting')
        d['cfd'] = ('blocked', 'Needs wind and sonic turbulence to drive the flow; the weather mast is not reporting')
    else:
        d['mdlq'] = ('cross_check', f"Weighs every source the sensors can see, alone or leaking together: leak probability for each, "
                                    f"not just the {case['camera_candidates']} the camera flagged")
        d['cfd'] = (('cross_check', 'One source left: solves the airflow around the separator and compressor to check the equipment does not '
                                    'bend the plume away from the sensors') if single else
                    ('waiting', 'Runs once the readings single out one source; about 1 s of flow solving per candidate'))
    return d


def _camera(camera_id, clip_id, clip_seconds):
    if not clip_id:
        return dict(method=qogi.METHOD, status='rate_unavailable', rate_kg_h=None, reason='no_footage_at_this_time')
    # The replay clock runs past the recorded clip (it maps to no clip time); the camera keeps its latest tracked window.
    return qogi.estimate(camera_id, clip_id, clip_seconds if clip_seconds is not None else math.inf)


def run(facility, rows, end, gaussian, attribution, candidates, camera_id, clip_id, clip_seconds, candidate_asset_id, tool=None):
    """Gaussian result in, incident result out: the primary tool's number, every tool's decision, and the cross-checks.

    tool: run this one as primary (an engineer's request) instead of the agent's choice; the agent's roles are still reported.
    """
    case = assess(facility, rows, end, attribution, candidates, camera_id)
    camera = _camera(camera_id, clip_id, clip_seconds) if case['camera_radiometric'] else None
    decisions = decide(case, camera)
    outputs = {'gaussian_candidate_fit': {k: gaussian.get(k) for k in ('method', 'status', 'reason', 'rate_kg_h', 'sensitivity_kg_h', 'residual_rms_ppm')}}
    if camera is not None:
        outputs['qogi'] = camera
    wind_ok = case['wind'] == 'available'
    # An engineer's request runs only the tool asked for; the agent's own pass runs every cross-check its roles call for.
    want = lambda t: tool == t if tool else decisions[t][0] == 'cross_check'
    if want('bls') and wind_ok:
        outputs['bls'] = bls_fit(facility, rows, candidate_asset_id, end, case)
    if want('gaussian_puff') and wind_ok:
        outputs['gaussian_puff'] = puff.fit(facility, rows, candidate_asset_id, end)
    if want('mdlq') and wind_ok:
        outputs['mdlq'] = mdlq.fit(facility, rows, candidate_asset_id, end)
    if want('cfd') and wind_ok:
        outputs['cfd'] = cfd.fit(facility, rows, candidate_asset_id, end, case, [c['asset_id'] for c in candidates])
    agent_primary = next((t for t, (role, _) in decisions.items() if role == 'primary'), None)
    primary = tool or agent_primary
    known = {m['id']: m for m in registry()['methods']}
    if tool and (tool not in known or not known[tool]['connected']):
        out = dict(status='rate_unavailable', rate_kg_h=None, reason='tool_not_deployed_on_this_edge_box')
    else:
        out = outputs.get(primary) or dict(status='rate_unavailable', rate_kg_h=None, reason=decisions.get(primary, ('', 'not run'))[1])
    tools = [dict(id=m['id'], label=m['label'], connected=m['connected'], role=decisions[m['id']][0], reason=decisions[m['id']][1],
                  output=outputs.get(m['id'])) for m in registry()['methods']]
    result = {**gaussian, 'case': case, 'tools': tools, 'primary_tool': primary, 'agent_primary_tool': agent_primary,
              'requested_tool': tool}
    if primary != 'gaussian_candidate_fit':
        result.update(method=out.get('method', primary), status=out['status'], rate_kg_h=out['rate_kg_h'],
                      sensitivity_kg_h=out.get('sensitivity_kg_h'), steady_state_check=out.get('steady_state_check'),
                      reason=out.get('reason'))
    if primary == 'qogi':
        result.update(camera_window_clip_seconds=out.get('window_clip_seconds'),
                      assumptions=['Integrated mass enhancement from the radiometric plume frame; no anemometer',
                                   'Plume speed from phase correlation of consecutive frames inside the detector mask',
                                   'One radiometric gain for the camera model, from a metered reference release',
                                   'Steady plume required: early/late half-window rates must agree within 30%',
                                   'Past the end of the recorded footage, the latest tracked window stands'])
        # Camera still waiting for a steady, fully tracked window: retry as the clock advances.
        if out['status'] != 'estimated':
            result['reason'] = 'waiting_for_camera_window' if out.get('reason') in CAMERA_WAIT_REASONS else gaussian['reason']
    # Other tools' numbers against the primary: the sensors and models say whether the camera's rate holds.
    p = result['rate_kg_h']
    checks = []
    for tid in ('gaussian_candidate_fit', 'bls', 'mdlq', 'cfd', 'gaussian_puff', 'qogi'):
        o = outputs.get(tid)
        if tid == primary or not o or o.get('status') != 'estimated' or not p:
            continue
        if decisions[tid][0] != 'cross_check' and tid != agent_primary:
            continue
        ratio = o['rate_kg_h'] / p
        checks.append(dict(tool=tid, label=known[tid]['label'], rate_kg_h=o['rate_kg_h'], ratio=round(ratio, 3), agrees=AGREE[0] <= ratio <= AGREE[1]))
    result['cross_checks'] = checks
    return result
