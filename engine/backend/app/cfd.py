"""CFD + inversion: steady 3-D flow around the equipment and methane transport through it, then a rate fit.

Fast diagnostic solver, not RANS or LES: a mass-consistent wind field (stable log profile from the sonic's L, forced
around the separator and compressor blocks by a pressure-like correction, as in QUIC-URB/CALMET-type models), then
steady advection-diffusion on a 1 m x 1 m x 0.5 m finite-volume grid. Near a source the plume is in Taylor's
near-field regime (spread grows in proportion to travel distance), so each source's eddy diffusivity grows with
distance from it: K = i^2 U x with turbulence intensities from the sonic (sigma_v = 1.9 u*, sigma_w = 1.25 u*).
Each candidate gets its own concentration field; a non-negative fit against the window's readings gives the rates.
"""
import math
from datetime import datetime, timedelta
from functools import lru_cache
import numpy as np
from scipy.optimize import nnls
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

METHOD = 'cfd-mass-consistent-advection-diffusion-inversion'
DX, DZ = 1., .5
X0, X1, Y0, Y1, Z1 = -4., 44., -8., 22., 8.
SIGMA_V_PER_USTAR, SIGMA_W_PER_USTAR = 1.9, 1.25   # surface-layer turbulence (Kaimal & Finnigan 1994)
BLOCK_KINDS = ('separator', 'compressor')
PLUME_HEIGHT_M = 1.2   # the profile carries the mast speed at plume height, as the other rate tools assume
PPM_PER_KG_M3 = 8.314462618 * 293.15 / (101325 * .01604) * 1e6


def _time(x):
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace('Z', '+00:00'))


def grid():
    xs = np.arange(X0 + DX / 2, X1, DX)
    ys = np.arange(Y0 + DX / 2, Y1, DX)
    zs = np.arange(DZ / 2, Z1, DZ)
    return xs, ys, zs


def cell(p):
    xs, ys, zs = grid()
    return (int(np.clip((p[0] - X0) // DX, 0, len(xs) - 1)), int(np.clip((p[1] - Y0) // DX, 0, len(ys) - 1)),
            int(np.clip(p[2] // DZ, 0, len(zs) - 1)))


@lru_cache(maxsize=8)
def solve(blocks, wind_from_deg, speed, ustar, obukhov, z0, sources):
    """Concentration (ppm per kg/h) fields for each source, as an array (n_sources, nx, ny, nz), plus the solid mask."""
    xs, ys, zs = grid()
    nx, ny, nz = len(xs), len(ys), len(zs)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    solid = np.zeros((nx, ny, nz), bool)
    for cx, cy, w, d, h in blocks:
        solid |= (abs(X - cx) <= w / 2) & (abs(Y - cy) <= d / 2) & (Z <= h)
    fluid = ~solid
    # Stable log profile carrying the mast speed at plume height.
    psi = lambda z: -5 * z / obukhov if obukhov > 0 else 0. * z
    u = speed * (np.log(Z / z0) - psi(Z)) / (math.log(PLUME_HEIGHT_M / z0) - psi(PLUME_HEIGHT_M))
    th = math.radians(wind_from_deg)
    ux, uy = np.where(fluid, -u * math.sin(th), 0), np.where(fluid, -u * math.cos(th), 0)   # air moves away from FROM
    idx = np.arange(nx * ny * nz).reshape(nx, ny, nz)
    ax, az = DX * DZ, DX * DX            # face areas: side faces, top/bottom faces
    # Faces: (low cells, high cells, open mask, first-guess velocity, area, spacing)
    faces = [((slice(None, -1),), (slice(1, None),), fluid[:-1] & fluid[1:], .5 * (ux[:-1] + ux[1:]), ax, DX),
             ((slice(None), slice(None, -1)), (slice(None), slice(1, None)), fluid[:, :-1] & fluid[:, 1:], .5 * (uy[:, :-1] + uy[:, 1:]), ax, DX),
             ((slice(None), slice(None), slice(None, -1)), (slice(None), slice(None), slice(1, None)), fluid[:, :, :-1] & fluid[:, :, 1:],
              np.zeros((nx, ny, nz - 1)), az, DZ)]
    # Open domain sides and top: (cells, outward first-guess velocity, area, spacing). The ground is closed.
    bounds = [((0,), -ux[0], ax, DX), ((-1,), ux[-1], ax, DX), ((slice(None), 0), -uy[:, 0], ax, DX),
              ((slice(None), -1), uy[:, -1], ax, DX), ((slice(None), slice(None), -1), np.zeros((nx, ny)), az, DZ)]

    def assemble(face_terms, bound_diag, extra_diag=None):
        rows, cols, vals, diag = [], [], [], np.zeros((nx, ny, nz))
        for (a, b, *_), (ca, cb, da, db) in zip(faces, face_terms):
            rows += [idx[a].ravel(), idx[b].ravel()]; cols += [idx[b].ravel(), idx[a].ravel()]; vals += [ca.ravel(), cb.ravel()]
            diag[a] += da; diag[b] += db
        for (sl, *_), d in zip(bounds, bound_diag):
            diag[sl] += d
        if extra_diag is not None:
            diag += extra_diag
        diag[solid] = 1.
        rows.append(idx.ravel()); cols.append(idx.ravel()); vals.append(diag.ravel())
        return coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(idx.size,) * 2).tocsc()
    # Mass consistency: sum_f c_f (phi_nb - phi_P) - sum_b c_b phi_P = net first-guess outflow, c = area / spacing.
    netout = np.zeros((nx, ny, nz))
    for a, b, open_, v, area, h in faces:
        netout[a] += v * area * open_; netout[b] -= v * area * open_
    for sl, vout, area, h in bounds:
        netout[sl] += vout * area * fluid[sl]
    L = assemble([(area / h * o, area / h * o, -area / h * o, -area / h * o) for a, b, o, v, area, h in faces],
                 [-2 * area / h * fluid[sl] for sl, vout, area, h in bounds])
    phi = spsolve(L, np.where(fluid, netout, 0).ravel()).reshape(nx, ny, nz)
    # Corrected volume fluxes through faces (+ along the axis) and outward through the open boundaries.
    F = [(v - (phi[b] - phi[a]) / h) * area * o for a, b, o, v, area, h in faces]
    Fb = [(vout + 2 * phi[sl] / h) * area * fluid[sl] for sl, vout, area, h in bounds]
    # Near-field eddy diffusivity for each source: K = i^2 U x, x = distance travelled downwind of that source.
    iv, iw = SIGMA_V_PER_USTAR * ustar / speed, SIGMA_W_PER_USTAR * ustar / speed
    dx_, dy_ = -math.sin(th), -math.cos(th)
    fields = []
    for src in sources:
        travel = np.maximum((X - src[0]) * dx_ + (Y - src[1]) * dy_, DX / 2)
        kh, kz = iv * iv * speed * travel, iw * iw * speed * travel
        K = [.5 * (kh[:-1] + kh[1:]), .5 * (kh[:, :-1] + kh[:, 1:]), .5 * (kz[:, :, :-1] + kz[:, :, 1:])]
        Kb = [kh[0], kh[-1], kh[:, 0], kh[:, -1], kz[:, :, -1]]
        terms = []
        for (a, b, o, v, area, h), f, k in zip(faces, F, K):
            D = k * area / h * o
            # Upwind advection + central diffusion; matrix rows are cell balances (outflow - inflow = source).
            terms.append((-np.maximum(-f, 0) - D, -np.maximum(f, 0) - D, np.maximum(f, 0) + D, np.maximum(-f, 0) + D))
        bdiag = [np.maximum(fb, 0) + 2 * k * area / h * fluid[sl] for (sl, vout, area, h), fb, k in zip(bounds, Fb, Kb)]
        rhs = np.zeros(idx.size)
        rhs[idx[cell(src)]] = 1 / 3600          # 1 kg/h in kg/s into the source cell
        fields.append(spsolve(assemble(terms, bdiag), rhs).reshape(nx, ny, nz))
    C = np.array(fields) * PPM_PER_KG_M3   # cell balances in m3/s and kg/s give kg/m3; -> ppm per kg/h
    return C, solid


def fit(facility, rows, asset_id, end, case, candidates, window_s=60):
    base = dict(method=METHOD, status='rate_unavailable', rate_kg_h=None, candidate_asset_id=asset_id)
    if case.get('wind') != 'available' or not case.get('sonic'):
        return {**base, 'reason': 'missing_wind_or_methane'}
    sensors = {s.id: s for s in facility.sensors}
    end = _time(end)
    win = [(sensors[r['sensor_id']], r['value'], r) for r in rows if sensors.get(r['sensor_id']) and r['quality_flag'] == 'good'
           and r['value'] is not None and end - timedelta(seconds=window_s) <= _time(r['timestamp_utc']) <= end]
    dirs = [v for s, v, r in win if s.variable == 'wind_direction']
    c_, s_ = np.mean(np.cos(np.radians(dirs))), np.mean(np.sin(np.radians(dirs)))
    wind_from = round(math.degrees(math.atan2(s_, c_)) % 360)
    speed = round(case['wind_speed_m_s'], 1)
    sonic = case['sonic']
    assets = {a.id: a for a in facility.assets}
    ids = [asset_id] + [c for c in candidates if c != asset_id and c in assets]
    blocks = tuple((a.position[0], a.position[1], a.dimensions[0], a.dimensions[1], a.dimensions[2])
                   for a in facility.assets if a.kind in BLOCK_KINDS)
    C, solid = solve(blocks, wind_from, speed, round(sonic['ustar_m_s'], 2), float(round(sonic['obukhov_m'])), .03,
                     tuple(tuple(assets[i].position) for i in ids))
    methane = [(s, v) for s, v, r in win if s.variable == 'methane']
    if not methane:
        return {**base, 'reason': 'missing_wind_or_methane'}
    resp = np.array([[C[k][cell(s.position)] for k in range(len(ids))] for s, v in methane])
    y = np.array([v for s, v in methane])
    if (resp[:, 0] > .01).sum() < 3:
        return {**base, 'reason': 'no_sensor_downwind'}
    X = np.column_stack((resp, np.ones(len(y))))
    coef, _ = nnls(X, y)
    rates = {i: float(coef[k]) for k, i in enumerate(ids)}
    xs, ys, zs = grid()
    return {**base, 'status': 'estimated', 'reason': None, 'rate_kg_h': rates[asset_id], 'background_ppm': float(coef[-1]),
            'residual_rms_ppm': float(np.sqrt(np.mean((X @ coef - y) ** 2))), 'joint_rates_kg_h': rates,
            'grid': dict(cells=int(len(xs) * len(ys) * len(zs)), resolution_m=[DX, DX, DZ], solid_cells=int(solid.sum()),
                         wind_from_deg=wind_from, wind_speed_m_s=speed)}
