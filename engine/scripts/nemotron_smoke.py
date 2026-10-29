"""Live Quantify→Plan→Approve→Report run with genuine hosted Nemotron inference.

Uses the real Neo4j/InfluxDB/Qdrant services and a disposable PostgreSQL schema with a
named smoke-test reviewer, so primary incidents and their audit chains are never touched.
Makes paid/credited hosted model calls; never run from the unit test suite.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
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

INCIDENT = 'SMOKE-NEMOTRON-001'
TOKEN = 'smoke-human-token-' + uuid4().hex


def expect(response, status=200):
    if response.status_code != status:
        raise SystemExit(f'{response.request.method} {response.request.url.path} -> {response.status_code}: {response.text[:2000]}')
    return response


if __name__ == '__main__':
    if not os.getenv('LLM_MODEL') or not (os.getenv('NVIDIA_API_KEY') or os.getenv('LLM_API_KEY')):
        raise SystemExit('Configure NVIDIA_API_KEY and LLM_MODEL in .env first')
    store = WorkflowStore(os.environ['WORKFLOW_ADMIN_DATABASE_URL'], schema='atelic_smoke_' + uuid4().hex)
    store.initialize()
    store.configure_engineer(TOKEN, 'smoke-test-reviewer')
    routes.store = lambda: store
    out = ROOT / 'docs/evidence/nemotron'
    out.mkdir(parents=True, exist_ok=True)
    try:
        with TestClient(app) as client:
            # Ephemeral copy of the primary synthetic scenario under a smoke-only incident ID.
            app.state.scenarios.append({**app.state.scenarios[0], 'incident_id': INCIDENT})
            event = next(iter(app.state.events.values())).model_copy(update={'incident_id': INCIDENT, 'event_id': 'SMOKE-EVENT'})
            app.state.events[event.event_id] = event
            auth = {'Authorization': 'Bearer ' + TOKEN}

            quantified = expect(client.post(f'/incidents/{INCIDENT}/quantify', json={})).json()
            record = expect(client.post(f'/incidents/{INCIDENT}/workflow/open', json={'idempotency_key': 'open'})).json()
            record = expect(client.post(f'/incidents/{INCIDENT}/draft-plan', json={
                'mode': 'model', 'expected_version': record['version'], 'idempotency_key': 'draft'})).json()
            plan = record['work_order']['plan']
            record = expect(client.post(f'/incidents/{INCIDENT}/submit-review', json={
                'expected_version': record['version'], 'idempotency_key': 'submit'})).json()
            record = expect(client.post(f'/incidents/{INCIDENT}/review', headers=auth, json={
                'expected_version': record['version'], 'idempotency_key': 'review', 'draft_version': record['draft_version'],
                'decision': 'approve', 'rationale': 'Smoke test only; not a real engineering approval'})).json()
            expect(client.post(f'/incidents/{INCIDENT}/report', headers=auth, json={
                'expected_version': record['version'], 'idempotency_key': 'report'}))
            report = expect(client.get(f'/incidents/{INCIDENT}/report')).json()
            pdf = expect(client.get(f'/incidents/{INCIDENT}/report?format=pdf')).content
            audit = expect(client.get(f'/incidents/{INCIDENT}/audit')).json()
    finally:
        with store.connect() as conn:
            conn.execute(f'DROP SCHEMA "{store.schema}" CASCADE')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (out / f'report-{stamp}.pdf').write_bytes(pdf)
    evidence = dict(
        generated_at=stamp, incident_id=INCIDENT, scope='disposable schema; smoke-test reviewer; synthetic scenario copy',
        quantification={k: quantified.get(k) for k in ('status', 'method', 'rate_kg_h', 'sensitivity_kg_h', 'reason', 'historian')},
        model_run=plan['model_run'], source_origin=plan['source_origin'],
        deployment=plan['evidence_backed_draft']['deployment'], activity=plan['evidence_backed_draft']['activity'],
        draft=plan['evidence_backed_draft']['content'], report_state=report.get('state', 'reported'),
        audit_events=len(audit['events']), hash_chain_valid=audit['hash_chain_valid'],
        report_pdf_sha256=hashlib.sha256(pdf).hexdigest())
    path = out / f'smoke-{stamp}.json'
    path.write_text(json.dumps(evidence, indent=2))
    print(json.dumps({k: evidence[k] for k in ('quantification', 'model_run', 'deployment', 'audit_events', 'hash_chain_valid')}, indent=2))
    print('model turns/tools:', [a['tool'] for a in evidence['activity']])
    print('Evidence:', path)
