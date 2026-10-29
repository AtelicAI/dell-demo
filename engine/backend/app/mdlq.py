"""MDLQ-style inversion: Gaussian puffs + spike-and-slab Bayesian model over every source the sensors can see.

After the method of Daniels et al. (2026, MDLQ): each candidate source either leaks (slab: half-normal rate) or does
not (spike at zero). Every set of up to three leaking sources is weighed by its evidence, which gives each source a
leak probability and a rate range. Our Python
implementation on the puff responses in app.puff; not the authors' R code.
"""
import math
import numpy as np
from itertools import combinations
from scipy.optimize import nnls
from . import puff

METHOD = 'mdlq-style-puff-spike-and-slab'
WINDOW_S = 120
PRIOR_LEAK = .1          # prior probability that any one source leaks
SLAB_SCALE_KG_H = 2.     # half-normal scale of a leaking source's rate
MAX_SOURCES = 3          # largest set of simultaneous leaks weighed
MIN_RESPONSE = .01       # ppm per kg/h: a source no sensor can see is not a candidate
THIN_S = 10              # one reading per 10 s per sensor: the sensors' 8 s time constant makes consecutive readings repeat


def fit(facility, rows, asset_id, end, seed=0):
    base = dict(method=METHOD, status='rate_unavailable', rate_kg_h=None, candidate_asset_id=asset_id)
    assets = [a for a in facility.assets]
    A, y, t = puff.design(facility, rows, [a.position for a in assets], end, WINDOW_S)
    if len(y) == 0:
        return {**base, 'reason': 'missing_wind_or_methane'}
    seen = [j for j in range(A.shape[1]) if A[:, j].max() > MIN_RESPONSE]
    if not any(assets[j].id == asset_id for j in seen):
        return {**base, 'reason': 'no_sensor_downwind'}
    A, ids = A[:, seen], [assets[j].id for j in seen]
    keep = t % THIN_S == 0
    A, y = A[keep], y[keep]
    sensors = [s for s in facility.sensors if s.variable == 'methane']
    s2 = max(s.uncertainty for s in sensors) ** 2   # measurement noise from the sensor specification
    n, k = A.shape
    # Spike-and-slab over which sources leak: every set of up to MAX_SOURCES leaking sources is weighed by its evidence
    # (Laplace approximation around the non-negative fit; half-normal slab on each rate, flat background).
    log_prior_on, log_prior_off = math.log(PRIOR_LEAK), math.log(1 - PRIOR_LEAK)
    models = []
    for m in range(1, MAX_SOURCES + 1):
        for S in combinations(range(k), m):
            X = np.column_stack((A[:, S], np.ones(n)))
            coef, _ = nnls(X, y)
            if (coef[:m] <= 0).any():
                continue                      # a zero rate makes it a smaller model, already counted
            r = y - X @ coef
            H = X.T @ X / s2
            H[np.arange(m), np.arange(m)] += 1 / SLAB_SCALE_KG_H ** 2
            _, logdet = np.linalg.slogdet(H)
            log_ev = (-(r @ r) / (2 * s2) + m * math.log(2 / (math.sqrt(2 * math.pi) * SLAB_SCALE_KG_H))
                      - (coef[:m] ** 2).sum() / (2 * SLAB_SCALE_KG_H ** 2) + (m + 1) / 2 * math.log(2 * math.pi) - logdet / 2)
            cov = np.linalg.inv(H)
            models.append((log_ev + m * log_prior_on + (k - m) * log_prior_off, S, coef[:m], np.sqrt(np.diag(cov)[:m])))
    if not models:
        return {**base, 'reason': 'no_source_explains_the_readings'}
    w = np.array([mm[0] for mm in models])
    w = np.exp(w - w.max())
    w /= w.sum()
    rng = np.random.default_rng(seed)
    draws = {j: [] for j in range(k)}
    for (_, S, coef, sd), weight in zip(models, w):
        n_draw = int(round(weight * 4000))
        for j, c, e in zip(S, coef, sd):
            draws[j].extend(np.maximum(rng.normal(c, e, n_draw), 0).tolist())
    prob = np.zeros(k)
    for (_, S, _, _), weight in zip(models, w):
        prob[list(S)] += weight
    best = models[int(np.argmax(w))]
    sources = []
    for j, aid in enumerate(ids):
        on = np.array(draws[j])
        sources.append(dict(asset_id=aid, leak_probability=round(float(min(prob[j], 1.)), 3),
                            rate_if_leaking_kg_h=round(float(on.mean()), 4) if len(on) else None,
                            rate_p05_kg_h=round(float(np.percentile(on, 5)), 4) if len(on) else None,
                            rate_p95_kg_h=round(float(np.percentile(on, 95)), 4) if len(on) else None))
    sources.sort(key=lambda s: -s['leak_probability'])
    mine = next(s for s in sources if s['asset_id'] == asset_id)
    out = {**base, 'sources': sources[:6], 'sources_considered': k, 'models_weighed': len(models), 'window_seconds': WINDOW_S,
           'readings_used': n, 'most_probable_set': [ids[j] for j in best[1]],
           'site_total_kg_h': round(float(sum(w_ * c.sum() for (_, _, c, _), w_ in zip(models, w))), 4)}
    if mine['leak_probability'] < .5:
        return {**out, 'reason': 'candidate_leak_probability_below_half', 'leak_probability': mine['leak_probability']}
    return {**out, 'status': 'estimated', 'reason': None, 'rate_kg_h': mine['rate_if_leaking_kg_h'], 'leak_probability': mine['leak_probability'],
            'sensitivity_kg_h': dict(min=mine['rate_p05_kg_h'], max=mine['rate_p95_kg_h'], kind='posterior_5th_to_95th_percentile')}
