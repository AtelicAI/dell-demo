"""Recorded replay metadata helpers; deliberately independent of HTTP routing."""
import copy
import json
import math
import os
from pathlib import Path

DATA = Path(os.environ.get('ATELIC_DATA_DIR', Path(__file__).resolve().parents[2] / 'data'))


def _load(name, data_dir=None):
    return json.loads((Path(data_dir or DATA) / name).read_text())


def get_video_manifest(data_dir=None):
    """Return metadata, including unverified rights and synthetic mappings."""
    return _load('video_manifest.json', data_dir)


def lookup_clip(clip_id, data_dir=None):
    matches = [c for c in get_video_manifest(data_dir)['clips'] if c['id'] == clip_id]
    if len(matches) != 1:
        raise KeyError(clip_id)
    return copy.deepcopy(matches[0])


def resolve_video_path(clip_id, data_dir=None):
    """Accept an ID only; resolve an explicitly allowlisted local MP4.

    Rights policy remains the caller's responsibility; local presence grants no
    redistribution permission. Never pass request paths to a file response.
    """
    data = Path(data_dir or DATA).resolve()
    clip = lookup_clip(clip_id, data)
    relative = Path(clip['file'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe manifest path')
    allowed = (data / 'downloaded/GasVid 2/Videos').resolve()
    if not allowed.is_relative_to(data):
        raise ValueError('Video directory escapes data root')
    path = (data / relative).resolve()
    if not path.is_relative_to(allowed) or path.suffix.lower() != '.mp4':
        raise ValueError('Video path outside allowlist')
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Clock values must be finite numbers')
    return value


def scenario_to_clip_time(clip_id, scenario_id, scenario_seconds, data_dir=None):
    """Affine approximate mapping, no looping/clamping or real-world sync claim."""
    clip = lookup_clip(clip_id, data_dir)
    mappings = [m for m in clip['scenario_mappings'] if m['scenario_id'] == scenario_id]
    if len(mappings) != 1:
        raise KeyError(scenario_id)
    m = mappings[0]
    if m.get('kind') != 'synthetic_approximate':
        raise ValueError('Unsupported mapping provenance')
    t = _number(scenario_seconds)
    start = _number(m['scenario_start_seconds'])
    end = _number(m['scenario_end_seconds'])
    speed = _number(m['clip_seconds_per_scenario_second'])
    offset = _number(m['clip_start_seconds'])
    duration = _number(clip['duration_seconds'])
    if speed <= 0 or start < 0 or end < start or offset < 0 or offset + (end-start)*speed > duration:
        raise ValueError('Invalid mapping bounds')
    if not start <= t <= end:
        raise ValueError('Scenario clock outside mapped interval')
    return {'clip_seconds': offset + (t-start)*speed, 'mapping_kind': m['kind'], 'source_time_synchronized': False}


def get_annotations(clip_id, data_dir=None, reviewed_only=False):
    """Pending records remain explicit; human review requires named evidence."""
    lookup_clip(clip_id, data_dir)
    records = _load('replay_annotations.json', data_dir)['annotations']
    result = []
    for record in records:
        if record['clip_id'] != clip_id:
            continue
        status = record.get('review_status')
        if status not in {'pending', 'human_reviewed'}:
            raise ValueError('Unknown review status')
        if status == 'human_reviewed' and not all(record.get(k) for k in ('reviewer', 'reviewed_at', 'evidence')):
            raise ValueError('Human review requires reviewer, timestamp, and evidence')
        if not reviewed_only or status == 'human_reviewed':
            result.append(copy.deepcopy(record))
    return result


def get_runtime_replays(data_dir=None):
    """Separate opt-in runtime additions; never mutate canonical scenarios.

    Scene observations are not gas detections. A parent assigning demonstration
    UTC or camera/geometry must retain source_independent/provenance markers.
    """
    return _load('replay_annotations.json', data_dir).get('runtime_replays', [])
