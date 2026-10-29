"""Deterministic incident orchestrator: fixed stage sequence that stops at every human decision.

The orchestrator owns stage order, retries and the durable trace. It calls the same
read/compute/workflow functions as the HTTP routes, so state-machine, idempotency and
evidence rules still apply. After a human acts (question, research round, request_changes, repair record)
it resumes the agent work that unblocks. It never reviews, approves, reports, closes or
controls equipment.
"""
from .routes import OPERATOR  # human/operator credential on state-changing routes
import math
import threading
import time
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field
from .contracts import Contract
from .investigation import ModelUnavailable, ToolDenied
from .workflow import WorkflowError

router = APIRouter()
STEPS = ('detect', 'locate', 'quantify', 'freeze', 'investigate', 'plan', 'submit')
FOLLOW_UP_STEPS = ('answer', 'reinvestigate', 'revise', 'submit', 'verify')
# Physics refusals that can clear as the replay clock advances; anything else is final.
RETRYABLE_RATE_REASONS = {'non_steady_plume', 'no_resolved_downwind_signal', 'waiting_for_camera_window'}
# An estimate is kept but the clock keeps advancing while the readings cannot yet separate the camera candidates.
UNDECIDED_ATTRIBUTION = {'partly_agrees', 'agrees_ambiguous'}
MODEL_ATTEMPTS = 2
# Pause before another attempt after a failed one, so a shedding endpoint is not hit again within seconds.
RETRY_PAUSE_SECONDS = 20
MAX_FOLLOW_UPS = 20
# Where an idle run waits, by workflow state. Every exit from these needs an authenticated human.
IDLE_STATUS = {'pending_review': 'awaiting_human_review', 'approved': 'awaiting_report', 'reported': 'awaiting_repair_record',
               'follow_up_required': 'awaiting_repair_record', 'verification_ready': 'awaiting_closure',
               'closed': 'closed', 'rejected': 'rejected'}
FOLLOW_UP_STATUSES = ('awaiting_human_review', 'awaiting_report', 'awaiting_repair_record', 'awaiting_closure')
NEXT_ACTION = {
    'pending_review': 'Authenticated engineer: POST /incidents/{id}/review (approve | reject | request_changes) or ask POST /incidents/{id}/questions',
    'approved': 'Authenticated engineer: POST /incidents/{id}/report',
    'reported': 'Authorized personnel perform field work; authenticated engineer records it: POST /incidents/{id}/repair',
    'follow_up_required': 'Authorized personnel perform further field work; authenticated engineer records it: POST /incidents/{id}/repair',
    'verification_ready': 'Authenticated engineer: POST /incidents/{id}/close (close | keep_open) or ask POST /incidents/{id}/questions',
    'closed': 'None; closed by an authenticated engineer', 'rejected': 'None; rejected by an authenticated engineer'}


class OrchestrationRequest(Contract):
    plan_mode: Literal['model', 'deterministic'] = 'model'
    plan_fallback: Literal['none', 'deterministic'] = 'none'
    quantify_step_seconds: int = Field(default=10, ge=1, le=60)
    quantify_max_wait_seconds: int = Field(default=300, ge=0, le=600)


class StepFailed(RuntimeError):
    pass


def describe(exc):
    """Explicit, non-sensitive failure reason for the trace."""
    if isinstance(exc, HTTPException):
        return dict(error=type(exc).__name__, status_code=exc.status_code, detail=str(exc.detail))
    if isinstance(exc, WorkflowError):
        return dict(error=type(exc).__name__, status_code=exc.status_code, detail=exc.reason)
    if isinstance(exc, (ModelUnavailable, ToolDenied, StepFailed)):
        return dict(error=type(exc).__name__, detail=str(exc))
    return dict(error=type(exc).__name__, detail='unexpected internal failure')


def pending_work(record):
    """Next agent task the workflow state calls for, or None when only a human can move it."""
    if record is None:
        return None
    question = next((q for q in record.get('questions', []) if q['status'] == 'pending'), None)
    if question:
        return 'answer', question['id']
    if record['state'] == 'pending_review' and record.get('research_request'):
        return 'reinvestigate', None
    if record['state'] == 'plan_drafted':
        revise = record.get('revision_required_after', 0) >= record.get('draft_version', 0)
        return ('revise', None) if revise else ('submit', None)
    if record['state'] == 'repair_recorded':
        return 'verify', None
    return None


def summary(record):
    plan = record.get('work_order', {}).get('plan', {})
    rate = record['snapshot']['quantification']
    verification = record.get('verification')
    return dict(incident_state=record['state'], workflow_version=record['version'], candidate_asset_id=record['snapshot']['candidate_asset_id'],
                rate_status=rate['status'], rate_kg_h=rate['rate_kg_h'], plan_origin=plan.get('source_origin'),
                draft_version=record.get('draft_version'), investigation_origin=(record.get('investigation') or {}).get('findings', {}).get('source_origin'),
                questions_answered=sum(q['status'] == 'answered' for q in record.get('questions', [])),
                verification=verification and dict(recommendation=verification['recommendation']['content']['recommendation'],
                                                   recovery_check=verification['recovery_check']['status'], consistency=verification['consistency']),
                next_action=NEXT_ACTION.get(record['state'], 'None'))


class Orchestrator:
    def __init__(self, incident_id, options, durable):
        self.incident_id, self.options, self.durable = incident_id, options, durable
        from . import main, routes
        self.main, self.routes = main, routes

    def emit(self, step, kind, **detail):
        self.durable.orchestration_event(self.incident_id, dict(step=step, kind=kind, detail=detail))

    def stage(self, name, action):
        self.durable.update_orchestration(self.incident_id, 'running', name)
        self.emit(name, 'started')
        started = time.perf_counter()
        try:
            summary = action()
        except Exception as exc:
            self.emit(name, 'failed', elapsed_ms=round((time.perf_counter()-started)*1000, 1), **describe(exc))
            raise
        self.emit(name, 'completed', elapsed_ms=round((time.perf_counter()-started)*1000, 1), **summary)
        return summary

    def record(self):
        try:
            return self.durable.get_incident(self.incident_id)
        except WorkflowError as exc:
            if exc.status_code == 404:
                return None
            raise

    def run(self):
        try:
            if self.record() is None:
                detected = self.stage('detect', self.detect)
                located = self.stage('locate', lambda: self.locate(detected['scenario_seconds']))
                quantified = self.stage('quantify', lambda: self.quantify(located['selected_asset_id'], detected['scenario_seconds']))
                self.stage('freeze', lambda: self.freeze(located['selected_asset_id'], quantified['as_of_seconds']))
            if self.record()['state'] == 'quantified_or_unavailable':
                # Resumed runs keep an investigation that already exists; findings are immutable.
                if not self.record().get('investigation'):
                    self.stage('investigate', self.investigate)
                self.stage('plan', self.plan)
            self.follow_ups()
        except Exception as exc:
            self.durable.update_orchestration(self.incident_id, 'failed', None, describe(exc))

    def follow_ups(self):
        """Do whatever agent work the state calls for, then park until a human acts."""
        def settle(record):
            if pending_work(record):
                return None
            return IDLE_STATUS.get(record['state'], 'idle'), 'human', summary(record)
        for _ in range(MAX_FOLLOW_UPS):
            if self.durable.release_orchestration(self.incident_id, settle):
                return
            kind, argument = pending_work(self.record())
            if kind == 'answer':
                self.stage('answer', lambda: self.answer(argument))
            elif kind == 'reinvestigate':
                self.stage('reinvestigate', self.reinvestigate)
            elif kind == 'revise':
                self.stage('revise', self.revise)
            elif kind == 'submit':
                self.stage('submit', self.submit)
            else:
                self.stage('verify', self.verify)
        raise StepFailed('Follow-up budget exhausted without reaching a human decision point')

    def detect(self):
        scenario = self.main.scenario_for(self.incident_id)
        events = self.main.event_at(self.incident_id, scenario['replay_limit_seconds'])
        if not events:
            raise StepFailed('No detection event for this incident; nothing to investigate')
        event = events[0]
        seconds = math.ceil((event.timestamp_utc-datetime.fromisoformat(scenario['start_utc'])).total_seconds())
        return dict(event_id=event.event_id, mode=event.mode, camera_id=event.camera_id, scenario_seconds=seconds,
                    clip_time_seconds=event.clip_time_seconds, evidence_reference=event.provenance.reference)

    def locate(self, seconds):
        result = self.main.investigation(self.incident_id, seconds, None)
        for call in result['tool_activity']:
            self.emit('locate', 'tool_call', **call)
        candidates = result['localization']['candidates']
        if not candidates:
            raise StepFailed('No camera-mapped candidate equipment: ' + result['localization']['status'])
        # Deterministic choice: highest illustrative overlap; alternatives stay in the evidence.
        return dict(status=result['localization']['status'], selected_asset_id=candidates[0]['asset_id'],
                    candidates=[dict(asset_id=c['asset_id'], overlap_fraction=c['overlap_fraction']) for c in candidates],
                    selection_rule='highest illustrative origin-region overlap; not a confirmed source')

    def quantify(self, asset_id, alert_seconds):
        scenario = self.main.scenario_for(self.incident_id)
        last = min(scenario['replay_limit_seconds'], alert_seconds + self.options['quantify_max_wait_seconds'])
        seconds, estimate = alert_seconds, None
        while True:
            body = self.routes.ReplaySelection(candidate_asset_id=asset_id, as_of_seconds=seconds)
            result = self.routes.selected_calculation(self.incident_id, body)[0]
            attribution = result['telemetry_attribution']
            self.emit('quantify', 'attempt', as_of_seconds=seconds, status=result['status'],
                      rate_kg_h=result['rate_kg_h'], reason=result['reason'], method=result['method'],
                      attribution=attribution['status'], best_fit_asset_id=attribution.get('best_fit_asset_id'),
                      primary_tool=result.get('primary_tool'), tool_roles={t['id']: t['role'] for t in result.get('tools', [])},
                      cross_checks=result.get('cross_checks'))
            if result['status'] == 'estimated':
                estimate = (seconds, result)
                if attribution['status'] not in UNDECIDED_ATTRIBUTION:
                    break
            elif result['reason'] not in RETRYABLE_RATE_REASONS:
                break
            if seconds >= last:
                break
            seconds = min(last, seconds + self.options['quantify_step_seconds'])
        if result['status'] != 'estimated' and estimate and result['reason'] in RETRYABLE_RATE_REASONS:
            # Ran out of wait while undecided: keep the last valid estimate rather than none.
            # A final refusal (e.g. wind lost) stands; an earlier window does not describe the current release.
            seconds, result = estimate
        return dict(as_of_seconds=seconds, status=result['status'], rate_kg_h=result['rate_kg_h'], reason=result['reason'],
                    sensitivity_kg_h=result.get('sensitivity_kg_h'), telemetry_attribution=result['telemetry_attribution'],
                    method=result['method'], primary_tool=result.get('primary_tool'), cross_checks=result.get('cross_checks'))

    def freeze(self, asset_id, seconds):
        body = self.routes.OpenWorkflow(candidate_asset_id=asset_id, as_of_seconds=seconds, idempotency_key='orch-open')
        record = self.routes.open_workflow(self.incident_id, body)
        return dict(state=record['state'], version=record['version'], evidence_references=len(record['evidence_references']))

    def attempts(self, step, tag, call):
        """Model attempts (each a new reserved key), then the explicit labeled fallback if requested."""
        previous = sum(1 for e in self.durable.get_orchestration(self.incident_id)['events']
                       if e['step'] == step and e['kind'] == 'attempt_failed' and e['detail'].get('tag') == tag)
        if self.options['plan_mode'] == 'model':
            modes = [('model', f'orch-{step}-{tag}-model-{previous+i+1}') for i in range(MODEL_ATTEMPTS)]
            if self.options['plan_fallback'] == 'deterministic':
                modes.append(('deterministic', f'orch-{step}-{tag}-deterministic-{previous+1}'))
        else:
            modes = [('deterministic', f'orch-{step}-{tag}-deterministic')]
        for i, (mode, key) in enumerate(modes):
            if i and mode == 'model':
                time.sleep(RETRY_PAUSE_SECONDS)
            if mode == 'deterministic' and self.options['plan_mode'] == 'model':
                self.emit(step, 'fallback', detail='Nemotron unavailable for this step; using labeled deterministic template, not AI output')
            body = self.routes.DraftRequest(mode=mode, expected_version=self.record()['version'], idempotency_key=key)
            try:
                response = call(body, lambda entry: self.emit(step, 'agent_activity', **entry))
            except (ModelUnavailable, ToolDenied, HTTPException, WorkflowError) as exc:
                self.emit(step, 'attempt_failed', mode=mode, idempotency_key=key, tag=tag, **describe(exc))
                continue
            if isinstance(response, JSONResponse):
                self.emit(step, 'attempt_failed', mode=mode, idempotency_key=key, tag=tag, detail='attempt already in progress')
                continue
            return response
        raise StepFailed(f'No {step} output produced; see attempt_failed events')

    def investigate(self):
        record = self.attempts('investigate', 'initial', lambda body, observe: self.routes.run_investigation(self.incident_id, body, observe))
        found = record['investigation']['findings']
        kinds = [f['kind'] for f in found['content']['findings']]
        return dict(source_origin=found['source_origin'], model=(found.get('model_run') or {}).get('model'),
                    findings=len(kinds), contradictions=kinds.count('contradiction'), gaps=kinds.count('gap'),
                    summary=found['content']['summary'])

    def reinvestigate(self):
        request = self.record()['research_request']
        self.emit('reinvestigate', 'research_request', round=request['round'], focus=request['focus'], requested_by=request['requested_by'])
        record = self.attempts('reinvestigate', f"r{request['round']}", lambda body, observe: self.routes.run_reinvestigation(self.incident_id, body, observe))
        found = record['investigation']['findings']
        kinds = [f['kind'] for f in found['content']['findings']]
        return dict(round=request['round'], source_origin=found['source_origin'], model=(found.get('model_run') or {}).get('model'),
                    findings=len(kinds), contradictions=kinds.count('contradiction'), gaps=kinds.count('gap'),
                    changes=found['content'].get('changes', []), summary=found['content']['summary'])

    def plan(self):
        version = self.record().get('draft_version', 0) + 1
        record = self.attempts('plan', f'v{version}', lambda body, observe: self.routes.run_draft_plan(self.incident_id, body, observe))
        plan = record['work_order']['plan']
        return dict(source_origin=plan['source_origin'], generation_method=plan['generation_method'],
                    model=(plan.get('model_run') or {}).get('model'), state=record['state'], version=record['version'], draft_version=record['draft_version'])

    def revise(self):
        current = self.record()
        basis = current.get('revision_basis')
        if basis:
            self.emit('revise', 'research_round', round=basis['round'], requested_by=basis['requested_by'], draft_version=current['draft_version'])
        else:
            feedback = current['review']
            self.emit('revise', 'reviewer_feedback', engineer_id=feedback['engineer_id'], draft_version=feedback['draft_version'], rationale=feedback['rationale'])
        record = self.attempts('revise', f"v{current['draft_version']+1}", lambda body, observe: self.routes.run_draft_plan(self.incident_id, body, observe))
        plan = record['work_order']['plan']
        return dict(source_origin=plan['source_origin'], model=(plan.get('model_run') or {}).get('model'),
                    draft_version=record['draft_version'], revision_of=plan.get('revision_of'))

    def answer(self, question_id):
        question = next(q for q in self.record()['questions'] if q['id'] == question_id)
        self.emit('answer', 'question', question_id=question_id, asked_by=question['asked_by'], question=question['question'])
        record = self.attempts('answer', question_id[:8], lambda body, observe: self.routes.run_answer(self.incident_id, question_id, body, observe))
        answered = next(q for q in record['questions'] if q['id'] == question_id)['answer']['answer']
        return dict(question_id=question_id, source_origin=answered['source_origin'], answer=answered['content']['answer'],
                    cannot_determine_from_evidence=answered['content']['cannot_determine_from_evidence'])

    def verify(self):
        repair = self.record()['repair']
        record = self.attempts('verify', repair['id'][:8], lambda body, observe: self.routes.run_verification(self.incident_id, body, observe))
        verification = record['verification']
        return dict(source_origin=verification['recommendation']['source_origin'], recommendation=verification['recommendation']['content']['recommendation'],
                    recovery_check=verification['recovery_check']['status'], post_repair_rate=verification['recovery_check']['post_repair_rate'],
                    consistency=verification['consistency'], state=record['state'])

    def submit(self):
        record = self.record()
        if record['state'] == 'plan_drafted':
            body = self.routes.Mutation(expected_version=record['version'], idempotency_key=f"orch-submit-v{record['draft_version']}")
            record = self.routes.store().submit_review(self.incident_id, expected_version=body.expected_version, idempotency_key=body.idempotency_key)
        if record['state'] != 'pending_review':
            raise StepFailed('Unexpected workflow state after submission: ' + record['state'])
        return dict(state=record['state'], version=record['version'], draft_version=record['draft_version'])


def launch(incident_id, options, durable):
    threading.Thread(target=Orchestrator(incident_id, options, durable).run, daemon=True,
                     name=f'orchestrator-{incident_id}').start()


def wake(incident_id, human_event=None):
    """After an authenticated human action: log it in the trace and resume any agent work it unblocks."""
    from . import routes
    durable = routes.store()
    try:
        run = durable.get_orchestration(incident_id)
    except WorkflowError:
        return False
    if human_event:
        durable.orchestration_event(incident_id, dict(step='human', kind=human_event.pop('action'), detail=human_event))
    if durable.claim_follow_up(incident_id, FOLLOW_UP_STATUSES):
        launch(incident_id, run['options'], durable)
        return True
    return False


def start(incident_id, options=None):
    """Claim and launch a background run; an active run is returned instead of duplicated."""
    from . import main, routes
    main.scenario_for(incident_id)
    durable = routes.store()
    options = (options or OrchestrationRequest()).model_dump()
    run, claimed = durable.claim_orchestration(incident_id, options)
    if claimed:
        launch(incident_id, options, durable)
    return claimed


@router.post('/incidents/{incident_id}/orchestration', status_code=202, dependencies=OPERATOR)
def start_orchestration(incident_id: str, body: OrchestrationRequest | None = None):
    claimed = start(incident_id, body)
    from .routes import store
    return dict(started=claimed, status_url=f'/incidents/{incident_id}/orchestration',
                **store().get_orchestration(incident_id))


@router.get('/incidents/{incident_id}/orchestration')
def orchestration(incident_id: str):
    from .routes import store
    return {**store().get_orchestration(incident_id), 'steps': STEPS, 'follow_up_steps': FOLLOW_UP_STEPS,
            'boundary': 'Agents stop at every human decision point; approval, report, repair records and closure require an authenticated engineer'}
