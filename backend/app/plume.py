"""Gaussian plume model (ground-reflected, steady state). Pure Python, deterministic.

Shared by scripts/gen_scenario.py (forward: make the simulated GD-01 trace) and the
interim estimator in main.py (inverse). The backend team will replace the estimator with pyELQ.
"""
import math

# Briggs rural sigma_y, sigma_z (m) at downwind distance x (m), per Pasquill-Gifford class.
BRIGGS_RURAL = {
    "A": (lambda x: 0.22 * x * (1 + 0.0001 * x) ** -0.5, lambda x: 0.20 * x),
    "B": (lambda x: 0.16 * x * (1 + 0.0001 * x) ** -0.5, lambda x: 0.12 * x),
    "C": (lambda x: 0.11 * x * (1 + 0.0001 * x) ** -0.5, lambda x: 0.08 * x * (1 + 0.0002 * x) ** -0.5),
    "D": (lambda x: 0.08 * x * (1 + 0.0001 * x) ** -0.5, lambda x: 0.06 * x * (1 + 0.0015 * x) ** -0.5),
    "E": (lambda x: 0.06 * x * (1 + 0.0001 * x) ** -0.5, lambda x: 0.03 * x * (1 + 0.0003 * x) ** -1),
    "F": (lambda x: 0.04 * x * (1 + 0.0001 * x) ** -0.5, lambda x: 0.016 * x * (1 + 0.0003 * x) ** -1),
}
# mg/m3 -> ppm for CH4 at 25 C, 1 atm (molar volume 24.45 L, molar mass 16.04 g/mol).
MG_M3_TO_PPM = 24.45 / 16.04


def ppm_per_kg_h(src, rec, wind_speed, wind_from_deg, stability="D"):
    """Methane enhancement (ppm) at rec from a 1 kg/h point source at src. 0 if rec is upwind."""
    t = math.radians(wind_from_deg)
    wx, wy = -math.sin(t), -math.cos(t)  # direction the wind blows toward
    dx, dy = rec["x_m"] - src["x_m"], rec["y_m"] - src["y_m"]
    x = dx * wx + dy * wy
    if x <= 0 or wind_speed <= 0:
        return 0.0
    y = dx * wy - dy * wx
    fy, fz = BRIGGS_RURAL[stability]
    sy, sz = fy(x), fz(x)
    h, z = src["z_m"], rec["z_m"]
    q_mg_s = 1e6 / 3600
    c = q_mg_s / (2 * math.pi * wind_speed * sy * sz) * math.exp(-y * y / (2 * sy * sy)) * (
        math.exp(-((z - h) ** 2) / (2 * sz * sz)) + math.exp(-((z + h) ** 2) / (2 * sz * sz)))
    return c * MG_M3_TO_PPM
