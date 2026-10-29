"""Bounded offline video experiment contracts; no model or dataset imports."""
import math
import re


def validate_config(config, registry):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', config['batch_id']):
        raise ValueError('Safe batch ID required')
    if not 1 <= len(config['clips']) <= 8:
        raise ValueError('One to eight short clips required')
    names = set()
    for clip in config['clips']:
        name = clip['run_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name) or name in names:
            raise ValueError('Unique safe run IDs required')
        names.add(name)
        values = [clip[k] for k in ('start_seconds', 'end_seconds', 'reference_start', 'reference_end')]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
            raise ValueError('Finite source times required')
        if clip['clip_id'] not in registry:
            raise ValueError('Unknown source recording')
        start, end, ref_start, ref_end = values
        source = registry[clip['clip_id']]
        if not (0 <= ref_start < ref_end <= start < end <= source['duration_seconds'] and
                end-start <= 7 and 4 <= ref_end-ref_start <= 20):
            raise ValueError('Bounded target and preceding reference required')
    return config
