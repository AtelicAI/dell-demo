"""Build monitoring runs for the two demo events from completed full-recording jobs (no new inference).

Each run keeps the executed OWLv2 detector samples inside the monitored window, unchanged, in the format
the monitor reads (detection/runs/<run_id>/results.json). The alarm is then computed causally by the engine
(3 of the last 5 samples positive). Usage:

  python scripts/prepare_monitor_runs.py --experiments <workspace>/data/detection/experiments \
      --backup-experiments <workspace>/data/backups/gpu-full-video-20261023/data/detection/experiments
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    # run_id: (clip_id, source full job, window start, window end)
    'monitor-mov1237-175-300-001': ('MOV_1237', 'full-MOV_1237-20261023', 175.0, 300.0),
    'monitor-mov1239-525-680-001': ('MOV_1239', 'full-mov_1239-cuda-20261028-v1', 525.0, 680.0),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiments', type=Path, required=True)
    parser.add_argument('--backup-experiments', type=Path)
    args = parser.parse_args()
    clips = {c['id']: c for c in json.loads((ROOT / 'data/video_manifest.json').read_text())['clips']}
    for run_id, (clip_id, job, start, end) in RUNS.items():
        src = next(d / job for d in (args.experiments, args.backup_experiments) if d and (d / job / 'results.json').is_file())
        full = json.loads((src / 'results.json').read_text())
        if full.get('status') != 'completed':
            raise SystemExit(f'{job} is not a completed run')
        rows = [json.loads(line) for line in (src / 'detections.jsonl').open()]
        predictions = sorted((r for r in rows if start <= r['pts_seconds'] <= end), key=lambda r: r['pts_seconds'])
        for p in predictions:
            p.setdefault('mask_file', None)
            p.setdefault('overlay_file', None)
        out = ROOT / 'data/detection/runs' / run_id
        out.mkdir(parents=True, exist_ok=True)
        (out / 'results.json').write_text(json.dumps(dict(
            schema_version='1.0', run_id=run_id, status='completed', clip_id=clip_id,
            source_sha256=clips[clip_id]['sha256'], start_seconds=start, end_seconds=end,
            sample_interval_seconds='irregular (detector cadence of the full job, median about 2 s)',
            model={**full['model'], 'method': 'langgas-inspired-owlv2-sam2-v1', 'detector': full.get('detector')},
            derived_from=dict(run_id=job, results_sha256=sha(src / 'results.json'), detections_sha256=sha(src / 'detections.jsonl'),
                              note='Detector samples copied unchanged from the completed full-recording job; no new inference'),
            predictions=predictions), indent=1) + '\n')
        positives = sum(p['prediction'] for p in predictions)
        print(f'{run_id}: {len(predictions)} samples ({positives} positive) from {job}')


if __name__ == '__main__':
    main()
