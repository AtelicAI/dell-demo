"""Independent, source-coordinate benchmark metrics. No inference or filtering.

All ratios are fractions. Undefined metrics are None, including empty/empty
IoU. Label-negative is an annotation statement, never physical methane absence.
Missing, stale and failed predictions remain unavailable. Operational reporting
separately penalizes their known reference foreground as omitted output, without
fabricating a prediction mask or awarding background true-negative credit.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from numbers import Integral

import numpy as np

UNAVAILABLE_STATES = frozenset({'unknown', 'uninitialized', 'stale', 'tracking_failure',
                              'tracking_failed', 'tracking-failure', 'failed', 'error', 'disconnected',
                              'inference_failure', 'inference_failed'})
INFERENCE_FAILURE_STATES = frozenset({'tracking_failure', 'tracking_failed', 'tracking-failure',
                                      'failed', 'error', 'inference_failure', 'inference_failed'})
RATIOS = ('iou', 'dice', 'precision', 'recall', 'omission', 'commission',
          'foreground_fraction', 'predicted_fraction')


def _divide(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def _mean(values):
    defined = [float(v) for v in values if v is not None]
    return sum(defined) / len(defined) if defined else None


def _binary(mask):
    value = np.asarray(mask)
    if value.ndim != 2 or not value.size or value.dtype.kind not in 'buif' or not np.isin(value, [0, 1]).all():
        raise ValueError('Require nonempty 2D binary 0/1 masks; no implicit thresholding')
    return value.astype(bool, copy=False)


def _pair(mask, truth):
    predicted, reference = _binary(mask), _binary(truth)
    if predicted.shape != reference.shape:
        raise ValueError('Masks must have identical source geometry; resizing is prohibited')
    return predicted, reference


def metrics_from_counts(tp, fp, fn, tn):
    counts = (tp, fp, fn, tn)
    if any(isinstance(v, bool) or not isinstance(v, Integral) or v < 0 for v in counts):
        raise ValueError('TP/FP/FN/TN must be nonnegative integer pixel counts')
    tp, fp, fn, tn = map(int, counts)
    total = tp + fp + fn + tn
    ratios = dict(iou=_divide(tp, tp + fp + fn), dice=_divide(2 * tp, 2 * tp + fp + fn),
        precision=_divide(tp, tp + fp), recall=_divide(tp, tp + fn),
        omission=_divide(fn, tp + fn), commission=_divide(fp, tp + fp),
        foreground_fraction=_divide(tp + fn, total), predicted_fraction=_divide(tp + fp, total))
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, total_pixels=total,
        foreground_pixels=tp + fn, predicted_pixels=tp + fp, **ratios,
        percentages={key: None if value is None else 100 * value for key, value in ratios.items()})


def _boundary(mask):
    padded = np.pad(mask, 1, constant_values=False)
    interior = (mask & padded[:-2, 1:-1] & padded[2:, 1:-1] &
                padded[1:-1, :-2] & padded[1:-1, 2:])
    return mask & ~interior


def _within_two_pixels(boundary):
    # Euclidean disk of radius two, clipped at the image boundary; no wraparound.
    padded = np.pad(boundary, 2, constant_values=False)
    h, w = boundary.shape
    result = np.zeros_like(boundary)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            if dy * dy + dx * dx <= 4:
                result |= padded[2 + dy:2 + dy + h, 2 + dx:2 + dx + w]
    return result


def boundary_f1(mask, truth, *, tolerance_pixels=2):
    """Label-relative 4-neighbor contour diagnostic; tolerance is fixed at 2 px.

    Many-to-one nearest-boundary matching in each direction, using a Euclidean
    radius-two disk. No contour smoothing or mask morphology affects predictions.
    Supplied annotations have unverified boundary quality; not physical accuracy.
    """
    if type(tolerance_pixels) is not int or tolerance_pixels != 2:
        raise ValueError('This frozen diagnostic uses a two-pixel tolerance')
    predicted, reference = _pair(mask, truth)
    p, y = _boundary(predicted), _boundary(reference)
    pn, yn = int(p.sum()), int(y.sum())
    pm, ym = int((p & _within_two_pixels(y)).sum()), int((y & _within_two_pixels(p)).sum())
    precision, recall = _divide(pm, pn), _divide(ym, yn)
    score = None if not pn and not yn else (
        2 * precision * recall / (precision + recall) if precision and recall else 0.0)
    return dict(tolerance_pixels=2, basis='label-relative diagnostic; boundary quality unverified',
        predicted_boundary_pixels=pn, reference_boundary_pixels=yn,
        matched_predicted_pixels=pm, matched_reference_pixels=ym,
        precision=precision, recall=recall, f1=score)


def pixel_metrics(mask, truth):
    """Compute native-pixel counts/ratios and boundary diagnostic; never mutate."""
    predicted, reference = _pair(mask, truth)
    result = metrics_from_counts(int((predicted & reference).sum()), int((predicted & ~reference).sum()),
        int((~predicted & reference).sum()), int((~predicted & ~reference).sum()))
    result['boundary'] = boundary_f1(predicted, reference)
    result['boundary_f1'] = result['boundary']['f1']
    return result


def _identity(row):
    identifier, recording = row.get('id'), row.get('recording')
    if (isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or identifier == '' or
            not isinstance(recording, str) or not recording):
        raise ValueError('Every frame requires an ID and recording identity')
    return recording, identifier


def _flag(value, name):
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f'{name} must be explicit boolean, never inferred from status')
    return bool(value)


def _validated_row(row):
    _identity(row)
    reference_positive = _flag(row.get('reference_positive'), 'reference_positive')
    measured = row.get('pixel_metrics')
    if measured is None or row.get('status') in UNAVAILABLE_STATES:
        return None
    calculated = metrics_from_counts(*(measured[k] for k in ('tp', 'fp', 'fn', 'tn')))
    if not calculated['total_pixels']:
        raise ValueError('Evaluated frame must have nonempty pixel geometry')
    predicted_positive = _flag(row.get('predicted_positive'), 'predicted_positive')
    if reference_positive != bool(calculated['foreground_pixels']) or predicted_positive != bool(calculated['predicted_pixels']):
        raise ValueError('Frame-positive flags disagree with pixel counts')
    for key, actual in calculated.items():
        if key not in measured or key == 'percentages':
            continue
        supplied = measured[key]
        if actual is None and supplied is None:
            continue
        if (isinstance(supplied, bool) or not isinstance(supplied, (int, float, np.number)) or
                not math.isfinite(supplied) or actual is None or not math.isclose(float(actual), float(supplied), rel_tol=1e-10, abs_tol=1e-12)):
            raise ValueError(f'Supplied {key} disagrees with independent pixel counts')
    calculated['boundary_f1'] = measured.get('boundary_f1')
    if calculated['boundary_f1'] is not None and (
            isinstance(calculated['boundary_f1'], bool) or not isinstance(calculated['boundary_f1'], (int, float, np.number)) or
            not math.isfinite(calculated['boundary_f1']) or not 0 <= calculated['boundary_f1'] <= 1):
        raise ValueError('Boundary F1 must be a fraction or None')
    return calculated


def _reference_foreground(row, measured):
    """Validate optional foreground accounting, never a predicted/empty mask."""
    value = row.get('reference_foreground_pixels')
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
            raise ValueError('reference_foreground_pixels must be a nonnegative integer or None')
        value = int(value)
        if bool(value) != row['reference_positive']:
            raise ValueError('Reference foreground count disagrees with reference-positive flag')
        if measured is not None and value != measured['foreground_pixels']:
            raise ValueError('Reference foreground count disagrees with measured pixel counts')
    if measured is not None:
        return measured['foreground_pixels']
    # A supplied label-negative explicitly has zero reference foreground. This
    # makes no claim about physical gas, or the content of an absent prediction.
    return 0 if not row['reference_positive'] else value


def _operational(pooled, frame_counts, unavailable_positive, unavailable_negative,
                 unavailable_foreground, missing_foreground_frames):
    complete = not missing_foreground_frames
    added_fn = unavailable_foreground if complete else None
    fn = pooled['fn'] + added_fn if complete else None
    tp, fp = pooled['tp'], pooled['fp']
    ratios = dict(iou=_divide(tp, tp + fp + fn) if complete else None,
        dice=_divide(2 * tp, 2 * tp + fp + fn) if complete else None,
        recall=_divide(tp, tp + fn) if complete else None,
        omission=_divide(fn, tp + fn) if complete else None)
    pixel = dict(tp=tp, fp=fp, fn=fn, known_fn_pixels=pooled['fn'],
        added_abstention_fn_pixels=added_fn, known_tn_pixels=pooled['tn'], added_tn_credit=0,
        **ratios, percentages={key: None if value is None else 100 * value for key, value in ratios.items()})
    ftp, ffp, ffn = frame_counts['tp'], frame_counts['fp'], frame_counts['fn'] + unavailable_positive
    frames = dict(tp=ftp, fp=ffp, fn=ffn, known_fn_frames=frame_counts['fn'],
        added_abstention_fn_frames=unavailable_positive, known_tn_frames=frame_counts['tn'], added_tn_credit=0,
        positive_frame_recall=_divide(ftp, ftp + ffn), omission=_divide(ffn, ftp + ffn),
        f1=_divide(2 * ftp, 2 * ftp + ffp + ffn))
    return dict(pixel=pixel, frames=frames, abstention_positive_frames=unavailable_positive,
        abstention_negative_frames=unavailable_negative,
        abstention_reference_foreground_pixels=added_fn,
        reference_foreground_accounting_complete=complete,
        missing_reference_foreground_frames=missing_foreground_frames,
        basis='Operational omission penalty against supplied annotations, not chemical accuracy. '
              'Unavailable positive foreground adds FN only; no invented prediction mask or unknown-background TN credit.')


def _summarize(rows):
    valid = []
    frame_counts = dict(tp=0, fp=0, fn=0, tn=0)
    unavailable_positive = unavailable_negative = unavailable_foreground = missing_foreground_frames = 0
    inference_failures = fresh_mask_frames = fresh_mask_flag_known = 0
    for row in rows:
        measured = _validated_row(row)
        foreground = _reference_foreground(row, measured)
        failed = row.get('inference_failed', False)
        if not isinstance(failed, (bool, np.bool_)):
            raise ValueError('inference_failed must be an explicit boolean when supplied')
        inference_failures += int(failed or row.get('status') in INFERENCE_FAILURE_STATES)
        if row.get('new_mask_inference') is not None:
            fresh_mask_flag_known += 1
            fresh_mask_frames += int(_flag(row['new_mask_inference'], 'new_mask_inference'))
        if measured is None:
            unavailable_positive += int(row['reference_positive'])
            unavailable_negative += int(not row['reference_positive'])
            missing_foreground_frames += int(foreground is None)
            unavailable_foreground += foreground if foreground is not None else 0
            continue
        valid.append(measured)
        y, p = row['reference_positive'], row['predicted_positive']
        frame_counts[{(True, True): 'tp', (False, True): 'fp', (True, False): 'fn', (False, False): 'tn'}[y, p]] += 1
    pooled = metrics_from_counts(*(sum(r[k] for r in valid) for k in ('tp', 'fp', 'fn', 'tn')))
    tp, fp, fn, tn = (frame_counts[k] for k in ('tp', 'fp', 'fn', 'tn'))
    frames = dict(**frame_counts, positive_reference_frames=tp + fn, negative_reference_frames=fp + tn,
        predicted_positive_frames=tp + fp, positive_frame_recall=_divide(tp, tp + fn),
        precision=_divide(tp, tp + fp), recall=_divide(tp, tp + fn),
        f1=_divide(2 * tp, 2 * tp + fp + fn), false_positive_rate=_divide(fp, fp + tn),
        false_positive_frames=fp, missed_positive_frames=fn,
        unavailable_positive_frames=unavailable_positive, unavailable_negative_frames=unavailable_negative,
        known_prediction_frames=len(valid), total_reference_positive_frames=tp + fn + unavailable_positive,
        total_reference_negative_frames=fp + tn + unavailable_negative)
    coverage = _divide(len(valid), len(rows))
    reasons = ([] if rows else ['no_reference_frames']) + (
        [] if rows and len(valid) == len(rows) else ['operational_prediction_coverage_below_100_percent']) + (
        ['inference_failure_present'] if inference_failures else [])
    operational = _operational(pooled, frame_counts, unavailable_positive, unavailable_negative,
                               unavailable_foreground, missing_foreground_frames)
    return dict(total_frames=len(rows), evaluated_frames=len(valid), unavailable_frames=len(rows) - len(valid),
        prediction_coverage=coverage, operational_prediction_coverage=coverage,
        status_counts=dict(Counter(row.get('status', 'unspecified') for row in rows)),
        pooled=pooled, frames=frames,
        conditional_known=dict(pixel=pooled, frames=frames, evaluated_frames=len(valid),
            basis='Conditional on available spatial outputs, including explicitly scored empty detector outputs'),
        operational_omission_penalized=operational,
        inference_coverage=dict(operational_prediction_frames=len(valid), operational_prediction_fraction=coverage,
            new_mask_inference_frames=fresh_mask_frames if fresh_mask_flag_known else None,
            new_mask_inference_flag_known_frames=fresh_mask_flag_known,
            new_mask_inference_fraction=_divide(fresh_mask_frames, len(rows)) if fresh_mask_flag_known == len(rows) else None,
            inference_failure_frames=inference_failures),
        inference_failure_count=inference_failures,
        prompt_selection_eligible=not reasons,
        prompt_selection_eligibility=dict(eligible=not reasons, reasons=reasons,
            required_operational_prediction_coverage=1.0, required_inference_failure_count=0,
            basis='Coverage/failure gate only; calibration split and fixed prompt ranking remain runner responsibilities'),
        mean_frame_iou=_mean(r['iou'] for r in valid),
        mean_frame_boundary_f1=_mean(r['boundary_f1'] for r in valid),
        boundary_basis='Mean per-frame label-relative diagnostic; not a pooled contour score',
        false_alerts_per_camera_hour=None)


def summarize_frames(rows):
    """Summarize rows {id,recording,pixel_metrics,reference_positive,predicted_positive,status?}.

    Unknown/stale/failed rows remain in coverage and unavailable-label counts;
    their pixels are excluded from conditional-known accuracy denominators.
    Optional reference_foreground_pixels supplies their operational FN penalty.
    When positive abstention foreground counts are missing, operational pixel
    ratios remain None and abstention-positive frame counts are still reported.
    A deliberate empty output from a completed detector (no_candidate_detected
    or low_confidence_candidate) must supply its zero spatial output's measured
    counts and a False flag. Status alone never fabricates an empty mask.
    Optional new_mask_inference preserves fresh-mask coverage independently of
    operational prediction coverage. Optional inference_failed disqualifies a
    prompt candidate even if partial measurements were retained.
    Macro ratios give equal recording weight over defined recording ratios.
    """
    rows = list(rows)
    seen, groups = set(), defaultdict(list)
    for row in rows:
        identity = _identity(row)
        if identity in seen:
            raise ValueError('Duplicate recording/frame identity')
        seen.add(identity)
        groups[row['recording']].append(row)
    result = _summarize(rows)
    recordings = {key: _summarize(group) for key, group in sorted(groups.items())}
    result['per_recording'] = recordings
    result['macro'] = dict(
        pixel={key: _mean(r['pooled'][key] for r in recordings.values()) for key in RATIOS},
        frame={key: _mean(r['frames'][key] for r in recordings.values()) for key in
               ('positive_frame_recall', 'precision', 'recall', 'f1', 'false_positive_rate')},
        contributing_recordings={key: sum(r['pooled'][key] is not None for r in recordings.values()) for key in RATIOS})
    operational_keys = ('iou', 'dice', 'recall', 'omission')
    result['macro']['operational_omission_penalized'] = dict(
        pixel={key: _mean(r['operational_omission_penalized']['pixel'][key] for r in recordings.values())
               if all(r['operational_omission_penalized']['reference_foreground_accounting_complete']
                      for r in recordings.values()) else None for key in operational_keys},
        positive_frame_recall=_mean(r['operational_omission_penalized']['frames']['positive_frame_recall']
                                   for r in recordings.values()))
    result['units'] = 'fractions in [0,1]; percentages field explicitly multiplies by 100'
    result['evaluation_basis'] = ('pooled/frames/macro.pixel are conditional-known aliases. '
        'Consult operational_omission_penalized, coverage and abstentions before comparing candidates. '
        'Label-negative is not verified physical absence.')
    return result


def _temporal_recording(rows, success_iou):
    rows = sorted(rows, key=lambda r: r['source_frame_index'])
    indices = [r['source_frame_index'] for r in rows]
    if not all(type(index) is int and index >= 0 for index in indices) or indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError('Temporal evaluation requires complete contiguous source indices without duplicates')
    frames, predicted, reference = [], [], []
    shape = None
    for row in rows:
        truth = _binary(row['reference_mask'])
        if shape is not None and truth.shape != shape:
            raise ValueError('Temporal source geometry changed')
        shape = truth.shape
        raw = row.get('predicted_mask')
        mask = None if raw is None or row.get('status') in UNAVAILABLE_STATES else _pair(raw, truth)[0]
        predicted.append(mask)
        reference.append(truth)
        measured = None if mask is None else pixel_metrics(mask, truth)
        positive = bool(truth.any())
        frames.append(dict(id=row['id'], source_frame_index=row['source_frame_index'],
            status=row.get('status', 'unspecified'),
            new_mask_inference=row.get('new_mask_inference'), inference_failed=row.get('inference_failed', False),
            pixel_metrics=measured, reference_foreground_pixels=int(truth.sum()),
            reference_positive=positive, predicted_positive=None if mask is None else bool(mask.any()),
            iou=None if measured is None else measured['iou'],
            omission=None if measured is None else measured['omission'],
            tracking_success=None if not positive or measured is None else measured['iou'] >= success_iou,
            operational_omission_penalized=dict(
                iou=(0.0 if positive else None) if measured is None else measured['iou'],
                omission=(1.0 if positive else None) if measured is None else measured['omission'],
                tracking_success=None if not positive else (False if measured is None else measured['iou'] >= success_iou)),
            adjacent_predicted_iou=None, adjacent_reference_iou=None,
            predicted_change=None, reference_change=None, jitter_excess=None))
    onset_events, disappearance_events = [], []
    for i, frame in enumerate(frames):
        if i:
            previous = frames[i - 1]
            truth_overlap = metrics_from_masks_iou(reference[i - 1], reference[i])
            frame['adjacent_reference_iou'] = truth_overlap
            gt_change = 0.0 if not reference[i - 1].any() and not reference[i].any() else 1 - truth_overlap
            frame['reference_change'] = gt_change
            if predicted[i - 1] is not None and predicted[i] is not None:
                overlap = metrics_from_masks_iou(predicted[i - 1], predicted[i])
                frame['adjacent_predicted_iou'] = overlap
                change = 0.0 if not predicted[i - 1].any() and not predicted[i].any() else 1 - overlap
                frame['predicted_change'] = change
                frame['jitter_excess'] = max(0.0, change - gt_change)
            if not previous['reference_positive'] and frame['reference_positive']:
                end = i
                while end < len(frames) and frames[end]['reference_positive']:
                    end += 1
                # Overlap is required; an unrelated cloud cannot count as onset detection.
                detected = next((j for j in range(i, end) if predicted[j] is not None and
                                 bool((predicted[j] & reference[j]).any())), None)
                onset_events.append(dict(onset_source_frame_index=indices[i],
                    detected_source_frame_index=None if detected is None else indices[detected],
                    delay_frames=None if detected is None else indices[detected] - indices[i],
                    detected=detected is not None, reference_episode_frames=end - i,
                    unavailable_frames_before_detection=sum(predicted[j] is None for j in range(i, detected if detected is not None else end)),
                    right_censored=end == len(frames), definition='First predicted pixel overlap with reference during the positive episode'))
            if previous['reference_positive'] and not frame['reference_positive']:
                end = i
                while end < len(frames) and not frames[end]['reference_positive'] and predicted[end] is not None and predicted[end].any():
                    end += 1
                censored = end == len(frames) or (end < len(frames) and
                    (frames[end]['reference_positive'] or predicted[end] is None))
                disappearance_events.append(dict(first_empty_reference_index=indices[i],
                    persistence_frames=end - i, right_censored=censored,
                    cleared_source_frame_index=None if censored else indices[end],
                    definition='Consecutive predicted-nonempty frames after a nonempty-to-empty reference transition; label-relative only'))
    positive_known = [f for f in frames if f['tracking_success'] is not None]
    summary = _summarize([dict(f, recording=rows[0]['recording']) for f in frames])
    operational = dict(summary['operational_omission_penalized'],
        tracking_success_fraction=_mean(f['operational_omission_penalized']['tracking_success'] for f in frames),
        mean_positive_omission=_mean(f['operational_omission_penalized']['omission'] for f in frames if f['reference_positive']))
    return dict(frames=frames, source_frames=len(rows),
        unavailable_frames=sum(p is None for p in predicted),
        success_iou_threshold=success_iou, positive_known_frames=len(positive_known),
        positive_unavailable_frames=sum(f['reference_positive'] and p is None for f, p in zip(frames, predicted)),
        tracking_success_fraction=_divide(sum(f['tracking_success'] for f in positive_known), len(positive_known)),
        conditional_known_tracking_success_fraction=_divide(sum(f['tracking_success'] for f in positive_known), len(positive_known)),
        mean_positive_omission=_mean(f['omission'] for f in frames if f['reference_positive']),
        conditional_known=summary['conditional_known'], operational_omission_penalized=operational,
        operational_prediction_coverage=summary['operational_prediction_coverage'],
        inference_coverage=summary['inference_coverage'], inference_failure_count=summary['inference_failure_count'],
        onset_events=onset_events, onset_left_censored=frames[0]['reference_positive'],
        mean_detected_onset_delay_frames=_mean(e['delay_frames'] for e in onset_events),
        detected_onsets=sum(e['detected'] for e in onset_events), missed_onsets=sum(not e['detected'] for e in onset_events),
        disappearance_events=disappearance_events,
        mean_uncensored_persistence_frames=_mean(e['persistence_frames'] for e in disappearance_events if not e['right_censored']),
        mean_adjacent_predicted_iou=_mean(f['adjacent_predicted_iou'] for f in frames),
        mean_jitter_excess=_mean(f['jitter_excess'] for f in frames),
        true_tracking_drift=None, onset_delay_seconds=None, false_alerts_per_camera_hour=None)


def metrics_from_masks_iou(mask, truth):
    """Adjacent-frame overlap helper without boundary or mask modification."""
    predicted, reference = _pair(mask, truth)
    return _divide(int((predicted & reference).sum()), int((predicted | reference).sum()))


def temporal_quality(rows, *, success_iou=0.5):
    """Dense annotation-relative temporal diagnostics; source-index clock only.

    Rows: id, recording, source_frame_index, reference_mask, predicted_mask
    (None for unavailable), optional status. No future data enters inference;
    this post-hoc evaluator scans observed sequences to measure event delays.
    Operational omission separately penalizes absent outputs against known
    reference foreground; no unknown input is converted to a prediction mask.
    It never invents source FPS, elapsed seconds, identity drift or field alerts.
    """
    if isinstance(success_iou, bool) or not isinstance(success_iou, (int, float)) or not math.isfinite(success_iou) or not 0 < success_iou <= 1:
        raise ValueError('Success IoU must be a finite fraction in (0,1]')
    rows = list(rows)
    groups, seen = defaultdict(list), set()
    for row in rows:
        identity = _identity(row)
        if identity in seen:
            raise ValueError('Duplicate recording/frame identity')
        seen.add(identity)
        groups[row['recording']].append(row)
    recordings = {name: _temporal_recording(group, success_iou) for name, group in sorted(groups.items())}
    frames = [frame for record in recordings.values() for frame in record['frames']]
    onsets = [event for record in recordings.values() for event in record['onset_events']]
    summary = _summarize([dict(frame, recording=name) for name, record in recordings.items() for frame in record['frames']])
    operational = dict(summary['operational_omission_penalized'],
        tracking_success_fraction=_mean(f['operational_omission_penalized']['tracking_success'] for f in frames),
        mean_positive_omission=_mean(f['operational_omission_penalized']['omission'] for f in frames if f['reference_positive']))
    return dict(per_recording=recordings, source_frames=len(frames),
        unavailable_frames=sum(r['unavailable_frames'] for r in recordings.values()),
        tracking_success_fraction=_mean(f['tracking_success'] for f in frames),
        conditional_known_tracking_success_fraction=_mean(f['tracking_success'] for f in frames),
        mean_positive_omission=_mean(f['omission'] for f in frames if f['reference_positive']),
        conditional_known=summary['conditional_known'], operational_omission_penalized=operational,
        operational_prediction_coverage=summary['operational_prediction_coverage'],
        inference_coverage=summary['inference_coverage'], inference_failure_count=summary['inference_failure_count'],
        mean_detected_onset_delay_frames=_mean(e['delay_frames'] for e in onsets),
        detected_onsets=sum(e['detected'] for e in onsets), missed_onsets=sum(not e['detected'] for e in onsets),
        mean_adjacent_predicted_iou=_mean(f['adjacent_predicted_iou'] for f in frames),
        mean_jitter_excess=_mean(f['jitter_excess'] for f in frames),
        clock='source_frame_index; actual FPS/PTS unavailable, presentation clock is not acquisition time',
        onset_delay_unit='frames', onset_delay_seconds=None, false_alerts_per_camera_hour=None,
        true_tracking_drift=None,
        diagnostic_scope='Label-relative omission, overlap and change excess; not independently annotated object-identity drift')
