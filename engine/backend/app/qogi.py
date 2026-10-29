"""Camera quantification (QOGI): emission rate from the infrared plume alone, no anemometer.

Integrated mass enhancement (Frish et al.; Varon et al. 2018): Q = IME x U / L, where IME is the methane mass in the
plume summed over its pixels, L the square root of the plume area and U the plume speed measured in the footage.
Column mass per pixel comes from apparent contrast through the camera's radiometric gain, set once on a metered
reference release recorded by the same camera.
"""
import json
import math
import statistics
from functools import lru_cache
from .data import DATA

METHOD = 'qogi-integrated-mass-enhancement'
WINDOW_S = 90.
MIN_FRAME_COVERAGE = .5       # share of window frames with a plume mask
STEADY_TOLERANCE = .3         # early/late half-window medians must agree within 30%
MIN_THERMAL_CONTRAST_K = 2.   # below this the gas is not imaged reliably


@lru_cache(maxsize=None)
def inputs(clip_id):
    path = DATA / f'quantification/qogi/{clip_id}.json'
    return json.loads(path.read_text()) if path.is_file() else None


@lru_cache(maxsize=None)
def radiometry():
    return json.loads((DATA / 'quantification/camera_radiometry.json').read_text())


def _raw(frame, pixel_m):
    """IME x U / L in contrast units; the gain turns it into kg/h."""
    _, pixels, contrast, speed = frame
    area = pixels * pixel_m * pixel_m
    return contrast * pixel_m * pixel_m * speed / math.sqrt(area)


@lru_cache(maxsize=None)
def gain(clip_id):
    """kg/h per raw unit for the camera model, from the reference release frames (median over the calibration span)."""
    d = inputs(clip_id)
    lo, hi = d['calibration']['span_s']
    ratios = [d['calibration']['metered_rate_g_h'] / 1000 / r for f in d['frames'] if lo <= f[0] < hi and (r := _raw(f, d['pixel_m'])) > 0]
    return statistics.median(ratios)


def estimate(camera_id, clip_id, clip_end_seconds):
    """Rate over the last WINDOW_S of footage ending at clip_end_seconds, or a refusal with a reason."""
    base = dict(method=METHOD, camera_id=camera_id, clip_id=clip_id, status='rate_unavailable', rate_kg_h=None)
    camera = radiometry()['cameras'].get(camera_id)
    d = inputs(clip_id) if clip_id else None
    if camera is None or d is None:
        return {**base, 'reason': 'no_radiometric_stream'}
    delta_t = camera['background_apparent_c'] - camera['atmospheric_c']
    # Past the end of the recorded footage the latest tracked window stands (the camera keeps watching the same plume).
    held = clip_end_seconds > d['incident_span_s'][1]
    clip_end_seconds = min(clip_end_seconds, d['incident_span_s'][1])
    window = (clip_end_seconds - WINDOW_S, clip_end_seconds)
    frames = [f for f in d['frames'] if window[0] <= f[0] <= window[1]]
    expected = WINDOW_S * d['fps']
    rates = [gain(camera['calibration_clip']) * _raw(f, d['pixel_m']) for f in frames]
    details = dict(window_clip_seconds=[round(window[0], 1), round(window[1], 1)], latest_window_held=held, frames_used=len(frames),
                   frames_in_window=round(expected), distance_m=d['distance_m'], pixel_size_mm=round(d['pixel_m'] * 1000, 1),
                   thermal_contrast_k=round(delta_t, 1), background_apparent_c=camera['background_apparent_c'],
                   atmospheric_c=camera['atmospheric_c'], reference_release=camera['reference_release'],
                   median_plume_area_m2=round(statistics.median(f[1] for f in frames) * d['pixel_m'] ** 2, 3) if frames else None,
                   median_plume_speed_m_s=round(statistics.median(f[3] for f in frames), 3) if frames else None)
    if abs(delta_t) < MIN_THERMAL_CONTRAST_K:
        return {**base, **details, 'reason': 'low_thermal_contrast'}
    if window[0] < d['incident_span_s'][0] or len(frames) < MIN_FRAME_COVERAGE * expected:
        return {**base, **details, 'reason': 'plume_not_tracked'}
    middle = sum(window) / 2
    early = statistics.median(r for f, r in zip(frames, rates) if f[0] < middle)
    late = statistics.median(r for f, r in zip(frames, rates) if f[0] >= middle)
    details['steady_state_check'] = dict(early_half_rate_kg_h=early, late_half_rate_kg_h=late, max_relative_change=STEADY_TOLERANCE)
    if abs(late - early) > STEADY_TOLERANCE * max(early, late):
        return {**base, **details, 'reason': 'camera_plume_not_steady'}
    q = sorted(rates)
    return {**base, **details, 'status': 'estimated', 'reason': None, 'rate_kg_h': statistics.median(rates),
            'sensitivity_kg_h': dict(min=q[len(q) // 4], max=q[3 * len(q) // 4], kind='frame_to_frame_interquartile_range_not_confidence_interval')}
