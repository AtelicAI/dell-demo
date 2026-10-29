"""Strict, dependency-free held-out binary detection evaluation.

Rows join on (video_id, frame_id); pts_seconds must also agree. Label rows
require label (0/1), label_source, review_status, split='test', group_id.
Human labels additionally require reviewer and reviewed_at; protocol labels
require protocol_id and explicit opt-in. No inference output is ground truth.
"""
import math
from collections import defaultdict
from numbers import Real


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    return float(value)


def _index(rows):
    indexed = {}
    for row in rows:
        key = (row['video_id'], row['frame_id'])
        if not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in key):
            raise ValueError('video_id/frame_id must be string or integer identifiers')
        if key in indexed:
            raise ValueError(f'duplicate frame: {key}')
        _number(row['pts_seconds'], 'pts_seconds')
        indexed[key] = row
    return indexed


def validate_group_split(records):
    """Reject recording-session groups occurring in multiple dataset splits."""
    seen = {}
    for row in records:
        group, split = row['group_id'], row['split']
        if not group or split not in {'train', 'validation', 'test', 'excluded', 'unassigned'}:
            raise ValueError('invalid group or split')
        if group in seen and seen[group] != split:
            raise ValueError(f'group leakage: {group}')
        seen[group] = split
    return True


def evaluate_detection(predictions, labels, *, allow_weak_labels=False,
                       pts_tolerance_seconds=0.001,
                       false_alarm_merge_gap_seconds=1.0, runtime=None):
    """Return JSON-safe metrics; empty/pending labels produce no accuracy.

    Predictions: video_id, frame_id, pts_seconds, prediction (0/1), optional
    score (finite ranking score, not assumed calibrated). Labels use the same
    IDs/PTS and the provenance fields above. Optional duration_seconds gives
    each sampled frame's independently defined exposure; absent exposure means
    false-alarm rates are unavailable. Never infer exposure across sparse gaps.
    Optional runtime is measured parent-supplied data, preserved as supplied.
    """
    tolerance = _number(pts_tolerance_seconds, 'pts tolerance')
    gap = _number(false_alarm_merge_gap_seconds, 'event merge gap')
    pred, truth = _index(predictions), _index(labels)
    validate_group_split(truth.values())
    pending = {key for key, row in truth.items() if row.get('label') is None}
    active = {key: row for key, row in truth.items() if key not in pending}
    for key, row in truth.items():
        source = row.get('label_source')
        if source not in {'human', 'source_protocol', 'pending'}:
            raise ValueError('pseudo/model/prediction ground truth is prohibited')
        if key in pending:
            continue
        if row['split'] != 'test' or row.get('used_for_training', False):
            raise ValueError('evaluation requires held-out test labels')
        if source == 'human':
            if row.get('review_status') != 'human_reviewed' or not row.get('reviewer') or not row.get('reviewed_at'):
                raise ValueError('human review provenance required')
        elif source == 'source_protocol':
            if not allow_weak_labels or row.get('review_status') != 'protocol_derived_unreviewed' or not row.get('protocol_id'):
                raise ValueError('weak labels require opt-in and protocol provenance')
        else:
            raise ValueError('pending labels cannot contain truth')
    if active and set(pred) != set(active):
        raise ValueError('prediction/label frame IDs must match exactly; no dropping or interpolation')
    result = dict(status='label_pending' if not active else 'evaluated', n=len(active),
                  pending_label_count=len(pending), prediction_count=len(pred),
                  label_basis=sorted({r['label_source'] for r in active.values()}),
                  accuracy=None, balanced_accuracy=None, precision=None, recall=None,
                  f1=None, auroc=None, tp=0, tn=0, fp=0, fn=0,
                  false_alarm_frames_per_minute=None, false_alarm_events_per_minute=None,
                  false_alarm_events=None, negative_exposure_seconds=None,
                  false_alarm_reason='No evaluated per-frame exposure labels',
                  iou=None, iou_reason='Independent pixel ground truth not supplied',
                  score_interpretation='Uncalibrated ranking score; not probability',
                  runtime=runtime)
    if not active:
        return result
    scores, positive, negative = [], 0, 0
    exposure_complete, negative_intervals = True, defaultdict(list)
    video_pts = defaultdict(set)
    for key, label in active.items():
        prediction = pred[key]
        if abs(prediction['pts_seconds'] - label['pts_seconds']) > tolerance:
            raise ValueError(f'PTS mismatch: {key}')
        pts = float(label['pts_seconds'])
        if pts in video_pts[key[0]]:
            raise ValueError('duplicate PTS in video')
        video_pts[key[0]].add(pts)
        y, p = label['label'], prediction['prediction']
        if type(y) not in (int, bool) or y not in (0, 1) or type(p) not in (int, bool) or p not in (0, 1):
            raise ValueError('binary label/prediction required')
        result[{(1, 1): 'tp', (0, 0): 'tn', (0, 1): 'fp', (1, 0): 'fn'}[y, p]] += 1
        positive += y
        negative += 1-y
        if prediction.get('score') is not None:
            score = prediction['score']
            if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score):
                raise ValueError('score must be finite')
            scores.append((float(score), y))
        if y == 0:
            duration = label.get('duration_seconds')
            if duration is None:
                exposure_complete = False
            else:
                duration = _number(duration, 'duration_seconds')
                if duration <= 0:
                    raise ValueError('duration_seconds must be positive')
                negative_intervals[key[0]].append((pts, pts + duration, p))
    tp, tn, fp, fn = (result[k] for k in ('tp', 'tn', 'fp', 'fn'))
    result.update(accuracy=(tp+tn)/len(active),
                  balanced_accuracy=(tp/positive+tn/negative)/2 if positive and negative else None,
                  precision=tp/(tp+fp) if tp+fp else None,
                  recall=tp/positive if positive else None,
                  f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)
    if len(scores) == len(active) and positive and negative:
        # Mann-Whitney rank statistic with tied scores receiving mean rank.
        ordered = sorted(scores)
        rank_sum, i = 0.0, 0
        while i < len(ordered):
            j = i+1
            while j < len(ordered) and ordered[j][0] == ordered[i][0]:
                j += 1
            rank_sum += sum(y for _, y in ordered[i:j]) * (i+1+j)/2
            i = j
        result['auroc'] = (rank_sum-positive*(positive+1)/2)/(positive*negative)
    if negative and exposure_complete:
        exposure, events = 0.0, 0
        for intervals in negative_intervals.values():
            previous_end, alarm_end = None, None
            for start, end, alarm in sorted(intervals):
                if previous_end is not None and start < previous_end - tolerance:
                    raise ValueError('negative exposure intervals overlap')
                # A gap or an observed non-alarm terminates a contiguous event.
                if previous_end is not None and start > previous_end + tolerance:
                    alarm_end = None
                exposure += end-start
                if alarm:
                    if alarm_end is None or start-alarm_end > gap:
                        events += 1
                    alarm_end = end
                else:
                    alarm_end = None
                previous_end = end
        result.update(negative_exposure_seconds=exposure, false_alarm_events=events,
                      false_alarm_frames_per_minute=fp*60/exposure,
                      false_alarm_events_per_minute=events*60/exposure, false_alarm_reason=None)
    return result


def binary_mask_iou(predicted_mask, truth_mask, *, label_source, review_status,
                    reviewer=None, reviewed_at=None):
    """Pixel IoU only for independent human-reviewed boolean masks; no resizing."""
    if label_source != 'human' or review_status != 'human_reviewed' or not reviewer or not reviewed_at:
        raise ValueError('independent reviewed pixel truth required')
    import numpy as np
    predicted, truth = np.asarray(predicted_mask), np.asarray(truth_mask)
    if predicted.shape != truth.shape or predicted.ndim != 2 or not predicted.size:
        raise ValueError('masks must have identical nonempty 2D shape')
    if not np.isin(predicted, [0, 1]).all() or not np.isin(truth, [0, 1]).all():
        raise ValueError('binary masks required')
    union = np.logical_or(predicted, truth).sum()
    return float(np.logical_and(predicted, truth).sum()/union) if union else None
