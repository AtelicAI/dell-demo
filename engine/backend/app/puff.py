"""Gaussian puff rate fit: puffs released every 0.25 s, each carried by the wind of its own second.

Unlike the steady plume, the plume can bend as the wind turns. Same spread laws as the plume model
(sigma = 0.12 x horizontally, 0.08 x vertically, x = distance travelled) and a ground reflection.
"""
import math
from datetime import datetime, timedelta
import numpy as np

METHOD = 'gaussian-puff-fit'
RELEASE_DT_S = .25
MAX_AGE_S = 60
PPM_PER_KG_M3 = 8.314462618 * 293.15 / (101325 * .01604) * 1e6


def _time(x):
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace('Z', '+00:00'))


def design(facility, rows, sources, end, window_s=60):
    """Unit responses (ppm per kg/h) of every methane reading in the window to each source position, the readings and their times."""
    sensors = {s.id: s for s in facility.sensors}
    end = _time(end)
    wind = {}
    for r in rows:
        s = sensors.get(r['sensor_id'])
        if s and s.variable in ('wind_speed', 'wind_direction') and r['quality_flag'] == 'good' and r['value'] is not None:
            wind.setdefault(int(_time(r['timestamp_utc']).timestamp()), {})[s.variable] = r['value']
    vel = {}
    for t, w in wind.items():
        if len(w) == 2:
            th = math.radians(w['wind_direction'])  # FROM direction: air moves the opposite way
            vel[t] = (-w['wind_speed'] * math.sin(th), -w['wind_speed'] * math.cos(th))
    samples = [(sensors[r['sensor_id']], r['value'], int(_time(r['timestamp_utc']).timestamp())) for r in rows
               if sensors.get(r['sensor_id']) and sensors[r['sensor_id']].variable == 'methane' and r['quality_flag'] == 'good'
               and r['value'] is not None and end - timedelta(seconds=window_s) <= _time(r['timestamp_utc']) <= end]
    ages = np.arange(RELEASE_DT_S, MAX_AGE_S, RELEASE_DT_S)
    idx = np.minimum((ages - 1e-9).astype(int), MAX_AGE_S - 1)
    frac = ages - idx
    sources = np.atleast_2d(np.asarray(sources, float))
    rows_a, y, times = [], [], []
    for s, v, t in samples:
        # Puff centres: integrate the per-second wind back from the sample time.
        steps = [vel.get(t - k) for k in range(1, MAX_AGE_S + 1)]
        if any(x is None for x in steps):
            continue
        cum = np.cumsum(np.array(steps), axis=0)
        prev = np.vstack(([0., 0.], cum))[idx]
        disp = prev + (cum[idx] - prev) * frac[:, None]
        travel = np.hypot(disp[:, 0], disp[:, 1])
        sh, sz = np.maximum(.5, .12 * travel), np.maximum(.4, .08 * travel)
        mass = RELEASE_DT_S / 3600  # kg per puff at 1 kg/h
        # All sources at once: (sources, puffs) arrays.
        dx = s.position[0] - (sources[:, :1] + disp[None, :, 0])
        dy = s.position[1] - (sources[:, 1:2] + disp[None, :, 1])
        z, h = s.position[2], sources[:, 2:3]
        c = mass / ((2 * math.pi) ** 1.5 * sh * sh * sz) * np.exp(-(dx * dx + dy * dy) / (2 * sh * sh)) * \
            (np.exp(-(z - h) ** 2 / (2 * sz * sz)) + np.exp(-(z + h) ** 2 / (2 * sz * sz)))
        rows_a.append(c.sum(axis=1) * PPM_PER_KG_M3)
        y.append(v)
        times.append(t)
    return np.array(rows_a).reshape(-1, len(sources)), np.array(y), np.array(times, int)


def fit(facility, rows, asset_id, end, window_s=60):
    from scipy.optimize import lsq_linear
    base = dict(method=METHOD, status='rate_unavailable', rate_kg_h=None, candidate_asset_id=asset_id)
    source = next(a for a in facility.assets if a.id == asset_id).position
    A, y, _ = design(facility, rows, [source], end, window_s)
    if len(y) == 0:
        return {**base, 'reason': 'missing_wind_or_methane'}
    resp = A[:, 0]
    if (resp > .01).sum() < 3:
        return {**base, 'reason': 'no_sensor_downwind'}
    X = np.column_stack((resp, np.ones(len(y))))
    solved = lsq_linear(X, y, bounds=([0, 0], [np.inf, 5]))
    return {**base, 'status': 'estimated', 'reason': None, 'rate_kg_h': float(solved.x[0]), 'background_ppm': float(solved.x[1]),
            'residual_rms_ppm': float(np.sqrt(np.mean((X @ solved.x - y) ** 2))), 'puffs_per_sample': int(MAX_AGE_S / RELEASE_DT_S)}
