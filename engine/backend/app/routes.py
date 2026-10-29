"""Backend workflow and source APIs; no frontend dependency or physical controls."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import Field
from .contracts import Contract
from .data import load_json
from .historian import HistorianUnavailable
from .investigation import (Investigator, Provider, ModelUnavailable, ToolDenied, INVESTIGATE, REINVESTIGATE, ANSWER, VERIFY,
                            deterministic_draft, deterministic_findings, deterministic_reinvestigation, deterministic_answer,
                            deterministic_verification)
from .process import solve_process
from .quantification import quantify, compare_candidates
from . import rate_tools
from .retrieval import Documents, RetrievalUnavailable
from .video import get_video_manifest, lookup_clip, resolve_video_path, get_annotations
from .workflow import WorkflowStore, WorkflowError, canonical, report_pdf

router = APIRouter()


class ReplaySelection(Contract):
    candidate_asset_id: str = "XV-2104"
    as_of_seconds: int = Field(default=760, ge=0, le=1199)
    tool: str | None = None  # run this rate tool instead of the agent's choice (engineer request)


class OpenWorkflow(ReplaySelection):
    idempotency_key: str = Field(min_length=1, max_length=200)


class Mutation(Contract):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class DraftRequest(Mutation):
    mode: str = "model"


class ReviewRequest(Mutation):
    draft_version: int = Field(ge=1)
    decision: str
    rationale: str = Field(min_length=1, max_length=5000)


def service():
    # Imported lazily to keep deterministic replay functions reusable.
    from . import main
    return main


def store():
    import os
    if not os.getenv("WORKFLOW_DATABASE_URL"):
        raise HTTPException(503, "PostgreSQL workflow is not configured")
    return WorkflowStore()


def selected_calculation(incident_id, body):
    m = service()
    local = m.candidates(incident_id, body.as_of_seconds)
    if body.candidate_asset_id not in {c["asset_id"] for c in local["candidates"]}:
        raise HTTPException(422, "Candidate is not supported by camera evidence at this replay clock")
    history = m.measurements(incident_id, body.as_of_seconds, 300, None)
    result = quantify(m.app.state.facility, history["measurements"], body.candidate_asset_id,
                      history["clock"]["source_timestamp_utc"])
    result["telemetry_attribution"] = compare_candidates(result, {c["asset_id"] for c in local["candidates"]})
    # The quantify agent reads the case and picks the rate tool; the Gaussian fit above is one of its tools.
    clock = history["clock"]
    mapping = clock.get("video_clock_mapping") if isinstance(clock.get("video_clock_mapping"), dict) else {}
    result = rate_tools.run(m.app.state.facility, history["measurements"], clock["source_timestamp_utc"], result,
                            result["telemetry_attribution"], local["candidates"], (local.get("event") or {}).get("camera_id"),
                            mapping.get("clip_id"), clock.get("video_timestamp_seconds"), body.candidate_asset_id,
                            getattr(body, "tool", None))
    result.update(incident_id=incident_id, scenario_id=m.scenario_for(incident_id)["id"],
                  clock=history["clock"], origin_type="derived_from_simulated_measurements",
                  historian=history["backend"], independent_validation="not_completed")
    return result, local, history


def require_operator(authorization: str | None = Header(None)):
    """Every state-changing route needs the provisioned human/operator credential. The orchestrator calls the
    agent steps in process and never goes through these routes."""
    durable = store()
    with durable.connect() as conn:
        return durable._authenticate(conn, authorization)


OPERATOR = [Depends(require_operator)]


@router.get("/datasets")
def datasets():
    manifest = load_json("downloaded_manifest.json")
    # The inventory is metadata, not a static file browser or truth access.
    summary={k:v for k,v in manifest.items() if k not in ("files", "gasvid_logging")}
    return {"source_manifest": load_json("source_manifest.json"), "downloaded_audit": summary,
            "file_inventory_reference": "data/downloaded_manifest.json"}


@router.get("/videos")
def videos():
    return get_video_manifest()


@router.get("/recorded-replays")
def recorded_replays():
    from .video import get_runtime_replays
    return {"replays": get_runtime_replays(), "warning": "Recorded scene observations are not verified methane detection events; no source attribution or sensor synchronization is asserted"}


@router.get("/videos/{clip_id}/clock")
def video_clock(clip_id: str, scenario_id: str, scenario_seconds: float = Query(ge=0)):
    from .video import scenario_to_clip_time
    try:
        return scenario_to_clip_time(clip_id, scenario_id, scenario_seconds)
    except KeyError as exc:
        raise HTTPException(404, "Clip or explicit scenario mapping not registered") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/videos/{clip_id}")
def video_metadata(clip_id: str):
    try:
        clip = lookup_clip(clip_id)
        return {**clip, "content_url": f"/videos/{clip_id}/content", "annotations": get_annotations(clip_id),
                "verified_detection_events": [], "detector_status": "not_integrated"}
    except KeyError as exc:
        raise HTTPException(404, "Unknown allowlisted video ID") from exc


@router.get("/videos/{clip_id}/content")
def video_content(clip_id: str, range_header: str | None = Header(None, alias="Range")):
    try:
        path = resolve_video_path(clip_id)
    except KeyError as exc:
        raise HTTPException(404, "Unknown allowlisted video ID") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(503, "Registered video is unavailable or outside the allowed directory") from exc
    return byte_range_video(path,range_header)


def byte_range_video(path,range_header=None):
    """Shared source/codec-derivative streaming; caller must validate allowlist."""
    size = path.stat().st_size
    start, end, status = 0, size-1, 200
    if range_header:
        import re
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or not any(match.groups()):
            raise HTTPException(416, "Unsupported byte range", headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        if left:
            start, end = int(left), min(size-1, int(right)) if right else size-1
        else:
            start = max(0, size-int(right))
        if start > end or start >= size:
            raise HTTPException(416, "Range outside video", headers={"Content-Range": f"bytes */{size}"})
        status = 206
    def chunks():
        with path.open("rb") as f:
            f.seek(start)
            remaining = end-start+1
            while remaining:
                chunk = f.read(min(262144, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end-start+1), "Cache-Control": "private, max-age=0"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type="video/mp4", headers=headers)


@router.get("/facility/process-model")
def process_model():
    return solve_process(service().app.state.facility)


@router.post("/incidents/{incident_id}/quantify")
def quantify_incident(incident_id: str, body: ReplaySelection):
    return selected_calculation(incident_id, body)[0]


@router.get("/assets/{asset_id}/documents")
def asset_documents(asset_id: str, query: str = Query("inspection engineering evidence", min_length=1, max_length=500)):
    m = service()
    if asset_id not in {a.id for a in m.app.state.facility.assets}:
        raise HTTPException(404, "Unknown canonical asset")
    docs = Documents()
    try:
        return {"asset_id": asset_id, "documents": docs.search(query, asset_id, kind="procedure"),
                "backend": "qdrant", "retrieval_method": "lexical_token_hash_not_neural"}
    finally:
        docs.close()


@router.post("/incidents/{incident_id}/workflow/open", dependencies=OPERATOR)
def open_workflow(incident_id: str, body: OpenWorkflow):
    durable=store()
    with durable.connect() as conn:
        previous=conn.execute('SELECT response FROM requests WHERE incident_id=%s AND key=%s', (incident_id,body.idempotency_key+':quantify')).fetchone()
    if previous:
        response=previous['response']
        frozen=response['snapshot']
        if frozen['candidate_asset_id']!=body.candidate_asset_id or frozen['clock']['scenario_seconds']!=body.as_of_seconds:
            raise WorkflowError('idempotency_key_reused')
        return response
    m = service()
    calculation, local, history = selected_calculation(incident_id, body)
    context = m.asset_context(body.candidate_asset_id, incident_id, body.as_of_seconds)
    # Alternative candidates' history is evidence too, so the selected asset is not favoured by omission.
    alternatives = {c["asset_id"]: m.asset_context(c["asset_id"], incident_id, body.as_of_seconds)
                    for c in local["candidates"] if c["asset_id"] != body.candidate_asset_id}
    alternative_maintenance = [r for a in alternatives.values() for r in a["maintenance"]]
    docs = Documents()
    try:
        procedures = docs.search("inspection source verification engineering review", body.candidate_asset_id, kind="procedure")
        references = docs.search("design leak points packing gasket torque connectivity", body.candidate_asset_id, kind=("datasheet", "manual", "drawing"))
    finally:
        docs.close()
    if not procedures:
        raise HTTPException(422, "No applicable example procedure found; engineering review required")
    # Freeze actual inputs and references, not changing tool latencies or hidden truth.
    snapshot = dict(incident_id=incident_id, candidate_asset_id=body.candidate_asset_id,
                    clock=history["clock"], events=m.incident(incident_id, body.as_of_seconds)["events"],
                    candidates=[{k:v for k,v in c.items() if k != "context"} for c in local["candidates"]],
                    context=context, alternative_maintenance=alternative_maintenance,
                    measurements=history["measurements"], quantification=calculation,
                    procedures=procedures, reference_documents=references, provenance=load_json("provenance.json"),
                    video_evidence_status=("monitoring alert from executed model inference on recorded footage; footage is a different physical scene; reviewed annotations unavailable"
                                           if any(e["mode"] == "model_inference" for e in m.incident(incident_id, body.as_of_seconds)["events"])
                                           else "recorded footage present; reviewed annotation events unavailable"))
    # Field evidence across all camera candidates: reports, promised follow-ups, record links, prior incidents.
    snapshot.update(field_evidence([context, *alternatives.values()]))
    # Plant history before the scenario start: episode timing, quiet stretches and location consistency.
    plant=m.long_term_history(incident_id,[c['asset_id'] for c in local['candidates']])['analysis']
    if plant['status']=='analyzed':
        snapshot['long_term_history']=plant
    snapshot['measurements_reference']=f"measurements:{history['measurements'][0]['scenario_id']}:{body.as_of_seconds}:300" if history['measurements'] else f'measurements:{incident_id}:empty'
    snapshot['calculation_reference']=f'calculation:{incident_id}:{body.as_of_seconds}:{body.candidate_asset_id}'
    selected_candidate=next(c for c in local['candidates'] if c['asset_id']==body.candidate_asset_id)
    snapshot['localization_evidence_ids']=selected_candidate['evidence']
    snapshot['camera_mapping']={'geometry_version':m.app.state.facility.geometry_version,
                                'cameras':[c.model_dump(mode='json') for c in m.app.state.facility.cameras]}
    evidence = [{"id": context["evidence_reference"], "uri": context["evidence_reference"], "source_kind": "simulated"}]
    evidence += [{"id": p["citation"], "uri": p["citation"], "source_kind": p["source_kind"], "content_sha256": p["content_sha256"]} for p in procedures + references]
    evidence += [{'id':snapshot[k],'uri':snapshot[k],'source_kind':'derived'} for k in ('measurements_reference','calculation_reference')]
    registered={e['id'] for e in evidence}
    for reference in sorted({ref for candidate in local['candidates'] for ref in candidate['evidence']}):
        if reference not in registered:
            evidence.append({'id':reference,'uri':reference,'source_kind':'simulated'})
            registered.add(reference)
    evidence += [{'id':'maintenance:'+r['record']['id'],'uri':'neo4j:WorkOrder/'+r['record']['id'],'source_kind':'simulated'} for r in context['maintenance'] + alternative_maintenance]
    evidence += [{"id": r["observation_id"], "uri": "observation:"+r["observation_id"], "source_kind": r["origin_type"]} for r in history["measurements"]]
    if 'long_term_history' in snapshot:
        evidence += [{'id':i,'uri':i,'source_kind':'simulated'} for i in history_evidence_ids(snapshot['long_term_history'])]
    evidence += [{'id':i,'uri':i,'source_kind':'simulated'} for i in field_evidence_ids(snapshot)]
    record = durable.create_incident(incident_id, snapshot, evidence, idempotency_key=body.idempotency_key+":create")
    record = durable.transition(incident_id, "localized_unconfirmed", expected_version=record["version"], idempotency_key=body.idempotency_key+":locate")
    return durable.transition(incident_id, "quantified_or_unavailable", expected_version=record["version"], idempotency_key=body.idempotency_key+":quantify")


@router.get("/incidents/{incident_id}/workflow")
def workflow_incident(incident_id: str):
    return store().get_incident(incident_id)


@router.post("/incidents/{incident_id}/draft-plan", dependencies=OPERATOR)
def draft_plan(incident_id: str, body: DraftRequest):
    return run_draft_plan(incident_id, body)


def summarize(rows, reference, clock):
    """Per-sensor summary so agent context stays bounded; full rows stay in the frozen record."""
    summaries=[]
    for sensor_id in sorted({r['sensor_id'] for r in rows}):
        series=[r for r in rows if r['sensor_id']==sensor_id]
        good=[r['value'] for r in series if r['quality_flag']=='good' and r['value'] is not None]
        summaries.append({'sensor_id':sensor_id,'unit':series[0]['unit'],'count':len(series),
                          'good_count':len(good),'min_good':min(good) if good else None,
                          'max_good':max(good) if good else None,
                          'first_observation':series[0],'last_observation':series[-1]})
    return {'evidence_reference':reference,'origin':'summarized_simulated_observations',
            'clock':clock,'summaries':summaries,'full_window_retained_in_frozen_snapshot':True}


def field_evidence(contexts):
    """Union of graph field evidence over the candidates' contexts, each item with its own evidence ID."""
    def union(key, inner, prefix):
        seen = {}
        for context in contexts:
            for row in context.get(key, []):
                item = row[inner]
                seen.setdefault(item['id'], {**item, 'evidence_id': prefix + item['id']})
        return [seen[k] for k in sorted(seen)]
    links = sorted({(l['from_id'], l['relationship'], l['to_id']) for c in contexts for l in c.get('work_order_links', [])})
    return dict(field_reports=union('field_reports', 'report', 'field_report:'),
                commitments=union('commitments', 'commitment', 'commitment:'),
                prior_incidents=union('prior_incidents', 'incident', 'incident_record:'),
                work_order_links=[dict(from_id=a, relationship=r, to_id=b) for a, r, b in links])


def field_evidence_ids(snapshot):
    return [x['evidence_id'] for key in ('field_reports', 'commitments', 'prior_incidents') for x in snapshot.get(key, [])]


def history_evidence_ids(analysis):
    operations=[analysis['load_change_alignment']['evidence_id']] if analysis.get('load_change_alignment') else []
    return [analysis['evidence_id']]+operations+[e['evidence_id'] for e in analysis['episodes']]


def cited(content, fields):
    return sorted({e for field in fields for claim in content.get(field, []) for e in claim['evidence_ids']})


def evidence_tools(record):
    """Read-only tools over the frozen incident scope; never credentials or write methods."""
    snap = record["snapshot"]
    tools = {"get_camera_candidates": lambda:{'evidence_ids':sorted({ref for c in snap['candidates'] for ref in c['evidence']}),'candidates':snap["candidates"]},
             "query_asset_graph":lambda:snap["context"],
             "get_sensor_history":lambda:summarize(snap['measurements'],snap['measurements_reference'],snap['clock']),
             "search_maintenance":lambda:{'evidence_ids':['maintenance:'+r['record']['id'] for r in snap['context']['maintenance']+snap.get('alternative_maintenance',[])]+field_evidence_ids(snap),
                                          'records':snap["context"]["maintenance"],'alternative_candidate_records':snap.get('alternative_maintenance',[]),
                                          'field_reports':snap.get('field_reports',[]),'commitments':snap.get('commitments',[]),
                                          'work_order_links':snap.get('work_order_links',[]),'prior_incidents':snap.get('prior_incidents',[])},
             "retrieve_procedure":lambda:{'evidence_ids':[p['citation'] for p in snap['procedures']+snap.get('reference_documents',[])],'procedures':snap["procedures"],'reference_documents':snap.get('reference_documents',[])},
             "estimate_emissions":lambda:{**snap["quantification"],'evidence_reference':snap['calculation_reference']}}
    if snap.get('long_term_history'):
        tools['get_long_term_history'] = lambda: {'evidence_ids':history_evidence_ids(snap['long_term_history']),**snap['long_term_history']}
    investigation = record.get('investigation')
    if investigation:
        found = investigation['findings']
        tools['get_investigation_findings'] = lambda: {'evidence_ids':cited(found['content'],('findings','candidate_assessment')),
                                                       'source_origin':found['source_origin'],'findings':found['content']}
    notes = record.get('field_notes')
    if notes:
        tools['get_field_notes'] = lambda: {'evidence_ids':[n['evidence_id'] for n in notes],
                                            'note':'Recorded by engineers after the evidence was frozen; unverified human observations',
                                            'field_notes':[dict(id=n['evidence_id'], text=n['text'], recorded_by=n['recorded_by'],
                                                                recorded_at=n['recorded_at']) for n in notes]}
    plan = record.get('work_order',{}).get('plan')
    if plan:
        draft = plan['evidence_backed_draft']['content']
        tools['get_current_draft'] = lambda: {'evidence_ids':cited(draft,('observed_evidence','hypotheses','recommended_checks')),
                                              'draft_version':record['draft_version'],'source_origin':plan['source_origin'],'draft':draft}
    return tools


def allowed_evidence(record):
    return ([e['id'] for e in record['evidence_references'] if not e['id'].startswith('SCN-')]
            + [n['evidence_id'] for n in record.get('field_notes', [])])


def answered(record, before=None):
    """The engineer's questions answered so far (the conversation), oldest first."""
    out = []
    for q in record.get('questions', []):
        if q['id'] == before:
            break
        if q['status'] == 'answered':
            out.append(dict(question=q['question'], answer=(q.get('answer') or {}).get('answer', {}).get('content', {}).get('answer')))
    return out


def model_run(engine):
    return {"provider":engine.provider.url, "model":engine.provider.model, "run_id":str(uuid4()), "generated_at":datetime.now(timezone.utc).isoformat()}




def prior_response(durable, incident_id, key, request=None):
    """Saved response for a retried key, only when the retry is the same request (same action and body)."""
    with durable.connect() as conn:
        row=conn.execute('SELECT response FROM requests WHERE incident_id=%s AND key=%s', (incident_id,key)).fetchone()
        if row and request is not None:
            job=conn.execute('SELECT fingerprint FROM draft_jobs WHERE incident_id=%s AND key=%s', (incident_id,key)).fetchone()
            # Model attempts are bound to their action and body; a deterministic save has no job row to compare.
            if job and job['fingerprint']!=hashlib.sha256(canonical(request).encode()).hexdigest():
                raise WorkflowError('idempotency_key_reused')
    return row['response'] if row else None


def job_request(body, action):
    """What a request key is bound to: the action (and its target) plus the body."""
    return {**body.model_dump(), 'action': action}


def run_agent(durable, incident_id, body, version, engine, states, save, action):
    """One reserved paid model attempt, saved durably; retries with the same key never call the model twice."""
    if not engine.provider.available():
        raise ModelUnavailable('Model unavailable; no inference executed. Configure a provider/model or explicitly request deterministic mode')
    request = job_request(body, action)
    if not durable.reserve_draft_job(incident_id,body.idempotency_key,request,version,states):
        return prior_response(durable, incident_id, body.idempotency_key, request) or IN_PROGRESS(body.idempotency_key)
    try:
        response=save(engine.run())
    except Exception as exc:
        durable.finish_draft_job(incident_id,body.idempotency_key,'failed',{'error_class':type(exc).__name__,'activity':engine.activity})
        raise
    durable.finish_draft_job(incident_id,body.idempotency_key,'completed',{'activity':engine.activity})
    return response


def provenance(result, engine=None):
    if engine is None:
        return {**result, 'source_origin':'deterministic', 'generation_method':'deterministic_template'}
    return {**result, 'source_origin':'llm', 'generation_method':'bounded_read_only_tool_calling', 'model_run':model_run(engine)}


def IN_PROGRESS(key):
    return JSONResponse(status_code=202,content={'status':'in_progress','idempotency_key':key,'retry_after_seconds':2})


@router.post("/incidents/{incident_id}/investigate", dependencies=OPERATOR)
def investigate(incident_id: str, body: DraftRequest):
    return run_investigation(incident_id, body)


def run_investigation(incident_id, body, observer=None):
    """Separate investigation agent: findings, contradictions and gaps before any work order."""
    durable = store()
    previous = prior_response(durable, incident_id, body.idempotency_key, job_request(body, 'investigate'))
    if previous:
        return previous
    record = durable.get_incident(incident_id)
    def save(findings):
        return durable.save_investigation(incident_id, findings, expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    if body.mode == "deterministic":
        return save(provenance(deterministic_findings(record['snapshot'])))
    if body.mode == "model":
        tools = {k:v for k,v in evidence_tools(record).items() if k in Investigator.EVIDENCE}
        engine = Investigator(tools, allowed_evidence(record), Provider(), observer, task=INVESTIGATE)
        return run_agent(durable, incident_id, body, body.expected_version, engine, ('quantified_or_unavailable','plan_drafted'),
                         lambda result: save(provenance(result, engine)), 'investigate')
    raise HTTPException(422, "mode must explicitly be model or deterministic")


def run_draft_plan(incident_id, body, observer=None):
    """Shared by the HTTP route and the orchestrator; observer streams agent activity."""
    durable = store()
    # Idempotent API retries must not execute a second paid model invocation.
    previous = prior_response(durable, incident_id, body.idempotency_key)
    api_request=body.model_dump()
    if previous:
        if previous.get('work_order',{}).get('plan',{}).get('api_request') != api_request:
            raise WorkflowError('idempotency_key_reused')
        return previous
    record = durable.get_incident(incident_id)
    if body.mode!='model' and record['version'] != body.expected_version:
        raise WorkflowError('version_conflict')
    if body.mode!='model' and record['state'] not in ('quantified_or_unavailable','plan_drafted'):
        raise WorkflowError('draft_not_allowed')
    snap = record["snapshot"]
    asset_id = snap["candidate_asset_id"]
    basis = record.get('revision_basis')
    revising = record.get('review',{}).get('decision') == 'request_changes' or bool(basis)
    def save(saved):
        if basis:
            saved['revision_of'] = dict(draft_version=record['draft_version'], research_round=basis['round'])
        elif revising:
            saved['revision_of'] = dict(draft_version=record['review']['draft_version'], review_id=record['review']['id'])
        saved['api_request']=api_request
        return durable.save_draft(incident_id, saved, expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    if body.mode == "deterministic":
        plan = deterministic_draft(asset_id, snap["context"]["evidence_reference"], snap["procedures"], snap["quantification"],snap['localization_evidence_ids'])
        if revising:
            plan['content']['unresolved_questions'].append('Reviewer feedback not incorporated: the deterministic template cannot revise content.')
        saved = {"text": json.dumps(plan["content"]), "source_origin": "deterministic", "generation_method": "deterministic_template", "evidence_backed_draft": plan}
    elif body.mode == "model":
        tools = evidence_tools(record)
        if record.get('investigation'):
            # Findings already distil the raw evidence; the planner turns them into checks.
            tools = {k:tools[k] for k in ('get_investigation_findings','get_camera_candidates','retrieve_procedure','estimate_emissions','get_field_notes') if k in tools}
        else:
            tools = {k:v for k,v in tools.items() if k in Investigator.EVIDENCE}
        if basis:
            draft, found = record['work_order']['plan']['evidence_backed_draft']['content'], record['investigation']['findings']['content']
            tools['get_review_feedback'] = lambda: {'evidence_ids':cited(draft,('observed_evidence','hypotheses','recommended_checks')),
                'reviewer':basis['requested_by'],'decision':'further_research',
                'feedback':(f"Research round {basis['round']} changed the findings; revise the draft to match them. "
                            f"Engineer's focus: {basis['focus'] or 'none given'}. What changed: " + '; '.join(found.get('changes', []))),
                'previous_draft_version':record['draft_version'],'previous_draft':draft,'answered_questions':answered(record)}
        elif revising:
            review, draft = record['review'], record['work_order']['plan']['evidence_backed_draft']['content']
            tools['get_review_feedback'] = lambda: {'evidence_ids':cited(draft,('observed_evidence','hypotheses','recommended_checks')),
                'reviewer':review['engineer_id'],'decision':review['decision'],'feedback':review['rationale'],
                'previous_draft_version':review['draft_version'],'previous_draft':draft,
                'answered_questions':[dict(question=q['question'],answer=(q.get('answer') or {}).get('answer',{}).get('content')) for q in record.get('questions',[])]}
        engine = Investigator(tools, allowed_evidence(record), Provider(), observer)
        return run_agent(durable, incident_id, body, body.expected_version, engine, ('quantified_or_unavailable','plan_drafted'),
                         lambda plan: save({"text": json.dumps(plan["content"]), "source_origin": "llm", "generation_method": "bounded_read_only_tool_calling",
                                            "model_run": model_run(engine), "evidence_backed_draft":plan}), 'plan')
    else:
        raise HTTPException(422, "mode must explicitly be model or deterministic")
    return save(saved)


class QuestionRequest(Mutation):
    question: str = Field(min_length=1, max_length=2000)


class FieldNoteRequest(Mutation):
    text: str = Field(min_length=1, max_length=3000)


class ResearchRequest(Mutation):
    focus: str = Field(default='', max_length=2000)


class RepairRequest(Mutation):
    work_performed: str = Field(min_length=1, max_length=5000)


class CloseRequest(Mutation):
    decision: str
    rationale: str = Field(min_length=1, max_length=5000)


def wake(incident_id, **human_event):
    """Human input may unblock an orchestrated incident; manual incidents are left alone."""
    from .orchestrator import wake as resume
    resume(incident_id, human_event)


@router.post("/incidents/{incident_id}/questions")
def ask_question(incident_id: str, body: QuestionRequest, authorization: str | None = Header(None)):
    record = store().ask_question(incident_id, body.question, authorization=authorization,
                                  expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    wake(incident_id, action='question', question_id=record['questions'][-1]['id'], question=body.question,
         asked_by=record['questions'][-1]['asked_by'])
    return record


@router.post("/incidents/{incident_id}/field-notes")
def add_field_note(incident_id: str, body: FieldNoteRequest, authorization: str | None = Header(None)):
    """An engineer's field observation, added beside the frozen evidence; it wakes no agent by itself."""
    return store().add_field_note(incident_id, body.text, authorization=authorization,
                                  expected_version=body.expected_version, idempotency_key=body.idempotency_key)


@router.post("/incidents/{incident_id}/research")
def request_research(incident_id: str, body: ResearchRequest, authorization: str | None = Header(None)):
    """Another research round before approval: the investigator reruns with the field notes and answers so far,
    then the planner revises the draft. The draft only changes through that revision, and still needs approval."""
    record = store().request_research(incident_id, body.focus, authorization=authorization,
                                      expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    request = record['research_request']
    wake(incident_id, action='research', round=request['round'], focus=body.focus, requested_by=request['requested_by'])
    return record


@router.post("/incidents/{incident_id}/reinvestigate", dependencies=OPERATOR)
def reinvestigate(incident_id: str, body: DraftRequest):
    return run_reinvestigation(incident_id, body)


def run_reinvestigation(incident_id, body, observer=None):
    """Investigation agent again, over the same frozen evidence plus field notes, earlier findings and answers."""
    durable = store()
    previous = prior_response(durable, incident_id, body.idempotency_key, job_request(body, 'reinvestigate'))
    if previous:
        return previous
    record = durable.get_incident(incident_id)
    request = record.get('research_request')
    if not request:
        raise WorkflowError('reinvestigation_not_requested')
    def save(findings):
        # Questions and field notes may arrive while the agent works; they never conflict with this round.
        for attempt in range(3):
            try:
                return durable.save_reinvestigation(incident_id, findings, expected_version=durable.get_incident(incident_id)['version'],
                                                    idempotency_key=body.idempotency_key)
            except WorkflowError as exc:
                if exc.reason != 'version_conflict' or attempt == 2:
                    raise
    if body.mode == "deterministic":
        return save(provenance(deterministic_reinvestigation(record['snapshot'], record.get('field_notes', []))))
    if body.mode == "model":
        all_tools = evidence_tools(record)
        tools = {k:v for k,v in all_tools.items() if k in Investigator.EVIDENCE or k == 'get_field_notes'}
        found = record['investigation']['findings']
        tools['get_previous_findings'] = lambda: {'evidence_ids':cited(found['content'],('findings','candidate_assessment')),
                                                  'round':record['investigation'].get('round', 1),'findings':found['content'],
                                                  'answered_questions':answered(record)}
        focus = request['focus'].strip()
        engine = Investigator(tools, allowed_evidence(record), Provider(), observer, task=REINVESTIGATE,
                              question=f"Research round {request['round']}. Engineer's focus: {focus or 'none given; re-check everything'}")
        return run_agent(durable, incident_id, body, record['version'], engine, ('pending_review',),
                         lambda result: save(provenance(result, engine)), 'reinvestigate')
    raise HTTPException(422, "mode must explicitly be model or deterministic")


@router.post("/incidents/{incident_id}/questions/{question_id}/answer", dependencies=OPERATOR)
def answer(incident_id: str, question_id: str, body: DraftRequest):
    return run_answer(incident_id, question_id, body)


def run_answer(incident_id, question_id, body, observer=None):
    """Answer agent: reads frozen evidence only and never changes the draft or the review state."""
    durable = store()
    previous = prior_response(durable, incident_id, body.idempotency_key, job_request(body, f'answer:{question_id}'))
    if previous:
        return previous
    record = durable.get_incident(incident_id)
    question = next((q for q in record.get('questions',[]) if q['id']==question_id), None)
    if question is None or question['status'] != 'pending':
        raise WorkflowError('question_not_pending')
    def save(result):
        # Answers append to the record; questions/answers alone never conflict with a review decision.
        for attempt in range(3):
            try:
                return durable.answer_question(incident_id, question_id, result, expected_version=durable.get_incident(incident_id)['version'],
                                               idempotency_key=body.idempotency_key)
            except WorkflowError as exc:
                if exc.reason != 'version_conflict' or attempt == 2:
                    raise
    if body.mode == "deterministic":
        return save(provenance(deterministic_answer()))
    if body.mode == "model":
        tools = evidence_tools(record)
        allowed = allowed_evidence(record) + [e['id'] for e in (record.get('verification') or {}).get('evidence_references', [])]
        if record.get('verification'):
            verification = record['verification']
            tools['get_post_repair_readings'] = lambda: {'evidence_ids':[e['id'] for e in verification['evidence_references']],'verification':verification}
        earlier = answered(record, before=question_id)[-4:]
        thread = ''.join(f"Earlier question: {q['question']}\nEarlier answer: {q['answer']}\n" for q in earlier)
        engine = Investigator(tools, allowed, Provider(), observer, task=ANSWER,
                              question=thread + ('Engineer follow-up question: ' if thread else 'Engineer question: ') + question['question'])
        return run_agent(durable, incident_id, body, record['version'], engine, None, lambda result: save(provenance(result, engine)), f'answer:{question_id}')
    raise HTTPException(422, "mode must explicitly be model or deterministic")


@router.post("/incidents/{incident_id}/repair")
def record_repair(incident_id: str, body: RepairRequest, authorization: str | None = Header(None)):
    record = store().record_repair(incident_id, body.work_performed, authorization=authorization,
                                   expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    wake(incident_id, action='repair_recorded', recorded_by=record['repair']['recorded_by'], work_performed=body.work_performed)
    return record


# Replay parameters for the simulated post-repair window; disclosed in provenance.json ("post-1200s counterfactual").
REPAIR_REPLAY_SECONDS = 1200
RECOVERY_SETTLE_SECONDS = 300
RECOVERY_WINDOW_SECONDS = 240
BASELINE_WINDOW_SECONDS = 240


def recovery_check(incident_id, record):
    """Deterministic physics check of post-repair readings against the pre-alert baseline."""
    m = service()
    scenario = m.scenario_for(incident_id)
    snap = record['snapshot']
    start = datetime.fromisoformat(scenario['start_utc'])
    alert = min(int((datetime.fromisoformat(e['timestamp_utc'].replace('Z','+00:00'))-start).total_seconds()) for e in snap['events'])
    baseline_end = max(BASELINE_WINDOW_SECONDS, alert-60)
    after = REPAIR_REPLAY_SECONDS + RECOVERY_SETTLE_SECONDS
    try:
        baseline = m.app.state.historian.history(scenario, baseline_end, BASELINE_WINDOW_SECONDS)
        post = m.app.state.historian.history(scenario, after, RECOVERY_WINDOW_SECONDS)
    except HistorianUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    sensors = {s.id: s for s in m.app.state.facility.sensors}
    rows = []
    for sensor_id in sorted({r['sensor_id'] for r in post if sensors[r['sensor_id']].variable == 'methane'}):
        good = lambda data: [r['value'] for r in data if r['sensor_id']==sensor_id and r['quality_flag']=='good' and r['value'] is not None]
        before, now, s = good(baseline), good(post), sensors[sensor_id]
        limit = (max(before) if before else None)
        margin = max(s.detection_limit, 3*s.uncertainty)
        rows.append(dict(sensor_id=sensor_id, baseline_max_ppm=limit, post_repair_max_ppm=max(now) if now else None,
                         allowed_max_ppm=limit+margin if limit is not None else None,
                         within_baseline=bool(now and limit is not None and max(now) <= limit+margin)))
    end = (start+timedelta(seconds=after)).isoformat()
    rate = quantify(m.app.state.facility, post, snap['candidate_asset_id'], end)
    status = ('insufficient_data' if not rows or any(r['post_repair_max_ppm'] is None or r['baseline_max_ppm'] is None for r in rows)
              else 'background_restored' if all(r['within_baseline'] for r in rows) else 'residual_signal')
    scenario_id = scenario['id']
    refs = dict(post=f'measurements:{scenario_id}:post_repair:{after}:{RECOVERY_WINDOW_SECONDS}',
                baseline=f'measurements:{scenario_id}:baseline:{baseline_end}:{BASELINE_WINDOW_SECONDS}',
                check=f'calculation:{incident_id}:recovery:{after}')
    return dict(status=status, sensors=rows, post_repair_rate=dict(status=rate['status'], reason=rate['reason'], rate_kg_h=rate['rate_kg_h']),
                post_repair_window=dict(end_scenario_seconds=after, window_seconds=RECOVERY_WINDOW_SECONDS),
                baseline_window=dict(end_scenario_seconds=baseline_end, window_seconds=BASELINE_WINDOW_SECONDS),
                origin_type='simulated_counterfactual_recovery_fixture', references=refs,
                warning='Simulated post-repair readings; not evidence that equipment was repaired at a real site'), baseline, post


def run_verification(incident_id, body, observer=None):
    """Verifier agent drafts a closure recommendation; only a human can close the incident."""
    durable = store()
    previous = prior_response(durable, incident_id, body.idempotency_key, job_request(body, 'verify'))
    if previous:
        return previous
    record = durable.get_incident(incident_id)
    if record['state'] != 'repair_recorded':
        raise WorkflowError('verification_not_allowed')
    check, baseline, post = recovery_check(incident_id, record)
    refs = check['references']
    work_order = 'work_order:' + record['work_order']['id']
    repair = 'repair:' + record['repair']['id']
    evidence = [dict(id=refs['post'], uri=refs['post'], source_kind='simulated'), dict(id=refs['baseline'], uri=refs['baseline'], source_kind='simulated'),
                dict(id=refs['check'], uri=refs['check'], source_kind='derived'), dict(id=work_order, uri=work_order, source_kind='workflow'),
                dict(id=repair, uri=repair, source_kind='human_record')]
    def save(result):
        agrees = (result['content']['recommendation'] == 'close') == (check['status'] == 'background_restored')
        verification = dict(recommendation=result, recovery_check=check, evidence_references=evidence,
                            consistency='agrees_with_deterministic_check' if agrees else 'conflicts_with_deterministic_check')
        return durable.save_verification(incident_id, verification, expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    if body.mode == "deterministic":
        return save(provenance(deterministic_verification(check, [refs['check']])))
    if body.mode == "model":
        plan = record['work_order']['plan']
        tools = {'get_post_repair_readings': lambda: summarize(post, refs['post'], check['post_repair_window']),
                 'get_pre_incident_baseline': lambda: summarize(baseline, refs['baseline'], check['baseline_window']),
                 'get_recovery_check': lambda: {**check, 'evidence_reference': refs['check']},
                 'get_approved_work_order': lambda: {'evidence_ids':[work_order, repair], 'approved_plan':plan['evidence_backed_draft']['content'],
                                                     'review':record['review'], 'repair_record':record['repair']}}
        engine = Investigator(tools, [e['id'] for e in evidence], Provider(), observer, task=VERIFY)
        return run_agent(durable, incident_id, body, body.expected_version, engine, ('repair_recorded',), lambda result: save(provenance(result, engine)), 'verify')
    raise HTTPException(422, "mode must explicitly be model or deterministic")


@router.post("/incidents/{incident_id}/verify", dependencies=OPERATOR)
def verify(incident_id: str, body: DraftRequest):
    return run_verification(incident_id, body)


@router.post("/incidents/{incident_id}/close")
def close(incident_id: str, body: CloseRequest, authorization: str | None = Header(None)):
    record = store().close(incident_id, body.decision, body.rationale, authorization=authorization,
                           expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    wake(incident_id, action='closure', engineer_id=record['closure']['engineer_id'], decision=body.decision, rationale=body.rationale)
    if record['state'] == 'closed':
        record = {**record, 'graph_memory': remember(record)}
    return record


def incident_memory(record, schema, facility_id):
    """What a closed incident teaches the next one: flat properties only (Neo4j), no hidden truth."""
    snap = record['snapshot']
    q = snap['quantification']
    draft = record['work_order']['plan']['evidence_backed_draft']['content']
    verification = record.get('verification', {})
    return dict(id=record['id'], workflow_schema=schema, facility_id=facility_id, origin_type='workflow_closure',
                occurred_at_utc=(snap['events'][0]['timestamp_utc'] if snap['events'] else snap['clock']['source_timestamp_utc']),
                selected_asset_id=snap['candidate_asset_id'], candidate_asset_ids=[c['asset_id'] for c in snap['candidates']],
                rate_status=q['status'], rate_kg_h=q.get('rate_kg_h'),
                telemetry_attribution=q.get('telemetry_attribution', {}).get('status'),
                approved_plan_title=draft['title'], approved_checks=[c['statement'] for c in draft['recommended_checks']],
                work_performed=record['repair']['work_performed'],
                verification_recommendation=verification.get('recommendation', {}).get('content', {}).get('recommendation'),
                recovery_check=verification.get('recovery_check', {}).get('status'),
                closure_rationale=record['closure']['rationale'], closed_by=record['closure']['engineer_id'],
                closed_at_utc=record['closure']['recorded_at'])


def remember(record):
    """Write the closed incident to the graph; closure stands even if the graph is unavailable."""
    m = service()
    try:
        return m.app.state.graph.remember_incident(incident_memory(record, store().schema, m.app.state.facility.id))
    except Exception as exc:  # the durable record is authoritative; report the failed write, never hide it
        return dict(status='failed', error=type(exc).__name__)


@router.post("/incidents/{incident_id}/submit-review", dependencies=OPERATOR)
def submit_review(incident_id: str, body: Mutation):
    return store().submit_review(incident_id, expected_version=body.expected_version, idempotency_key=body.idempotency_key)


@router.post("/incidents/{incident_id}/review")
def review(incident_id: str, body: ReviewRequest, authorization: str | None = Header(None)):
    record = store().review(incident_id, body.decision, body.rationale, body.draft_version,
                            authorization=authorization, expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    wake(incident_id, action='review', engineer_id=record['review']['engineer_id'], decision=body.decision,
         draft_version=body.draft_version, rationale=body.rationale)
    return record


@router.post("/incidents/{incident_id}/report")
def issue_report(incident_id: str, body: Mutation, authorization: str | None = Header(None)):
    durable = store()
    # Report issue requires the same authenticated human boundary as review.
    with durable.connect() as conn:
        durable._authenticate(conn, authorization)
    record = durable.create_report(incident_id, expected_version=body.expected_version, idempotency_key=body.idempotency_key)
    wake(incident_id, action='report_issued', issued_at=record['report']['issued_at'])
    return record


@router.get("/incidents/{incident_id}/report")
def read_report(incident_id: str, format: str = "json"):
    record = store().get_incident(incident_id)
    if not record.get("report"):
        raise HTTPException(409, "No approved issued report")
    if format == "pdf":
        return Response(content=report_pdf(record["report"]), media_type="application/pdf", headers={"Content-Disposition":f'attachment; filename="methane-incident-report.pdf"'})
    if format != "json":
        raise HTTPException(422, "format must be json or pdf")
    return record["report"]


@router.get("/incidents/{incident_id}/audit")
def audit(incident_id: str):
    durable = store()
    durable.get_incident(incident_id)
    return {"events": durable.get_audit(incident_id), "hash_chain_valid": durable.verify_audit(incident_id), "limitation": "Not tamper-proof against the database owner; no external head anchoring"}


@router.get('/incidents/{incident_id}/recovery-preview')
def recovery_preview(incident_id: str, as_of_seconds: int = Query(1260,ge=1200,le=1800), authorization: str | None = Header(None)):
    durable=store()
    with durable.connect() as conn:
        reviewer=durable._authenticate(conn,authorization)
    record=durable.get_incident(incident_id)
    if record['state'] not in ('approved','reported','repair_recorded','verification_ready','closed','follow_up_required') or record.get('review',{}).get('decision')!='approve':
        raise HTTPException(409,'Simulated recovery preview requires an approved draft')
    m=service()
    scenario=m.scenario_for(incident_id)
    try:
        rows=m.app.state.historian.history(scenario,as_of_seconds,60)
    except HistorianUnavailable as exc:
        raise HTTPException(503,str(exc)) from exc
    return {'incident_id':incident_id,'scenario_seconds':as_of_seconds,'measurements':rows,
            'origin_type':'simulated','requested_by':reviewer,'physical_action_performed':False,
            'warning':'Precomputed counterfactual recovery fixture, not evidence that equipment was repaired or emissions stopped at a real site'}
