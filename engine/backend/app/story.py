"""Recorded demo run for playback when live inference is too slow or unavailable on stage."""
import json
from fastapi import APIRouter, HTTPException
from .data import DATA

router = APIRouter()


@router.get('/story/recorded-run')
def recorded_run():
    """Serve the saved story recording; always labeled as recorded, never presented as live."""
    path = DATA / 'story/recorded_run.json'
    if not path.is_file():
        raise HTTPException(404, 'No recorded run saved; run scripts/story_run.py --save')
    return {**json.loads(path.read_text()), 'playback': 'recorded_not_live'}


# The answer key is shown only after the engineer's decisions are on record, so it cannot anchor review.
REVEAL_STATES = ('reported', 'repair_recorded', 'verification_ready', 'closed', 'follow_up_required')


@router.get('/incidents/{incident_id}/reveal')
def reveal(incident_id: str):
    """Compare the frozen estimate with the metered release behind the recorded footage."""
    from . import main, monitor, routes
    record = routes.store().get_incident(incident_id)
    if record['state'] not in REVEAL_STATES:
        raise HTTPException(409, 'Answer key is available once the report is issued, so it cannot anchor the review')
    cfg = monitor.profile_for(incident_id)
    facts = monitor.footage(cfg) if cfg else None
    scenario = main.scenario_for(incident_id)
    event = next(iter(main.event_at(incident_id, scenario['replay_limit_seconds'])), None)
    segment = monitor.segment_at(event.clip_time_seconds, facts) if facts and event and event.clip_time_seconds is not None else None
    if segment is None:
        raise HTTPException(404, 'No metered release is published for the footage behind this incident')
    q = record['snapshot']['quantification']
    attribution = q.get('telemetry_attribution', {})
    estimate = q['rate_kg_h'] * 1000 if q.get('rate_kg_h') is not None else None
    metered = segment['metered_rate_g_h']
    return dict(
        incident_id=incident_id, state=record['state'],
        footage=dict(clip_id=facts['clip_id'], alert_clip_seconds=event.clip_time_seconds, gasvid_class=segment['class'],
                     metered_rate_g_h=metered, ci95_g_h=segment['ci95_g_h'], site=facts['site'], recorded_date=facts['recorded_date'],
                     release_location=facts['release_location'], camera=facts['camera'], imaging_distance_m=facts['imaging_distance_m'],
                     source=facts['leak_classes_source'], segment_timing_basis=facts['segment_timing_basis']),
        plant_alignment=facts['plant_alignment'],
        estimate=dict(status=q['status'], reason=q.get('reason'), rate_g_h=estimate,
                      as_of_scenario_seconds=record['snapshot']['clock']['scenario_seconds'],
                      telemetry_attribution=attribution.get('status'), best_fit_asset_id=attribution.get('best_fit_asset_id')),
        error_percent=round((estimate - metered) / metered * 100, 1) if estimate is not None else None,
        note='Answer key for people only; never an agent input. The plant is synthetic and releases the same metered rate; '
             'agreement shows the method recovers a known rate under its own assumptions, not field accuracy.')
