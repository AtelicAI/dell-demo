"""Job-based real model execution and timestamped replay assets, no UI changes."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from datetime import datetime,timezone
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Header,Query
from fastapi.responses import FileResponse
from pydantic import Field,model_validator
from .contracts import Contract
from .data import DATA
from .video import lookup_clip

router=APIRouter()
WORKSPACE=Path(__file__).resolve().parents[2]
RUNS=DATA/'detection/runs'
_worker_lock=threading.Lock()


class DetectionRequest(Contract):
    start_seconds:float=Field(default=200,ge=15)
    end_seconds:float=Field(default=230,gt=15)
    sample_interval_seconds:float=Field(default=5,ge=1,le=30)

    @model_validator(mode='after')
    def bounded(self):
        if not 0<self.end_seconds-self.start_seconds<=60:
            raise ValueError('A local model job must cover at most 60 source seconds')
        return self


def directory(run_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',run_id):raise HTTPException(404,'Unknown detection run')
    path=RUNS/run_id
    if not path.resolve().is_relative_to(RUNS.resolve()):raise HTTPException(404,'Invalid run path')
    return path


def write_status(path,status):
    path.mkdir(parents=True,exist_ok=True)
    temporary=path/'job.json.tmp'
    temporary.write_text(json.dumps(status,indent=2)+'\n')
    temporary.replace(path/'job.json')


def read_result(run_id):
    path=directory(run_id)
    if (path/'results.json').is_file():return json.loads((path/'results.json').read_text())
    if (path/'job.json').is_file():return json.loads((path/'job.json').read_text())
    raise HTTPException(404,'Unknown or incomplete detection run')


@router.get('/detection/runs')
def list_runs():
    runs=[]
    for path in sorted(RUNS.glob('*/results.json')):
        data=json.loads(path.read_text())
        runs.append({'run_id':data['run_id'],'clip_id':data.get('clip_id'),'status':data['status'],
                     'method':data.get('model',{}).get('method'),'prediction_count':len(data.get('predictions',[])),
                     'execution':'previously_executed_inference_replay_not_live_camera'})
    return {'runs':runs}


@router.get('/detection/runs/{run_id}')
def detection_run(run_id:str):return read_result(run_id)


@router.get('/detection/runs/{run_id}/assets/{kind}/{filename}')
def detection_asset(run_id:str,kind:str,filename:str):
    if kind not in ('masks','overlays','inputs','differences') or not re.fullmatch(r'[A-Za-z0-9_-]+\.png',filename):
        raise HTTPException(404,'Unknown detection asset')
    run=read_result(run_id)
    allowed={r.get({'masks':'mask_file','overlays':'overlay_file','inputs':'input_file','differences':'difference_file'}[kind]) for r in run.get('predictions',[])}
    if kind+'/'+filename not in allowed:raise HTTPException(404,'Asset is not registered in this inference run')
    path=directory(run_id)/kind/filename
    if not path.resolve().is_relative_to(directory(run_id).resolve()) or not path.is_file():raise HTTPException(404,'Inference asset unavailable')
    return FileResponse(path,media_type='image/png')


@router.get('/videos/{clip_id}/detections')
def detections_for_video(clip_id:str,run_id:str):
    try:clip=lookup_clip(clip_id)
    except KeyError as exc:raise HTTPException(404,'Unknown recorded video') from exc
    run=read_result(run_id)
    if run.get('clip_id')!=clip_id:raise HTTPException(422,'Run belongs to another source recording')
    if run.get('source_sha256')!=clip['sha256']:raise HTTPException(409,'Inference and video checksums disagree')
    preview=DATA/'detection/previews'/f'{clip_id}.json'
    playback=f'/videos/{clip_id}/playback' if preview.is_file() else None
    return {**run,'source_video_url':f'/videos/{clip_id}/content','browser_playback_url':playback,
            'playback_mode':'timestamped_replay_of_executed_model_predictions',
            'overlay_coordinates':'normalized original source pixels; plume extent, not source origin',
            'overlay_policy':'Display prediction timestamp/age; do not invent masks between sampled frames or call replay live inference',
            'asset_mapping_status':'not_calibrated; no synthetic asset attribution inferred from masks'}


@router.get('/videos/{clip_id}/playback')
def browser_playback(clip_id:str,range_header:str|None=Header(None,alias='Range')):
    try:clip=lookup_clip(clip_id)
    except KeyError as exc:raise HTTPException(404,'Unknown recorded video') from exc
    registry=DATA/'detection/previews'/f'{clip_id}.json'
    if not registry.is_file():raise HTTPException(404,'Browser-compatible derivative not generated')
    metadata=json.loads(registry.read_text())
    if metadata['source_sha256']!=clip['sha256']:raise HTTPException(409,'Display derivative does not match registered source')
    path=DATA/'detection/previews'/f'{clip_id}.h264.mp4'
    if not path.is_file() or path.stat().st_size!=metadata['bytes']:raise HTTPException(503,'Display derivative unavailable')
    from .routes import byte_range_video
    return byte_range_video(path,range_header)


@router.get('/videos/{clip_id}/detection-at')
def detection_at(clip_id:str,run_id:str,
                 source_seconds:float=Query(ge=0,allow_inf_nan=False),
                 max_age_seconds:float=Query(default=.25,gt=0,le=1,allow_inf_nan=False)):
    # Reuse source checksum/recording checks without altering existing replay responses.
    run=detections_for_video(clip_id,run_id)
    from .mask_quality import prediction_at
    try:result=prediction_at(run,source_seconds,max_age_seconds)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return {**result,'clip_id':clip_id,'run_id':run_id,
            'overlay_policy':'Render only fresh masks; stale/not_analyzed means unknown, not no gas'}


@router.post('/videos/{clip_id}/detect',status_code=202)
def start_detection(clip_id:str,body:DetectionRequest):
    try:clip=lookup_clip(clip_id)
    except KeyError as exc:raise HTTPException(404,'Unknown recorded video') from exc
    if body.end_seconds>clip['duration_seconds']:raise HTTPException(422,'Interval exceeds original video duration')
    python=Path(os.getenv('DETECTOR_PYTHON',WORKSPACE/'.venv-detector/bin/python'))
    if not python.is_file():raise HTTPException(503,'Optional detector environment not installed; no inference executed')
    if not _worker_lock.acquire(blocking=False):raise HTTPException(409,'A local detector job is already active')
    run_id='inference-'+uuid4().hex
    path=directory(run_id)
    status={'run_id':run_id,'clip_id':clip_id,'status':'queued','requested_at':datetime.now(timezone.utc).isoformat(),
            'source_sha256':clip['sha256'],'request':body.model_dump(),'metrics':None,'metrics_reason':'Inference alone does not establish accuracy'}
    write_status(path,status)
    def execute():
        try:
            status.update(status='running');write_status(path,status)
            command=[str(python),str(WORKSPACE/'scripts/run_detection.py'),'--clip-id',clip_id,
                     '--start',str(body.start_seconds),'--end',str(body.end_seconds),
                     '--interval',str(body.sample_interval_seconds),'--run-id',run_id]
            command.extend(['--output',str(path)])
            inherited={'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT','HF_HOME','HUGGINGFACE_HUB_CACHE','ATELIC_DATA_DIR'}
            environment={k:v for k,v in os.environ.items() if k in inherited}
            environment.update(HF_HUB_OFFLINE='1',PYTORCH_ENABLE_MPS_FALLBACK='1',WANDB_DISABLED='true')
            with (path/'worker.log').open('w') as log:
                result=subprocess.run(command,cwd=WORKSPACE,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=900)
            valid_output=False
            if result.returncode==0 and (path/'results.json').is_file():
                report=json.loads((path/'results.json').read_text())
                valid_output=(report.get('run_id')==run_id and report.get('clip_id')==clip_id
                              and report.get('source_sha256')==clip['sha256']
                              and report.get('status')=='completed' and bool(report.get('predictions')))
            status.update(status='completed' if valid_output else 'failed',exit_code=result.returncode)
            if not valid_output:status['error']='Worker failed or did not produce a matching completed inference report'
        except Exception as exc:
            status.update(status='failed',error_class=type(exc).__name__)
        finally:
            write_status(path,status);_worker_lock.release()
    threading.Thread(target=execute,daemon=True).start()
    return {**status,'status_url':f'/detection/runs/{run_id}'}
