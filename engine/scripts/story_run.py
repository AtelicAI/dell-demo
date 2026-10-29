"""Live end-to-end demo story through the real HTTP routes; saves a recorded run for replay.

Beats: below-detection control stays quiet → leak alert (metered release on camera) → plant history →
quantify → investigate → plan → engineer asks a question → agent answers → engineer requests changes →
agent revises → approve → report → answer key → repair recorded → verifier checks recovery → engineer
closes; then the wind-outage contrast run to pending_review.

Uses the real Neo4j/InfluxDB/Qdrant services, hosted Nemotron (unless --deterministic) and a
disposable PostgreSQL schema with a smoke-test reviewer; primary incidents are never touched.
The engineer actions are scripted test inputs, not real engineering decisions.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
for line in (ROOT / '.env').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key.strip()) and key.strip() != 'ENGINEER_TOKEN':
            os.environ.setdefault(key.strip(), value)

from fastapi.testclient import TestClient  # noqa: E402
from app import routes  # noqa: E402
from app.main import app  # noqa: E402
from app.workflow import WorkflowStore  # noqa: E402

TOKEN = 'story-human-token-' + uuid4().hex
AUTH = {'Authorization': 'Bearer ' + TOKEN}
QUESTION = 'WO-009 says the XV-2104 gland was re-torqued and verified. Why is XV-2104 still the leading candidate rather than F-205?'
CHANGES = ('Before any packing work, add a check of the missing F-205 bolt torque record from WO-004, '
           'and make the OGI re-survey requested in WO-008 the first field check.')
REPAIR = ('Scripted demo record: contractor torque record for F-205 (WO-004) obtained and within DS-F-205; '
          'OGI re-survey requested in WO-008 found an indication at the XV-2104 stem packing; authorized crew repacked '
          'the stem per DS-XV-2104 and a follow-up OGI survey of XV-2104 and F-205 showed no indication.')


class Story:
    def __init__(self, client):
        self.client, self.started, self.beats = client, time.perf_counter(), []

    def now(self):
        return round(time.perf_counter() - self.started, 1)

    def beat(self, name, **detail):
        self.beats.append(dict(t_seconds=self.now(), beat=name, **detail))
        print(f'[{self.now():>6}s] {name}', json.dumps(detail)[:220])

    def call(self, method, path, status=200, **kwargs):
        response = getattr(self.client, method)(path, **kwargs)
        if response.status_code != status:
            raise SystemExit(f'{method.upper()} {path} -> {response.status_code}: {response.text[:2000]}')
        return response.json()

    def watch(self, incident, until):
        """Print new trace events until the run parks at the expected human decision point."""
        shown = len(self.call('get', f'/incidents/{incident}/orchestration')['events'])
        while True:
            run = self.call('get', f'/incidents/{incident}/orchestration')
            for event in run['events'][shown:]:
                d = event['detail']
                note = d.get('tool') or d.get('status') or d.get('reason') or d.get('detail') or ''
                if event['kind'] in ('completed', 'failed'):
                    note = f"{round(d.get('elapsed_ms', 0)/1000, 1)}s {d.get('error', '')}"
                print(f"          [{event['sequence']:>3}] {event['step']:<11} {event['kind']:<16} {note}")
            shown = len(run['events'])
            if run['status'] != 'running':
                if run['status'] != until:
                    raise SystemExit(f'Run parked at {run["status"]}, expected {until}: {json.dumps(run["result"])[:2000]}')
                return run
            time.sleep(1)

    def monitor_until_alert(self, profile):
        cfg = self.call('get', '/monitor', params={'profile': profile})
        state, alerts = None, 0
        for second in range(cfg['clip_start_seconds'], cfg['clip_end_seconds'] + 1):
            status = self.call('get', '/monitor/status', params={'clip_seconds': second, 'profile': profile})
            if status['state'] != state:
                state = status['state']
                self.beat('monitor_state', profile=profile, clip_seconds=second, scenario_seconds=status['scenario_seconds'], state=state)
            if state == 'alert':
                return status['alert']
        return None


def record(client, mode):
    story = Story(client)
    # 1. No-leak control: the whole recording replays without an alert and no incident can be opened.
    assert story.monitor_until_alert('no_leak') is None
    refused = client.post('/monitor/incidents', json={'profile': 'no_leak', 'plan_mode': mode})
    story.beat('no_leak_control', alert=False, register_status=refused.status_code, detail=refused.json().get('detail'))

    # 2. Leak: alert, then the orchestrator runs every agent step up to the engineer.
    alert = story.monitor_until_alert('leak')
    registered = story.call('post', '/monitor/incidents', json={'profile': 'leak', 'plan_mode': mode})
    incident = registered['incident_id']
    on_camera = story.call('get', '/monitor/status', params={'clip_seconds': alert['clip_seconds']})['metered_release']
    story.beat('alert_registered', incident_id=incident, alert=alert, on_camera_metered_release=on_camera)
    story.watch(incident, 'awaiting_human_review')
    record = story.call('get', f'/incidents/{incident}/workflow')
    plant = record['snapshot']['long_term_history']
    story.beat('plant_history', episodes=plant['episode_count'], first_episode=plant['episodes'][0]['start_utc'],
               episodes_by_week=plant['episodes_by_week'], quiet_stretches=plant['quiet_stretches'],
               location_consistency=plant['location_consistency']['episodes_consistent_with'])
    quantified = record['snapshot']['quantification']
    story.beat('quantified', as_of_seconds=record['snapshot']['clock']['scenario_seconds'], rate_kg_h=quantified['rate_kg_h'],
               attribution=quantified['telemetry_attribution']['status'], best_fit=quantified['telemetry_attribution']['best_fit_asset_id'])
    findings = record['investigation']['findings']
    story.beat('investigation_ready', source_origin=findings['source_origin'], summary=findings['content']['summary'],
               findings=[f"[{f['kind']}] {f['statement']}" for f in findings['content']['findings']])
    first_draft = record['work_order']['plan']['evidence_backed_draft']['content']
    story.beat('draft_v1_pending_review', title=first_draft['title'], recommended_checks=[c['statement'] for c in first_draft['recommended_checks']])

    # 3. The engineer asks for more information; the answer agent replies from frozen evidence.
    story.call('post', f'/incidents/{incident}/questions', headers=AUTH,
               json={'question': QUESTION, 'expected_version': record['version'], 'idempotency_key': 'story-question'})
    story.beat('engineer_question', question=QUESTION)
    story.watch(incident, 'awaiting_human_review')
    record = story.call('get', f'/incidents/{incident}/workflow')
    answer = record['questions'][0]['answer']['answer']
    story.beat('agent_answer', source_origin=answer['source_origin'], answer=answer['content']['answer'],
               cannot_determine=answer['content']['cannot_determine_from_evidence'])

    # 4. The engineer requests changes; the planner revises with that feedback and resubmits.
    story.call('post', f'/incidents/{incident}/review', headers=AUTH, json={
        'expected_version': record['version'], 'idempotency_key': 'story-changes', 'draft_version': record['draft_version'],
        'decision': 'request_changes', 'rationale': CHANGES})
    story.beat('engineer_requests_changes', rationale=CHANGES)
    story.watch(incident, 'awaiting_human_review')
    record = story.call('get', f'/incidents/{incident}/workflow')
    revised = record['work_order']['plan']['evidence_backed_draft']['content']
    story.beat('draft_v2_pending_review', draft_version=record['draft_version'], title=revised['title'],
               recommended_checks=[c['statement'] for c in revised['recommended_checks']])

    # 5. Approve and issue the report (scripted reviewer in a disposable schema).
    record = story.call('post', f'/incidents/{incident}/review', headers=AUTH, json={
        'expected_version': record['version'], 'idempotency_key': 'story-approve', 'draft_version': record['draft_version'],
        'decision': 'approve', 'rationale': 'Scripted demo approval in a disposable schema; not a real engineering approval'})
    story.watch(incident, 'awaiting_report')
    record = story.call('post', f'/incidents/{incident}/report', headers=AUTH, json={'expected_version': record['version'], 'idempotency_key': 'story-report'})
    pdf = client.get(f'/incidents/{incident}/report?format=pdf').content
    story.beat('approved_and_reported', report_pdf_sha256=hashlib.sha256(pdf).hexdigest())
    answer_key = story.call('get', f'/incidents/{incident}/reveal')
    story.beat('answer_key', metered_rate_g_h=answer_key['footage']['metered_rate_g_h'], estimate_g_h=answer_key['estimate']['rate_g_h'],
               error_percent=answer_key['error_percent'], footage=answer_key['footage'])
    story.watch(incident, 'awaiting_repair_record')

    # 6. Field work is recorded by a human; the verifier checks post-repair readings.
    # The scripted crew record signs off each approved check by number, as a field sign-off sheet would.
    approved = record['work_order']['plan']['evidence_backed_draft']['content']['recommended_checks']
    work = (REPAIR + ' Approved work order checks signed off as completed: '
            + ' '.join(f'({i}) {c["statement"]}' for i, c in enumerate(approved, 1)))[:4000]
    story.call('post', f'/incidents/{incident}/repair', headers=AUTH,
               json={'expected_version': record['version'], 'idempotency_key': 'story-repair', 'work_performed': work})
    story.beat('repair_recorded', work_performed=work)
    story.watch(incident, 'awaiting_closure')
    record = story.call('get', f'/incidents/{incident}/workflow')
    verification = record['verification']
    story.beat('verification_ready', recommendation=verification['recommendation']['content']['recommendation'],
               summary=verification['recommendation']['content']['summary'], recovery_check=verification['recovery_check']['status'],
               consistency=verification['consistency'], source_origin=verification['recommendation']['source_origin'])

    # 7. Only the engineer closes.
    record = story.call('post', f'/incidents/{incident}/close', headers=AUTH, json={
        'expected_version': record['version'], 'idempotency_key': 'story-close', 'decision': 'close',
        'rationale': 'Scripted demo closure in a disposable schema'})
    leak_run = story.watch(incident, 'closed')
    audit = story.call('get', f'/incidents/{incident}/audit')
    story.beat('closed', state=record['state'], audit_events=len(audit['events']), hash_chain_valid=audit['hash_chain_valid'],
               graph_memory=record.get('graph_memory'))
    memory = story.call('get', f"/assets/{record['snapshot']['candidate_asset_id']}/incident-memory")
    story.beat('incident_memory', asset_id=memory['asset_id'], incidents=[i['incident']['id'] for i in memory['incidents']])

    # 8. Contrast: same footage, weather station down → no rate, the plan says so.
    alert = story.monitor_until_alert('wind_outage')
    contrast = story.call('post', '/monitor/incidents', json={'profile': 'wind_outage', 'plan_mode': mode})['incident_id']
    story.beat('wind_outage_registered', incident_id=contrast, alert=alert)
    wind_run = story.watch(contrast, 'awaiting_human_review')
    wind = story.call('get', f'/incidents/{contrast}/workflow')
    story.beat('wind_outage_pending_review', rate=wind['snapshot']['quantification']['status'],
               reason=wind['snapshot']['quantification']['reason'],
               recommended_checks=[c['statement'] for c in wind['work_order']['plan']['evidence_backed_draft']['content']['recommended_checks']])
    final = story.call('get', f'/incidents/{incident}/workflow')
    return dict(beats=story.beats, incidents={incident: dict(trace=leak_run, record=final),
                                              contrast: dict(trace=wind_run, record=wind)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--deterministic', action='store_true', help='Labeled templates instead of Nemotron')
    parser.add_argument('--save', action='store_true', help='Write data/story/recorded_run.json for GET /story/recorded-run')
    args = parser.parse_args()
    mode = 'deterministic' if args.deterministic else 'model'
    store = WorkflowStore(os.environ['WORKFLOW_ADMIN_DATABASE_URL'], schema='atelic_story_' + uuid4().hex)
    store.initialize()
    store.configure_engineer(TOKEN, 'scripted-demo-reviewer')
    routes.store = lambda: store
    try:
        with TestClient(app) as client:
            result = record(client, mode)
    finally:
        with store.connect() as conn:
            conn.execute(f'DROP SCHEMA "{store.schema}" CASCADE')
        # Rehearsal closures write incident memory under this disposable schema; remove it.
        from app.graph import Graph
        graph = Graph()
        try:
            graph.forget_workflow(store.schema)
        finally:
            graph.close()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    recording = dict(recorded_at=stamp, mode=mode, model=os.getenv('LLM_MODEL') if mode == 'model' else None,
                     label='Recorded run, not live: scripted reviewer actions in a disposable schema; synthetic facility and telemetry; '
                           'recorded GasVid footage from a different physical scene',
                     **result)
    out = ROOT / 'docs/evidence/story'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'story-{stamp}.json').write_text(json.dumps(recording, indent=2))
    print('Evidence:', out / f'story-{stamp}.json')
    if args.save:
        target = ROOT / 'data/story/recorded_run.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(recording, indent=2))
        print('Recorded run:', target)
