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
from . import qogi

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


def decide(case, gaussian):
    """Role and reason for every registered tool. Primary: the tool whose number the incident uses."""
    wind_ok = case['wind'] == 'available'
    single = case['attribution'] == 'agrees_single'
    stable_txt = {'stable': 'stable night air', 'unstable': 'unstable daytime air', 'near_neutral': 'near-neutral air'}.get(case['stability'])
    spread = case['wind_direction_spread_deg']
    d = {}
    if wind_ok:
        d['gaussian_candidate_fit'] = ('primary', f"{case['methane_sensors_reporting']} methane sensors reporting, "
                                       f"{case['camera_candidates']} candidate locations from the camera and steady wind "
                                       f"(direction spread {spread:.1f}°): fit every candidate and keep the ones the readings support")
    else:
        d['gaussian_candidate_fit'] = ('blocked', 'Needs wind speed and direction; the weather mast is not reporting')
    if not wind_ok or not case['sonic']:
        d['bls'] = ('blocked', 'Needs the sonic anemometer for turbulence and stability; the weather mast is not reporting')
    elif not single:
        d['bls'] = ('waiting', 'Runs once the readings single out one source; candidates are still being separated')
    else:
        d['bls'] = ('cross_check', f"One source left, sensors within {BLS_REACH_M:.0f} m and {stable_txt} measured by the sonic "
                                   f"(L = {case['sonic']['obukhov_m']:+.0f} m): particle model checks the plume model's rate")
    d['gaussian_puff'] = (('not_needed', f'Wind direction steady (spread {spread:.1f}°, 30° allowed); a single plume shape holds')
                          if wind_ok and case['wind_steady'] else
                          ('not_needed', 'No wind readings to carry puffs') if not wind_ok else
                          ('recommended', f'Wind turning (spread {spread:.0f}°): puffs follow the changing direction'))
    many = 'several sources leaking at once, over hours of network data'
    d['pyelq'] = ('not_needed', f"Built for {many}; the camera alarm narrowed this to {case['camera_candidates']} candidates in one window")
    d['mdlq'] = ('not_needed', f"Built for {many} and changing wind; one camera-flagged release here")
    if not case['camera_radiometric']:
        d['qogi'] = ('blocked', 'Camera does not stream radiometric frames')
    elif wind_ok:
        d['qogi'] = ('not_needed', 'Sensors and wind give a rate tied to one source; the camera stays on detection')
    else:
        d['qogi'] = ('primary', 'No wind data, but the camera sees the plume: mass from the radiometric frame, speed from the footage')
    d['cfd'] = ('not_needed', 'Open skid on a desert pad, no enclosure; air flows freely around the equipment')
    return d


def run(facility, rows, end, gaussian, attribution, candidates, camera_id, clip_id, clip_seconds, candidate_asset_id):
    """Gaussian result in, incident result out: primary tool's number plus every tool's decision and outputs."""
    case = assess(facility, rows, end, attribution, candidates, camera_id)
    decisions = decide(case, gaussian)
    outputs = {'gaussian_candidate_fit': {k: gaussian.get(k) for k in ('status', 'reason', 'rate_kg_h', 'sensitivity_kg_h', 'residual_rms_ppm')}}
    if decisions['bls'][0] == 'cross_check' and gaussian['status'] == 'estimated':
        outputs['bls'] = bls_fit(facility, rows, candidate_asset_id, end, case)
    if decisions['qogi'][0] == 'primary':
        outputs['qogi'] = qogi.estimate(camera_id, clip_id, clip_seconds) if clip_seconds is not None else \
            dict(method=qogi.METHOD, status='rate_unavailable', rate_kg_h=None, reason='no_footage_at_this_time')
    primary = next((t for t, (role, _) in decisions.items() if role == 'primary'), None)
    tools = [dict(id=m['id'], label=m['label'], connected=m['connected'], role=decisions[m['id']][0], reason=decisions[m['id']][1],
                  output=outputs.get(m['id'])) for m in registry()['methods']]
    result = {**gaussian, 'case': case, 'tools': tools, 'primary_tool': primary}
    if primary == 'qogi':
        q = outputs['qogi']
        result.update(method=q['method'], status=q['status'], rate_kg_h=q['rate_kg_h'], sensitivity_kg_h=q.get('sensitivity_kg_h'),
                      camera_window_clip_seconds=q.get('window_clip_seconds'), steady_state_check=q.get('steady_state_check'),
                      assumptions=['Integrated mass enhancement from the radiometric plume frame; no anemometer',
                                   'Plume speed from phase correlation of consecutive frames inside the detector mask',
                                   'Radiometric gain from a metered reference release recorded by the same camera',
                                   'Steady plume required: early/late half-window rates must agree within 30%'])
        # Camera still waiting for a steady, fully tracked window: retry as the clock advances.
        result['reason'] = None if q['status'] == 'estimated' else (
            'waiting_for_camera_window' if q['reason'] in ('plume_not_tracked', 'camera_plume_not_steady') else gaussian['reason'])
    if outputs.get('bls', {}).get('status') == 'estimated' and gaussian.get('rate_kg_h'):
        b = outputs['bls']['rate_kg_h']
        result['cross_check'] = dict(tool='bls', rate_kg_h=b, primary_rate_kg_h=gaussian['rate_kg_h'],
                                     ratio=round(b / gaussian['rate_kg_h'], 3), agrees=.67 <= b / gaussian['rate_kg_h'] <= 1.5)
    return result
