"""Camera monitoring replay: causal alerts from executed model predictions on recorded footage.

The alert time comes only from saved model predictions and a fixed persistence policy.
The origin region used for candidate equipment is the illustrative synthetic mapping,
never a drifting plume extent from the recorded scene.
"""
from .routes import OPERATOR  # human/operator credential on state-changing routes
import math
import re
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query
from .contracts import DetectionEvent
from .data import load_json
from .orchestrator import OrchestrationRequest

router = APIRouter()
_issued = set()


DEFAULT_PROFILE = 'leak'


def profiles():
    """Named monitoring feeds: the default leak feed plus overrides (below-detection control, wind outage)."""
    base = load_json('scenarios/monitor.json')
    return {base['profile']: base, **{p['profile']: {**base, **p} for p in load_json('scenarios/monitor_profiles.json')}}


def config(profile=None):
    try:
        return profiles()[profile or DEFAULT_PROFILE]
    except KeyError as exc:
        raise HTTPException(404, 'Unknown monitoring profile') from exc


def profile_for(incident_id):
    return next((cfg for cfg in profiles().values()
                 if re.fullmatch(re.escape(cfg['incident_prefix']) + r'\d{3}', incident_id)), None)


def predictions(cfg=None):
    cfg = cfg or config()
    from .detection_api import read_result
    from .video import lookup_clip
    run = read_result(cfg['run_id'])
    if run.get('status') != 'completed' or run.get('clip_id') != cfg['clip_id'] or run.get('source_sha256') != lookup_clip(cfg['clip_id'])['sha256']:
        raise HTTPException(503, 'Monitoring inference run is unavailable or does not match the registered recording')
    return run, sorted(run['predictions'], key=lambda p: p['pts_seconds'])


def to_scenario_seconds(clip_seconds, cfg=None):
    cfg = cfg or config()
    return cfg['scenario_seconds_at_clip_start'] + (clip_seconds - cfg['clip_start_seconds'])


def to_clip_seconds(scenario_seconds, cfg=None):
    cfg = cfg or config()
    clip = cfg['clip_start_seconds'] + (scenario_seconds - cfg['scenario_seconds_at_clip_start'])
    return clip if cfg['clip_start_seconds'] <= clip <= cfg['clip_end_seconds'] else None


def first_alert(samples, policy):
    """First sample at which the trailing window satisfies the persistence policy."""
    for i in range(len(samples)):
        window = samples[max(0, i-policy['window_samples']+1):i+1]
        if samples[i]['prediction'] and sum(s['prediction'] for s in window) >= policy['min_positive_samples']:
            return i
    return None


def sample_view(run_id, p):
    base = f"/detection/runs/{run_id}/assets"
    return dict(pts_seconds=p['pts_seconds'], prediction=p['prediction'], score=p['score'], score_kind=p['score_kind'],
                normalized_boxes=p['normalized_boxes'], plume_extent_polygons=p['plume_extent_polygons'],
                mask_url=f"{base}/{p['mask_file']}" if p.get('mask_file') else None, overlay_url=f"{base}/{p['overlay_file']}" if p.get('overlay_file') else None)


def footage(cfg):
    """Published facts about the recorded clip behind a feed, with each segment's metered release."""
    facts = load_json('footage/gasvid_facts.json')
    clip = facts['clips'].get(cfg['clip_id'])
    if clip is None:
        return None
    rates = {c['class']: c for c in facts['leak_classes']}
    segments = [{**s, 'metered_rate_g_h': rates[s['class']]['rate_g_h'], 'ci95_g_h': rates[s['class']]['ci95_g_h']} for s in clip['segments']]
    return dict(clip_id=cfg['clip_id'], dataset=facts['dataset'], site=facts['site'], campaign=facts['campaign'], camera=facts['camera'],
                release_hardware=facts['release_hardware'], protocol=facts['protocol'],
                **{k: clip[k] for k in ('recorded_date', 'shooting_time_as_logged', 'imaging_distance_m', 'release_location',
                                        'release_location_basis', 'segment_timing_basis')},
                segments=segments, leak_classes_source=facts['leak_classes_source'], publication=facts['publication'],
                plant_alignment=facts['plant_alignment'], agent_access=facts['agent_access'])


def segment_at(clip_seconds, facts):
    return next((s for s in (facts or {}).get('segments', []) if s['start_seconds'] <= clip_seconds < s['end_seconds']), None)


def is_monitor_incident(incident_id):
    return profile_for(incident_id) is not None


def incident_event(incident_id, scenario):
    """Model-triggered detection event for a monitoring incident, or None if never alerted."""
    cfg = profile_for(incident_id)
    run, samples = predictions(cfg)
    index = first_alert(samples, cfg['alert_policy'])
    if index is None:
        return None
    alert = samples[index]
    fixture = next(e for e in load_json('scenarios/detections.json') if 'scenarios/detections.json#' + e['event_id'] == cfg['origin_region_reference'])
    seconds = math.ceil(to_scenario_seconds(alert['pts_seconds'], cfg))
    return DetectionEvent(event_id=f'{incident_id}-DET', incident_id=incident_id, scenario_id=scenario['id'],
                          camera_id=cfg['camera_id'], clip_time_seconds=alert['pts_seconds'], mode='model_inference',
                          timestamp_utc=datetime.fromisoformat(scenario['start_utc'])+timedelta(seconds=seconds),
                          origin_polygon=fixture['origin_polygon'], confidence=None,
                          provenance=dict(source_kind='inferred', source_dataset=f"gasvid-{cfg['clip_id']}",
                                          generator_version=run['model'].get('method', 'pretrained'),
                                          reference=f"detection/runs/{cfg['run_id']}#{cfg['clip_id']}@{alert['pts_seconds']:.3f}"))


@router.get('/monitor/profiles')
def monitor_profiles():
    return dict(default_profile=DEFAULT_PROFILE, profiles=[dict(profile=c['profile'], title=c['title'], scenario_id=c['scenario_id'],
                                                                 clip_id=c['clip_id'], clip_start_seconds=c['clip_start_seconds'],
                                                                 clip_end_seconds=c['clip_end_seconds'], incident_prefix=c['incident_prefix'])
                                                            for c in profiles().values()])


@router.get('/monitor')
def monitor(profile: str = DEFAULT_PROFILE):
    cfg = config(profile)
    run, samples = predictions(cfg)
    return {**cfg, 'playback_url': f"/videos/{cfg['clip_id']}/playback", 'sample_count': len(samples),
            'model': run['model'], 'execution': 'replay of previously executed model inference on recorded footage; not a live camera',
            'labels': {'video': 'Recorded METEC footage, illustrative feed', 'facility': 'Synthetic facility and telemetry'}}


@router.get('/monitor/footage')
def monitor_footage(profile: str = DEFAULT_PROFILE):
    """What the camera actually recorded: site, date, distance and the metered release in each segment."""
    cfg = config(profile)
    facts = footage(cfg)
    if facts is None:
        raise HTTPException(404, 'No published facts for this clip')
    shown = [s for s in facts['segments'] if s['start_seconds'] < cfg['clip_end_seconds'] and s['end_seconds'] > cfg['clip_start_seconds']]
    return {**facts, 'profile': cfg['profile'], 'monitored_window': [cfg['clip_start_seconds'], cfg['clip_end_seconds']],
            'segments_in_window': [s['class'] for s in shown]}


@router.get('/monitor/status')
def monitor_status(clip_seconds: float = Query(ge=0, allow_inf_nan=False), profile: str = DEFAULT_PROFILE):
    """Causal state at a source-video time: only predictions at or before clip_seconds are used."""
    cfg = config(profile)
    run, samples = predictions(cfg)
    if not cfg['clip_start_seconds'] <= clip_seconds <= cfg['clip_end_seconds']:
        raise HTTPException(422, 'Clip time is outside the monitored interval')
    seen = [p for p in samples if p['pts_seconds'] <= clip_seconds + 1e-6]
    policy = cfg['alert_policy']
    latest = seen[-1] if seen else None
    fresh = latest is not None and clip_seconds - latest['pts_seconds'] <= policy['max_sample_age_seconds']
    window = seen[-policy['window_samples']:]
    index = first_alert(seen, policy)
    if index is not None:
        state = 'alert'
    elif any(p['prediction'] for p in window):
        state = 'possible_plume'
    else:
        state = 'monitoring_no_alert' if fresh else 'unknown_no_recent_sample'
    alert = None
    if index is not None:
        hit = seen[index]
        alert = dict(clip_seconds=hit['pts_seconds'], scenario_seconds=math.ceil(to_scenario_seconds(hit['pts_seconds'], cfg)),
                     camera_id=cfg['camera_id'], policy=policy,
                     evidence_reference=f"detection/runs/{cfg['run_id']}#{cfg['clip_id']}@{hit['pts_seconds']:.3f}",
                     next_step='POST /monitor/incidents to register the incident; the orchestrator then runs the investigation')
    return dict(profile=cfg['profile'], scenario_id=cfg['scenario_id'], state=state, clip_seconds=clip_seconds, scenario_seconds=to_scenario_seconds(clip_seconds, cfg),
                latest_sample=sample_view(cfg['run_id'], latest) if latest and fresh else None,
                recent_positive_samples=sum(p['prediction'] for p in window), window_samples=len(window), alert=alert,
                metered_release=segment_at(clip_seconds, footage(cfg)),
                source='executed_model_replay', warning='No alert is not proof of no gas; recorded scene is not the synthetic facility')


class MonitorIncidentRequest(OrchestrationRequest):
    orchestrate: bool = True
    profile: str = DEFAULT_PROFILE


@router.post('/monitor/incidents', dependencies=OPERATOR)
def register_incident(body: MonitorIncidentRequest | None = None):
    """Allocate the next monitoring incident ID and, by default, start the orchestrator."""
    body = body or MonitorIncidentRequest()
    cfg = config(body.profile)
    run, samples = predictions(cfg)
    if first_alert(samples, cfg['alert_policy']) is None:
        raise HTTPException(409, 'Monitoring run contains no alert; no incident to register')
    used = {i for i in _issued if i.startswith(cfg['incident_prefix'])}
    import os
    if os.getenv('WORKFLOW_DATABASE_URL'):
        from .routes import store
        with store().connect() as conn:
            used |= {r['id'] for r in conn.execute('SELECT id FROM incidents WHERE id LIKE %s', (cfg['incident_prefix'] + '%',)).fetchall()}
            used |= {r['incident_id'] for r in conn.execute('SELECT incident_id FROM orchestration_runs WHERE incident_id LIKE %s', (cfg['incident_prefix'] + '%',)).fetchall()}
    numbers = [int(i[len(cfg['incident_prefix']):]) for i in used if (profile_for(i) or {}).get('profile') == cfg['profile']]
    incident_id = f"{cfg['incident_prefix']}{max(numbers, default=0)+1:03d}"
    _issued.add(incident_id)
    from . import main
    event = main.event_at(incident_id, main.scenario_for(incident_id)['replay_limit_seconds'])[0]
    orchestration = None
    if body.orchestrate:
        from .orchestrator import start
        start(incident_id, OrchestrationRequest(**body.model_dump(exclude={'orchestrate', 'profile'})))
        orchestration = f'/incidents/{incident_id}/orchestration'
    return dict(incident_id=incident_id, profile=cfg['profile'], scenario_id=cfg['scenario_id'], event=event.model_dump(mode='json'),
                orchestration_url=orchestration,
                investigation_url=f"/incidents/{incident_id}/investigation?as_of_seconds={math.ceil(to_scenario_seconds(event.clip_time_seconds, cfg))}")
