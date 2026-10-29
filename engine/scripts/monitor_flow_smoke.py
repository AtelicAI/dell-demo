"""Live Monitor→Detect→Locate→Quantify→Plan→Approve→Report run through the real HTTP routes.

Monitoring replays executed model predictions on recorded footage second by second. After the
alert, the scenario clock advances until the physics returns a steady-plume estimate. Uses the real
Neo4j/InfluxDB/Qdrant services, genuine hosted Nemotron inference (unless --deterministic) and a
disposable PostgreSQL schema with a smoke-test reviewer; primary incidents are never touched.
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

TOKEN = 'smoke-human-token-' + uuid4().hex


def expect(response, status=200):
    if response.status_code != status:
        raise SystemExit(f'{response.request.method} {response.request.url.path} -> {response.status_code}: {response.text[:2000]}')
    return response.json()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--deterministic', action='store_true', help='Use the labeled template instead of Nemotron')
    args = parser.parse_args()
    mode = 'deterministic' if args.deterministic else 'model'
    store = WorkflowStore(os.environ['WORKFLOW_ADMIN_DATABASE_URL'], schema='atelic_smoke_' + uuid4().hex)
    store.initialize()
    store.configure_engineer(TOKEN, 'smoke-test-reviewer')
    routes.store = lambda: store
    auth = {'Authorization': 'Bearer ' + TOKEN}
    timeline = []
    try:
        with TestClient(app) as client:
            cfg = expect(client.get('/monitor'))
            state = None
            for second in range(cfg['clip_start_seconds'], cfg['clip_end_seconds'] + 1):
                status = expect(client.get('/monitor/status', params={'clip_seconds': second}))
                if status['state'] != state:
                    state = status['state']
                    timeline.append(dict(stage='monitor', clip_seconds=second, scenario_seconds=status['scenario_seconds'], state=state))
                    print(f'clip {second:>3}s  scenario {status["scenario_seconds"]:>4}s  {state}')
                if state == 'alert':
                    break
            else:
                raise SystemExit('No monitoring alert in the replayed interval')
            alert = status['alert']
            registered = expect(client.post('/monitor/incidents', json={'plan_mode': mode}))
            incident = registered['incident_id']
            timeline.append(dict(stage='detect', incident_id=incident, **alert))
            print('ALERT ->', incident, 'at scenario', alert['scenario_seconds'], 's | orchestrator started')
            # The server-side orchestrator drives Locate→Quantify→Freeze→Plan→Submit; we only watch.
            shown = 0
            while True:
                run = expect(client.get(f'/incidents/{incident}/orchestration'))
                for event in run['events'][shown:]:
                    d = event['detail']
                    note = {'attempt': f"t={d.get('as_of_seconds')}s {d.get('status')} {d.get('rate_kg_h') and round(d['rate_kg_h'], 2) or ''} {d.get('reason') or ''}",
                            'agent_activity': f"{d.get('tool')} {d.get('status')} {round(d.get('elapsed_ms', 0)/1000, 1)}s",
                            'tool_call': f"{d.get('tool')} {d.get('backend')}",
                            'completed': f"{round(d.get('elapsed_ms', 0)/1000, 1)}s",
                            'failed': d.get('detail'), 'attempt_failed': d.get('detail')}.get(event['kind'], '')
                    print(f"  [{event['sequence']:>2}] {event['step']:<8} {event['kind']:<15} {note}")
                shown = len(run['events'])
                if run['status'] != 'running':
                    break
                time.sleep(1)
            timeline.append(dict(stage='orchestration', status=run['status'], result=run['result'], events=run['events']))
            if run['status'] != 'awaiting_human_review':
                raise SystemExit('Orchestration stopped: ' + json.dumps(run['result']))
            record = expect(client.get(f'/incidents/{incident}/workflow'))
            plan = record['work_order']['plan']
            print('PLAN', plan['source_origin'], (plan.get('model_run') or {}).get('model'), '| state', record['state'])
            record = expect(client.post(f'/incidents/{incident}/review', headers=auth, json={
                'expected_version': record['version'], 'idempotency_key': 'review', 'draft_version': record['draft_version'],
                'decision': 'approve', 'rationale': 'Smoke test only; not a real engineering approval'}))
            print('APPROVE', record['state'], record['review']['engineer_id'])
            expect(client.post(f'/incidents/{incident}/report', headers=auth, json={'expected_version': record['version'], 'idempotency_key': 'report'}))
            pdf = client.get(f'/incidents/{incident}/report?format=pdf').content
            audit = expect(client.get(f'/incidents/{incident}/audit'))
            print('REPORT issued; audit events', len(audit['events']), 'hash chain valid', audit['hash_chain_valid'])
    finally:
        with store.connect() as conn:
            conn.execute(f'DROP SCHEMA "{store.schema}" CASCADE')

    out = ROOT / 'docs/evidence/monitor-flow'
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (out / f'report-{stamp}.pdf').write_bytes(pdf)
    evidence = dict(generated_at=stamp, incident_id=incident, scope='disposable schema; smoke-test reviewer', timeline=timeline,
                    plan_origin=plan['source_origin'], model_run=plan.get('model_run'),
                    activity=plan['evidence_backed_draft']['activity'], draft=plan['evidence_backed_draft']['content'],
                    audit_events=len(audit['events']), hash_chain_valid=audit['hash_chain_valid'],
                    report_pdf_sha256=hashlib.sha256(pdf).hexdigest())
    path = out / f'flow-{stamp}.json'
    path.write_text(json.dumps(evidence, indent=2))
    print('Evidence:', path)
