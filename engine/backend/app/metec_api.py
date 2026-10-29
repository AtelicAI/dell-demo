"""Additive read-only measured-incident benchmark APIs; no labels or workflow controls."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict
from .metec import RecordedBenchmark,estimate,utc
from . import metec_store

router=APIRouter(prefix='/benchmarks/metec2024',tags=['Measured METEC benchmark'])


class EstimateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_id:str
    as_of_utc:datetime|None=None
    backend:Literal['artifact','influxdb']='influxdb'


def selected(incident_id=None):
    try:facility,case,rows=RecordedBenchmark().read()
    except (FileNotFoundError,ValueError) as exc:raise HTTPException(503,'Measured benchmark is not prepared') from exc
    if incident_id is not None and incident_id!=case['id']:raise HTTPException(404,'Unknown measured benchmark incident')
    return facility,case,rows


def history_rows(case,rows,as_of,backend):
    try:end=utc(as_of or case['history_end_utc'])
    except ValueError as exc:raise HTTPException(422,'Timezone-aware timestamp required') from exc
    try:
        if backend=='influxdb':return metec_store.influx_history(case,end)
        return [r for r in rows if utc(r['timestamp_utc'])<=end]
    except Exception as exc:
        raise HTTPException(503,'Recorded historian unavailable or invalid; no silent artifact fallback') from exc


@router.get('/facility')
def facility():
    return selected()[0]


@router.get('/incidents')
def incidents():
    return dict(incidents=[selected()[1]],truth_available=False,paired_video=None)


@router.get('/incidents/{incident_id}/measurements')
def measurements(incident_id:str,as_of_utc:datetime|None=None,backend:Literal['artifact','influxdb']='influxdb'):
    f,c,rows=selected(incident_id);data=history_rows(c,rows,as_of_utc,backend)
    return dict(incident_id=c['id'],measurements=data,count=len(data),backend='influxdb' if backend=='influxdb' else 'recorded_public_artifact',
                origin_type='recorded_aggregated',source_rate_truth_available=False)


@router.post('/incidents/{incident_id}/estimate')
def calculation(incident_id:str,body:EstimateRequest):
    f,c,rows=selected(incident_id)
    if body.candidate_id not in c['candidate_ids']:raise HTTPException(422,'Unknown measured candidate')
    end=body.as_of_utc or c['analysis_end_utc']
    rows=history_rows(c,rows,end,body.backend)
    result=estimate(f,c,rows,body.candidate_id,end)
    return {**result,'incident_id':c['id'],'historian':body.backend,'as_of_utc':utc(end).isoformat(),
            'tool_execution':'deterministic_calculation_executed','ground_truth_accessed':False}


@router.get('/incidents/{incident_id}/graph')
def graph(incident_id:str,as_of_utc:datetime|None=None):
    f,c,rows=selected(incident_id)
    try:
        from .main import app
        return metec_store.graph_context(app.state.graph,f['id'],as_of_utc or c['history_end_utc'])
    except Exception as exc:raise HTTPException(503,'Recorded Neo4j projection unavailable; no synthetic fallback') from exc
