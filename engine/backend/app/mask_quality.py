"""Independent segmentation diagnostics and causal replay freshness, not tracking."""
import math
import numpy as np


def mask_errors(predicted, truth, valid):
    arrays = [np.asarray(x) for x in (predicted, truth, valid)]
    if any(x.dtype != np.bool_ or x.ndim != 2 for x in arrays):
        raise ValueError('Expected two-dimensional boolean masks')
    predicted, truth, valid = arrays
    if predicted.shape != truth.shape or truth.shape != valid.shape or not valid.any():
        raise ValueError('Masks must share shape and have a nonempty valid region')
    tp = int((predicted & truth & valid).sum())
    fp = int((predicted & ~truth & valid).sum())
    fn = int((~predicted & truth & valid).sum())
    actual, proposed = tp + fn, tp + fp
    return dict(tp=tp, fp=fp, fn=fn, truth_pixels=actual, predicted_pixels=proposed,
                iou=tp / (tp + fp + fn) if tp + fp + fn else None,
                dice=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                omission_fraction=fn / actual if actual else None,
                commission_fraction=fp / proposed if proposed else None,
                area_ratio=proposed / actual if actual else None,
                # Net area alone hides displacement: retain FP and FN independently.
                net_area_error_pixels=fp - fn)


def prediction_at(run, source_seconds, max_age_seconds=.25):
    """Select a past sample only; never interpolate or extend beyond run exposure.

    Expired masks are withheld. A fresh zero prediction means no model alert,
    never proof that gas is absent. Age is source-video time, not live latency.
    """
    if not math.isfinite(source_seconds) or source_seconds < 0:
        raise ValueError('Source time must be finite and nonnegative')
    if not math.isfinite(max_age_seconds) or not 0 < max_age_seconds <= 1:
        raise ValueError('Mask age must be in (0, 1] seconds')
    start, end = run.get('start_seconds'), run.get('end_seconds')
    if start is None or end is None or not all(math.isfinite(x) for x in (start, end)) or start >= end:
        raise ValueError('Recorded run exposure is required')
    rows = run.get('predictions', [])
    times = [r.get('pts_seconds') for r in rows]
    if any(t is None or not math.isfinite(t) or not start <= t < end for t in times):
        raise ValueError('Invalid source prediction time')
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('Prediction times must be strictly increasing')
    result = dict(source_seconds=source_seconds, max_age_seconds=max_age_seconds,
                  prediction=None, prediction_age_seconds=None, sample_pts_seconds=None,
                  inference_mode='cached_sample_lookup_not_live_tracking',
                  gas_absence_established=False)
    if not start <= source_seconds < end:
        return {**result, 'status': 'outside_run'}
    previous = [r for r in rows if r['pts_seconds'] <= source_seconds]
    if not previous:
        return {**result, 'status': 'not_analyzed'}
    row = previous[-1]
    age = source_seconds - row['pts_seconds']
    result.update(prediction_age_seconds=age, sample_pts_seconds=row['pts_seconds'])
    if age > max_age_seconds:
        return {**result, 'status': 'stale'}
    return {**result, 'status': 'fresh', 'prediction': row}
