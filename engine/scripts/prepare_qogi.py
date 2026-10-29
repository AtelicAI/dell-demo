"""Per-frame plume inputs for camera quantification (QOGI) from recorded GasVid footage and executed detector masks.

For every frame in the calibration and incident spans: plume pixels (nearest detector mask within 2.5 s), summed
apparent contrast against the plume-free background (median of class-0 frames) and plume speed from phase correlation
of consecutive contrast images inside the mask. The runtime (app.qogi) turns these into a rate; nothing here reads a
metered rate except to name the calibration segment.

GasVid MP4s are 8-bit grayscale, not radiometric: grey levels stand in for apparent temperature contrast.

usage: python -I scripts/prepare_qogi.py <MOV_1239.mp4> <full-recording detections.jsonl>
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
CLIP = 'MOV_1239'
DISTANCE_M = 12.6     # logged imaging distance (data/footage/gasvid_facts.json)
FOV_DEGREES = 24.     # GF-320 horizontal field of view
BACKGROUND_SPAN = (20, 180)       # class 0: release below camera detection
CALIBRATION_SPAN = (200, 365)     # class 1, 277.7 g/h, from 15 s after the step
INCIDENT_SPAN = (520, 725)        # covers the CAM-02 monitor clip (525-680 s)
MASK_MAX_GAP_S = 2.5
MIN_MASK_PIXELS = 50


def gray(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)


def main(video, detections):
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)

    def frame_at(t):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        return gray(f)

    background = np.median(np.stack([frame_at(t) for t in np.arange(*BACKGROUND_SPAN, 4)]), axis=0)
    rows = sorted((json.loads(line) for line in open(detections)), key=lambda r: r['pts_seconds'])
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

    pixel_m = 2 * DISTANCE_M * math.tan(math.radians(FOV_DEGREES / 2)) / w
    frames = []
    for start, end in (CALIBRATION_SPAN, INCIDENT_SPAN):
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
            if j is not None and abs(times[j] - t) <= MASK_MAX_GAP_S and mask(j).sum() >= MIN_MASK_PIXELS:
                m = mask(j)
                ys, xs = np.nonzero(m)
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                window = cv2.createHanningWindow((x1 - x0, y1 - y0), cv2.CV_32F)
                (dx, dy), _ = cv2.phaseCorrelate((np.abs(previous) * m)[y0:y1, x0:x1], (np.abs(current) * m)[y0:y1, x0:x1], window)
                frames.append([round(t, 3), int(m.sum()), round(float(np.abs(current)[m].sum()), 1),
                               round(math.hypot(dx, dy) * fps * pixel_m, 4)])
            previous = current
    out = ROOT / f'data/quantification/qogi/{CLIP}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(
        clip_id=CLIP, method='qogi-integrated-mass-enhancement-inputs-v1',
        source_sha256=hashlib.sha256(Path(video).read_bytes()).hexdigest(),
        detections_sha256=hashlib.sha256(Path(detections).read_bytes()).hexdigest(),
        fps=fps, distance_m=DISTANCE_M, fov_degrees=FOV_DEGREES, pixel_m=pixel_m,
        background_span_s=BACKGROUND_SPAN, calibration=dict(span_s=CALIBRATION_SPAN, gasvid_class=1, metered_rate_g_h=277.7),
        incident_span_s=INCIDENT_SPAN, columns=['clip_seconds', 'plume_pixels', 'contrast_sum', 'plume_speed_m_s'],
        frames=frames,
        note='Grey-level contrast stands in for radiometric apparent temperature: GasVid MP4s are not radiometric')) + '\n')
    print(f'{len(frames)} frames -> {out}')


if __name__ == '__main__':
    main(*sys.argv[1:3])
