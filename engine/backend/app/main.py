from .routes import OPERATOR  # human/operator credential on state-changing routes
import copy
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from threading import Lock
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError, DriverError
from .contracts import DetectionEvent
from .data import load_facility, load_json
from .graph import Graph
from .historian import create_historian, HistorianUnavailable
from .history import analyze, hourly_series

def validate_event_reference(event, scenarios, facility):
    scenario=next((s for s in scenarios if s['incident_id']==event.incident_id),None)
    if not scenario or scenario['id']!=event.scenario_id:
        raise ValueError('detection scenario/incident mismatch')
    if event.camera_id not in {c.id for c in facility.cameras}:
        raise ValueError('detection camera is not canonical')
    elapsed=(event.timestamp_utc-datetime.fromisoformat(scenario['start_utc'])).total_seconds()
    if not 0<=elapsed<=scenario['replay_limit_seconds']:
        raise ValueError('detection outside replay boundary')

def _at(value):
    return datetime.fromisoformat(value+'T00:00:00+00:00' if len(value)==10 else value.replace('Z','+00:00'))

def maintenance_as_of(context, timestamp):
    """Records as they stood at the replay time: nothing dated later, commitments open until fulfilled."""
    result=dict(context)
    result['maintenance']=[m for m in context['maintenance'] if _at(m['record']['date'])<=timestamp]
    result['field_reports']=[r for r in context.get('field_reports',[]) if _at(r['report']['date'])<=timestamp]
    commitments=[]
    for row in context.get('commitments',[]):
        c=dict(row['commitment'])
        if _at(c['work_order_date'])>timestamp:continue
        if c.get('fulfilled_by_date') and _at(c['fulfilled_by_date'])>timestamp:
            c.update(status='open',fulfilled_by=None,fulfilled_by_date=None)
        commitments.append(dict(commitment=c))
    result['commitments']=commitments
    result['work_order_links']=[l for l in context.get('work_order_links',[]) if _at(l['from_date'])<=timestamp and _at(l['to_date'])<=timestamp]
    result['prior_incidents']=[i for i in context.get('prior_incidents',[]) if _at(i['incident']['occurred_at_utc'])<timestamp]
    return result

@asynccontextmanager
async def lifespan(app):
    app.state.facility=load_facility()
    app.state.historian=create_historian(app.state.facility)
    app.state.scenarios=load_json("scenarios/scenarios.json")
    events=[DetectionEvent.model_validate(e) for e in load_json("scenarios/detections.json")]
    if len({e.event_id for e in events}) != len(events):
        raise ValueError('duplicate detection event ID')
    for event in events:
        validate_event_reference(event, app.state.scenarios, app.state.facility)
    app.state.events={e.event_id:e for e in events}
    app.state.event_lock=Lock()
    app.state.graph=Graph()
    yield
    app.state.graph.close()
    app.state.historian.close()

app=FastAPI(title="Atelic Methane Incident Intelligence", version="0.1.0",lifespan=lifespan)

from .routes import router
from .workflow import WorkflowError
from .investigation import ModelUnavailable, ToolDenied
from .retrieval import RetrievalUnavailable
import psycopg

app.include_router(router)
from .metec_api import router as metec_router
app.include_router(metec_router)
from .detection_api import router as detection_router
app.include_router(detection_router)
from . import monitor
app.include_router(monitor.router)
from .orchestrator import router as orchestration_router
app.include_router(orchestration_router)
from .story import router as story_router
app.include_router(story_router)

@app.exception_handler(WorkflowError)
async def workflow_error(request: Request, exc: WorkflowError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.reason})

@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exc: psycopg.Error):
    return JSONResponse(status_code=503, content={"detail": "PostgreSQL workflow unavailable; initialize the isolated schema and runtime credentials"})

@app.exception_handler(ModelUnavailable)
async def model_error(request: Request, exc: ModelUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc), "draft_saved": False})

@app.exception_handler(ToolDenied)
async def tool_error(request: Request, exc: ToolDenied):
    return JSONResponse(status_code=422, content={"detail": str(exc)})

@app.exception_handler(RetrievalUnavailable)
async def retrieval_error(request: Request, exc: RetrievalUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

def scenario_for(incident_id):
    scenario=next((s for s in app.state.scenarios if s['incident_id']==incident_id),None)
    profile=monitor.profile_for(incident_id) if scenario is None else None
    if profile:
        # Monitoring incidents replay their profile's synthetic scenario under their own durable ID.
        base=next(s for s in app.state.scenarios if s['id']==profile['scenario_id'])
        scenario={**base,'incident_id':incident_id}
    if scenario is None:raise HTTPException(404,"Unknown incident")
    return scenario

def clock(scenario, seconds):
    if seconds>scenario['replay_limit_seconds']:
        raise HTTPException(422,"Replay beyond pre-intervention boundary requires authenticated human approval; not implemented")
    profile=monitor.profile_for(scenario['incident_id'])
    if profile:
        clip_seconds=monitor.to_clip_seconds(seconds,profile)
        return dict(scenario_seconds=seconds,source_timestamp_utc=(datetime.fromisoformat(scenario['start_utc'])+timedelta(seconds=seconds)).isoformat(),video_timestamp_seconds=clip_seconds,video_clock_mapping=dict(clip_id=profile['clip_id'],mapping_kind='synthetic_approximate',source_time_synchronized=False,basis=profile['mapping_basis']),origin_type="simulated")
    from .video import get_video_manifest, scenario_to_clip_time
    manifest=get_video_manifest()
    clip_id=manifest.get('default_clip_id')
    mapped=None
    if clip_id:
        try:mapped=scenario_to_clip_time(clip_id,scenario['id'],seconds)
        except (KeyError,ValueError):pass
    return dict(scenario_seconds=seconds,source_timestamp_utc=(datetime.fromisoformat(scenario['start_utc'])+timedelta(seconds=seconds)).isoformat(),video_timestamp_seconds=mapped['clip_seconds'] if mapped else None,video_clock_mapping=mapped or 'No approximate mapping for this scenario',origin_type="simulated")

def graph_context(asset_id):
    try:
        result=app.state.graph.context(asset_id)
    except (Neo4jError,DriverError,OSError) as exc:
        raise HTTPException(503,"Neo4j unavailable; graph evidence cannot be retrieved") from exc
    if result is None:raise HTTPException(404,"Unknown graph asset; seed canonical facility first")
    return result

def event_at(incident_id, seconds):
    s=scenario_for(incident_id)
    end=datetime.fromisoformat(s['start_utc'])+timedelta(seconds=seconds)
    events=[e for e in app.state.events.values() if e.incident_id==incident_id]
    if monitor.is_monitor_incident(incident_id):
        derived=monitor.incident_event(incident_id,s)
        events+=[derived] if derived else []
    return sorted([e for e in events if e.timestamp_utc<=end],key=lambda e:(e.timestamp_utc,e.event_id))

@app.get('/health')
def health():
    try:app.state.graph.query("RETURN 1 AS ok");graph='connected'
    except (Neo4jError,DriverError,OSError):graph='unavailable'
    historian_ok=app.state.historian.health()
    from .investigation import Provider
    from .retrieval import Documents
    import os
    docs=Documents() if os.getenv('QDRANT_API_KEY') else None
    docs_ok=docs.health() if docs else False
    if docs:docs.close()
    workflow_ok=False
    if os.getenv('WORKFLOW_DATABASE_URL'):
        try:
            from .workflow import WorkflowStore
            with WorkflowStore().connect() as c:
                c.execute('SELECT 1 FROM incidents LIMIT 1')
            workflow_ok=True
        except psycopg.Error:pass
    from .data import DATA
    recorded_model=(DATA/'detection/model_manifest.json').is_file()
    return dict(status='ok' if graph=='connected' and historian_ok and docs_ok and workflow_ok else 'degraded',graph=graph,historian=app.state.historian.backend,historian_status='connected' if historian_ok else 'unavailable',documents='qdrant_connected' if docs_ok else 'unavailable',workflow='postgresql_connected' if workflow_ok else 'unavailable',detector='synthetic incident fixture; separate recorded pretrained inference available' if recorded_model else 'synthetic_annotation_fixture; recorded labels pending',recorded_detector='executed_local_pretrained; real_GasVid_accuracy_unvalidated' if recorded_model else 'not_executed',nvidia='configured_not_verified' if Provider().available() else 'not_configured',dell='deployment_not_verified')

@app.get('/facility')
def facility():return app.state.facility.model_dump(mode='json')

@app.get('/incidents/{incident_id}')
def incident(incident_id:str,as_of_seconds:int=Query(760,ge=0)):
    s=scenario_for(incident_id);c=clock(s,as_of_seconds)
    return dict(id=incident_id,scenario_id=s['id'],title=s['title'],state='localized_unconfirmed' if event_at(incident_id,as_of_seconds) else 'no_observation',clock=c,provenance=load_json('provenance.json'),events=[e.model_dump(mode='json') for e in event_at(incident_id,as_of_seconds)],persistence='fixture_replay_only')

@app.get('/incidents/{incident_id}/video')
def video(incident_id:str):
    from .video import get_video_manifest,lookup_clip,get_annotations
    s=scenario_for(incident_id)
    manifest=get_video_manifest()
    clip=lookup_clip(manifest['default_clip_id']) if manifest.get('default_clip_id') else None
    return dict(status=manifest['status'],clip=clip,content_url=f"/videos/{clip['id']}/content" if clip else None,annotations=get_annotations(clip['id']) if clip else [],rights=manifest.get('rights'),source_time_synchronized=False,warning='Recorded footage and synthetic facility/telemetry describe different physical scenes; detection fixture is not an annotation of this footage')

def bounds(poly):return min(p[0] for p in poly),min(p[1] for p in poly),max(p[0] for p in poly),max(p[1] for p in poly)
def overlap(a,b):
    x,y,X,Y=bounds(a);u,v,U,V=bounds(b)
    intersection=max(0,min(X,U)-max(x,u))*max(0,min(Y,V)-max(y,v))
    return intersection/((X-x)*(Y-y))

@app.get('/incidents/{incident_id}/candidates')
def candidates(incident_id:str,as_of_seconds:int=Query(760,ge=0)):
    s=scenario_for(incident_id);c=clock(s,as_of_seconds);events=event_at(incident_id,as_of_seconds)
    if not events:return dict(candidates=[],status='no_observation',clock=c)
    event=events[-1]
    camera=next((c for c in app.state.facility.cameras if c.id==event.camera_id),None)
    if camera is None or not camera.regions:return dict(candidates=[],status='mapping_unavailable',clock=c)
    results=[];traces=[]
    for region in camera.regions:
        score=overlap(event.origin_polygon,region.polygon)
        if not score:continue
        started=time.perf_counter();context=graph_context(region.asset_id)
        context=maintenance_as_of(context,datetime.fromisoformat(c['source_timestamp_utc']))
        traces.append(dict(tool='query_asset_graph',asset_id=region.asset_id,backend='neo4j',elapsed_ms=round((time.perf_counter()-started)*1000,2),status='completed'))
        results.append(dict(asset_id=region.asset_id,overlap_fraction=round(score,4),score_kind='origin bounding-region overlap; not a probability',confirmed=False,why=(f"Model alert on recorded footage triggered this incident; the approximate synthetic origin region overlaps the {event.camera_id} mapping for this asset. Graph confirms canonical asset and connections, not leak causality" if event.mode=='model_inference' else f"Synthetic origin region overlaps approximate {event.camera_id} mapping; graph confirms canonical asset and connections, not leak causality"),evidence=[event.provenance.reference,f"camera:{camera.id}/region/{region.asset_id}",context['evidence_reference']],context=context))
    results.sort(key=lambda x:(-x['overlap_fraction'],x['asset_id']))
    return dict(candidates=results,status='ambiguous_unconfirmed' if len(results)>1 else 'unconfirmed',warning='Camera mapping is approximate; source not confirmed.',event=event.model_dump(mode='json'),clock=c,tool_activity=traces)

@app.get('/assets/{asset_id}/context')
def asset_context(asset_id:str,incident_id:str='INC-001',as_of_seconds:int=Query(760,ge=0)):
    if asset_id not in {a.id for a in app.state.facility.assets}:raise HTTPException(404,'Unknown canonical asset')
    scenario=scenario_for(incident_id)
    replay=clock(scenario,as_of_seconds)
    return maintenance_as_of(graph_context(asset_id),datetime.fromisoformat(replay['source_timestamp_utc']))

@app.get('/incidents/{incident_id}/measurements')
def measurements(incident_id:str,as_of_seconds:int=Query(760,ge=0),window_seconds:int=Query(300,ge=1,le=1800),asset_id:str|None=None):
    s=scenario_for(incident_id);c=clock(s,as_of_seconds)
    selected=None
    if asset_id is not None:
        context=asset_context(asset_id,incident_id,as_of_seconds)
        selected={r['sensor']['id'] for r in context['sensors']}
        selected.update(sensor.id for sensor in app.state.facility.sensors if sensor.variable.startswith('wind'))
    try:
        rows=app.state.historian.history(s,as_of_seconds,window_seconds,selected)
    except HistorianUnavailable as exc:
        raise HTTPException(503,str(exc)) from exc
    return dict(measurements=rows,clock=c,backend=app.state.historian.backend,origin_type='simulated',count=len(rows))

HISTORY_DAYS=60
_plant_history={}

def long_term_history(incident_id, focus_asset_ids):
    """Plant history before the scenario start (causal cutoff), analyzed once per process."""
    s=scenario_for(incident_id)
    end=datetime.fromisoformat(s['start_utc'])
    key=(end,tuple(sorted(focus_asset_ids)))
    if key not in _plant_history:
        try:rows=app.state.historian.long_history(end-timedelta(days=HISTORY_DAYS),end)
        except HistorianUnavailable as exc:raise HTTPException(503,str(exc)) from exc
        log=load_json('operations/load_changes.json')
        before=dict(log,events=[e for e in log['events'] if datetime.fromisoformat(e['time_utc'])<end])
        analysis=analyze(app.state.facility,rows,key[1],before)
        # Operating log entries during the replay window (before any alert can be raised), e.g. the K-201 load step.
        analysis['incident_day_operations']=dict(evidence_id=log['evidence_id'],
            events=[e for e in log['events'] if end<=datetime.fromisoformat(e['time_utc'])<end+timedelta(seconds=s['replay_limit_seconds'])],
            note='Operating log entries after the history window, inside the incident replay; read with the pressure trace')
        _plant_history[key]=dict(analysis=analysis,series=hourly_series(app.state.facility,rows))
    return copy.deepcopy(_plant_history[key])

@app.get('/quantification/methods')
def quantification_methods():
    """Rate methods: which one runs in the demo, and how each did in pre-registered tests on real METEC 2024 data."""
    return load_json('quantification/methods.json')

@app.get('/graph/summary')
def graph_summary():
    """Node and edge counts of the plant knowledge graph (excludes the separate METEC benchmark graph)."""
    try:return app.state.graph.summary(app.state.facility.id)
    except (Neo4jError,DriverError,OSError) as exc:raise HTTPException(503,"Neo4j unavailable") from exc

@app.get('/graph/plant')
def graph_plant():
    """Plant equipment, sensors, cameras, work orders, documents, field reports, commitments and incident memory
    with all relationships between them, read from Neo4j (history episodes and load changes are left out)."""
    try:return app.state.graph.plant(app.state.facility.id)
    except (Neo4jError,DriverError,OSError) as exc:raise HTTPException(503,"Neo4j unavailable") from exc

@app.post('/incident-memory/reset', dependencies=OPERATOR)
def reset_incident_memory():
    """Forget every closed incident written to the graph for this facility (demo reset). Plant records stay."""
    try:return dict(removed=app.state.graph.forget_incidents(app.state.facility.id))
    except (Neo4jError,DriverError,OSError) as exc:raise HTTPException(503,"Neo4j unavailable") from exc

@app.get('/assets/{asset_id}/incident-memory')
def incident_memory(asset_id:str):
    """Closed incidents that considered this asset (written at closure). Agents see them only through the
    replay-time cutoff in the asset context; this view is unfiltered, for people."""
    if asset_id not in {a.id for a in app.state.facility.assets}:raise HTTPException(404,'Unknown canonical asset')
    return dict(asset_id=asset_id,incidents=graph_context(asset_id)['prior_incidents'])

@app.get('/incidents/{incident_id}/plant-history')
def plant_history(incident_id:str,include_series:bool=False):
    s=scenario_for(incident_id)
    focus=[c['asset_id'] for c in candidates(incident_id,s['replay_limit_seconds'])['candidates']]
    result=long_term_history(incident_id,focus)
    return dict(analysis=result['analysis'],series=result['series'] if include_series else None,
                backend=app.state.historian.backend,origin_type='simulated',
                note='Synthetic plant history before the incident; wind and background variability derived from measured METEC 2024 data')

@app.get('/incidents/{incident_id}/investigation')
def investigation(incident_id:str,as_of_seconds:int=Query(760,ge=0),asset_id:str|None=None):
    result=candidates(incident_id,as_of_seconds)
    if asset_id is None and result['candidates']:asset_id=result['candidates'][0]['asset_id']
    started=time.perf_counter()
    selected_context=asset_context(asset_id,incident_id,as_of_seconds) if asset_id else None
    selected_trace=([dict(tool='query_asset_graph',asset_id=asset_id,backend='neo4j',elapsed_ms=round((time.perf_counter()-started)*1000,2),status='completed')] if asset_id else [])
    started=time.perf_counter()
    history=measurements(incident_id,as_of_seconds,300,asset_id)
    trace=dict(tool='get_sensor_history',backend=history['backend'],elapsed_ms=round((time.perf_counter()-started)*1000,2),status='completed',observation_count=history['count'])
    return dict(incident=incident(incident_id,as_of_seconds),localization=result,history=history,selected_asset_id=asset_id,selected_context=selected_context,tool_activity=result.get('tool_activity',[])+selected_trace+[trace],agent_status='No LLM executed; deterministic backend tools only')

@app.post('/events/detections', dependencies=OPERATOR)
def ingest_event(event:DetectionEvent):
    try:validate_event_reference(event,app.state.scenarios,app.state.facility)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    # Until external provenance verification exists, accept only the exact local fixture.
    fixtures=[DetectionEvent.model_validate(e) for e in load_json('scenarios/detections.json')]
    if event not in fixtures:raise HTTPException(422,'External event ingestion unavailable; only verified local synthetic fixtures accepted')
    with app.state.event_lock:
        old=app.state.events.get(event.event_id)
        if old and old!=event:raise HTTPException(409,'Event ID conflicts with existing content')
        app.state.events[event.event_id]=event
    return dict(event_id=event.event_id,duplicate=old is not None,persistence='process_memory_fixture_only')
