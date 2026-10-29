"""Per-frame plume inputs for camera quantification (QOGI) from recorded GasVid footage and executed detector masks.

For every frame in the calibration and incident spans: plume pixels (nearest detector mask within 2.5 s), summed
apparent contrast against the plume-free background (median of class-0 frames) and plume speed from phase correlation
of consecutive contrast images inside the mask. The runtime (app.qogi) turns these into a rate; nothing here reads a
metered rate except to name the calibration segment.

GasVid MP4s are 8-bit grayscale, not radiometric: grey levels stand in for apparent temperature contrast.

usage: python -I scripts/prepare_qogi.py <clip id> <clip .mp4> <full-recording detections.jsonl | monitor run results.json>
"""
import bisect
import hashlib
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FOV_DEGREES = 24.     # GF-320 horizontal field of view
# Per clip: logged imaging distance (data/footage/gasvid_facts.json), plume-free background span (class 0), the
# reference-release span used to calibrate the camera model (class 1, 277.7 g/h, from 15 s after the step) and the
# span the incident needs. MOV_1237 is calibrated on its own first 40 s of the 277.7 g/h release (CAM-01 commissioning
# release in the story; the same release the incident later measures). MOV_1237 masks come from the CAM-01 monitor run (about one every 6 s), hence the wider gap.
CLIPS = {
    'MOV_1239': dict(distance_m=12.6, background_s=(20, 180), calibration_s=(200, 365), spans_s=[(200, 365), (520, 725)], mask_gap_s=2.5),
    'MOV_1237': dict(distance_m=6.9, background_s=(30, 190), calibration_s=(214, 254), spans_s=[(199, 300)], mask_gap_s=3.5),
}
MIN_MASK_PIXELS = 50


def gray(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)


def main(clip, video, detections):
    cfg = CLIPS[clip]
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)

    def frame_at(t):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        return gray(f)

    background = np.median(np.stack([frame_at(t) for t in np.arange(*cfg['background_s'], 4)]), axis=0)
    rows = json.load(open(detections))['predictions'] if detections.endswith('.json') else [json.loads(line) for line in open(detections)]
    rows = sorted(rows, key=lambda r: r['pts_seconds'])
    rows = [r for r in rows if r['prediction'] == 1 and r['plume_extent_polygons']]
    times = [r['pts_seconds'] for r in rows]
    h, w = background.shape
    masks = {}

    def mask(i):
        if i not in masks:
            m = np.zeros((h, w), np.uint8)
            for poly in rows[i]['plume_extent_polygons']:
                cv2.fillPoly(m, [np.round(np.array(poly) * [w, h]).astype(np.int32)], 1)
            masks[i] = m.astype(bool)
        return masks[i]

    pixel_m = 2 * cfg['distance_m'] * math.tan(math.radians(FOV_DEGREES / 2)) / w
    frames = []
    for start, end in cfg['spans_s']:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        ok, f = cap.read()
        previous, t = gray(f) - background, start
        while t < end:
            ok, f = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            current = gray(f) - background
            k = bisect.bisect_left(times, t)
            j = min((j for j in (k - 1, k) if 0 <= j < len(times)), key=lambda j: abs(times[j] - t), default=None)
            if j is not None and abs(times[j] - t) <= cfg['mask_gap_s'] and mask(j).sum() >= MIN_MASK_PIXELS:
                m = mask(j)
                ys, xs = np.nonzero(m)
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                window = cv2.createHanningWindow((x1 - x0, y1 - y0), cv2.CV_32F)
                (dx, dy), _ = cv2.phaseCorrelate((np.abs(previous) * m)[y0:y1, x0:x1], (np.abs(current) * m)[y0:y1, x0:x1], window)
                frames.append([round(t, 3), int(m.sum()), round(float(np.abs(current)[m].sum()), 1),
                               round(math.hypot(dx, dy) * fps * pixel_m, 4)])
            previous = current
    out = ROOT / f'data/quantification/qogi/{clip}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(
        clip_id=clip, method='qogi-integrated-mass-enhancement-inputs-v1',
        source_sha256=hashlib.sha256(Path(video).read_bytes()).hexdigest(),
        detections_sha256=hashlib.sha256(Path(detections).read_bytes()).hexdigest(),
        fps=fps, distance_m=cfg['distance_m'], fov_degrees=FOV_DEGREES, pixel_m=pixel_m, background_span_s=cfg['background_s'],
        calibration=dict(span_s=cfg['calibration_s'], gasvid_class=1, metered_rate_g_h=277.7) if cfg['calibration_s'] else None,
        incident_span_s=cfg['spans_s'][-1], columns=['clip_seconds', 'plume_pixels', 'contrast_sum', 'plume_speed_m_s'],
        frames=frames,
        note='Grey-level contrast stands in for radiometric apparent temperature: GasVid MP4s are not radiometric')) + '\n')
    print(f'{len(frames)} frames -> {out}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
