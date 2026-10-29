"""Durable advisory workflow. No equipment control or HTTP framework dependency."""
import hashlib
import hmac
import io
import json
import os
import re
from datetime import datetime, timezone
from uuid import uuid4


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class WorkflowError(ValueError):
    def __init__(self, reason, status_code=409):
        self.reason, self.status_code = reason, status_code
        super().__init__(reason)


class AuthenticationError(WorkflowError):
    def __init__(self):
        super().__init__('invalid_engineer_credentials', 401)


class WorkflowStore:
    def __init__(self, database_url=None, schema='atelic'):
        self.database_url = database_url or os.environ['WORKFLOW_DATABASE_URL']
        if not re.fullmatch(r'[a-z][a-z0-9_]*', schema):
            raise ValueError('invalid schema')
        self.schema = schema

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        conn.execute(f'SET search_path TO "{self.schema}", public')
        return conn

    def initialize(self):
        """Run with schema-owner credentials, not the production application role."""
        with self.connect() as c:
            c.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            c.execute('''CREATE TABLE IF NOT EXISTS incidents (
                id text PRIMARY KEY, state text NOT NULL, version integer NOT NULL,
                record jsonb NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (
                incident_id text REFERENCES incidents(id), version integer NOT NULL,
                id text NOT NULL UNIQUE, payload jsonb NOT NULL,
                PRIMARY KEY(incident_id, version));
                CREATE TABLE IF NOT EXISTS reviews (
                id text PRIMARY KEY, incident_id text REFERENCES incidents(id),
                payload jsonb NOT NULL);
                CREATE TABLE IF NOT EXISTS reports (
                incident_id text PRIMARY KEY REFERENCES incidents(id), payload jsonb NOT NULL);
                CREATE TABLE IF NOT EXISTS credentials (
                token_hash text PRIMARY KEY, engineer_id text NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                incident_id text NOT NULL, key text NOT NULL, fingerprint text NOT NULL,
                response jsonb NOT NULL, PRIMARY KEY(incident_id,key));
                CREATE TABLE IF NOT EXISTS draft_jobs (
                incident_id text REFERENCES incidents(id), key text NOT NULL,
                fingerprint text NOT NULL, status text NOT NULL,
                started_at timestamptz NOT NULL DEFAULT now(), result jsonb,
                PRIMARY KEY(incident_id,key));
                CREATE TABLE IF NOT EXISTS audit (
                incident_id text REFERENCES incidents(id), sequence integer NOT NULL,
                previous_hash text NOT NULL, hash text NOT NULL, event_text text NOT NULL,
                PRIMARY KEY(incident_id,sequence));
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                incident_id text PRIMARY KEY, status text NOT NULL, current_step text,
                options jsonb NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(), result jsonb);
                CREATE TABLE IF NOT EXISTS incident_artifacts (
                id text PRIMARY KEY, incident_id text REFERENCES incidents(id), kind text NOT NULL,
                payload jsonb NOT NULL);
                CREATE TABLE IF NOT EXISTS orchestration_events (
                incident_id text NOT NULL, sequence integer NOT NULL,
                recorded_at timestamptz NOT NULL DEFAULT now(), event jsonb NOT NULL,
                PRIMARY KEY(incident_id,sequence));
                CREATE OR REPLACE FUNCTION deny_immutable_mutation() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                  RAISE EXCEPTION 'immutable workflow record'; END $$;
                ''')
            for table in ('audit', 'drafts', 'reviews', 'reports', 'incident_artifacts', 'orchestration_events'):
                c.execute(f'DROP TRIGGER IF EXISTS immutable_record ON {table}')
                c.execute(f'''CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE OR TRUNCATE
                    ON {table} FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_mutation()''')

    # Orchestration runs are mutable operational state; their event trace is append-only.
    STALE_RUN_SECONDS = 900

    def claim_orchestration(self, incident_id, options):
        """Start or resume a run; returns (run, claimed). Only one active run per incident."""
        from psycopg.types.json import Jsonb
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.schema + ':orchestration:' + incident_id,))
            run = c.execute('SELECT * FROM orchestration_runs WHERE incident_id=%s', (incident_id,)).fetchone()
            if run is None:
                run = c.execute('INSERT INTO orchestration_runs (incident_id,status,options) VALUES (%s,%s,%s) RETURNING *',
                                (incident_id, 'running', Jsonb(options))).fetchone()
                return run, True
            stale = c.execute('SELECT now()-updated_at > make_interval(secs=>%s) AS stale FROM orchestration_runs WHERE incident_id=%s',
                              (self.STALE_RUN_SECONDS, incident_id)).fetchone()['stale']
            if run['status'] in ('failed', 'interrupted') or (run['status'] == 'running' and stale):
                run = c.execute("UPDATE orchestration_runs SET status='running', options=%s, updated_at=now(), result=NULL WHERE incident_id=%s RETURNING *",
                                (Jsonb(options), incident_id)).fetchone()
                return run, True
            return run, False

    def claim_follow_up(self, incident_id, resumable):
        """Wake an idle run for new human input; False if absent, busy or not in a resumable status."""
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.schema + ':orchestration:' + incident_id,))
            run = c.execute('SELECT status FROM orchestration_runs WHERE incident_id=%s', (incident_id,)).fetchone()
            if run is None or run['status'] not in resumable:
                return False
            c.execute("UPDATE orchestration_runs SET status='running', updated_at=now() WHERE incident_id=%s", (incident_id,))
            return True

    def release_orchestration(self, incident_id, settle):
        """Finish a run unless new work arrived; settle(record) returns (status, step, result) or None for more work.

        Runs under the same lock as claim_follow_up, so input that arrives while the worker is
        busy is either seen here or wakes a new run, never lost.
        """
        from psycopg.types.json import Jsonb
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.schema + ':orchestration:' + incident_id,))
            row = c.execute('SELECT record FROM incidents WHERE id=%s', (incident_id,)).fetchone()
            outcome = settle(row['record'] if row else None)
            if outcome is None:
                return False
            status, step, result = outcome
            c.execute('UPDATE orchestration_runs SET status=%s, current_step=%s, result=%s, updated_at=now() WHERE incident_id=%s',
                      (status, step, Jsonb(result) if result is not None else None, incident_id))
            return True

    def update_orchestration(self, incident_id, status, current_step=None, result=None):
        from psycopg.types.json import Jsonb
        with self.connect() as c:
            c.execute('UPDATE orchestration_runs SET status=%s, current_step=%s, result=%s, updated_at=now() WHERE incident_id=%s',
                      (status, current_step, Jsonb(result) if result is not None else None, incident_id))

    def orchestration_event(self, incident_id, event):
        from psycopg.types.json import Jsonb
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.schema + ':orchestration-events:' + incident_id,))
            row = c.execute('SELECT coalesce(max(sequence),0)+1 AS next FROM orchestration_events WHERE incident_id=%s', (incident_id,)).fetchone()
            c.execute('INSERT INTO orchestration_events (incident_id,sequence,event) VALUES (%s,%s,%s)',
                      (incident_id, row['next'], Jsonb(json.loads(canonical(event)))))
            c.execute('UPDATE orchestration_runs SET updated_at=now() WHERE incident_id=%s', (incident_id,))
            return row['next']

    def get_orchestration(self, incident_id):
        with self.connect() as c:
            run = c.execute('SELECT *, now()-updated_at > make_interval(secs=>%s) AS stale FROM orchestration_runs WHERE incident_id=%s',
                            (self.STALE_RUN_SECONDS, incident_id)).fetchone()
            if run is None:
                raise WorkflowError('orchestration_not_started', 404)
            events = c.execute('SELECT sequence, recorded_at, event FROM orchestration_events WHERE incident_id=%s ORDER BY sequence', (incident_id,)).fetchall()
        if run.pop('stale') and run['status'] == 'running':
            run['status'] = 'interrupted'
        return dict(run, events=[dict(sequence=e['sequence'], recorded_at=e['recorded_at'].isoformat(), **e['event']) for e in events],
                    started_at=run['started_at'].isoformat(), updated_at=run['updated_at'].isoformat())

    def configure_engineer(self, token=None, engineer_id=None):
        token = token or os.environ.get('ENGINEER_TOKEN')
        engineer_id = engineer_id or os.environ.get('ENGINEER_ID')
        if not token or len(token) < 24 or not engineer_id:
            raise ValueError('ENGINEER_TOKEN (24+ characters) and ENGINEER_ID required')
        with self.connect() as c:
            c.execute('DELETE FROM credentials')
            c.execute('INSERT INTO credentials VALUES (%s,%s)',
                      (hashlib.sha256(token.encode()).hexdigest(), engineer_id))

    def _authenticate(self, c, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise AuthenticationError()
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        row = c.execute('SELECT * FROM credentials WHERE token_hash=%s', (digest,)).fetchone()
        if not row or not hmac.compare_digest(digest, row['token_hash']):
            raise AuthenticationError()
        return row['engineer_id']

    def _audit(self, c, incident_id, action, actor, payload):
        row = c.execute('SELECT sequence, hash FROM audit WHERE incident_id=%s ORDER BY sequence DESC LIMIT 1', (incident_id,)).fetchone()
        previous = row['hash'] if row else '0' * 64
        seq = row['sequence'] + 1 if row else 1
        event = canonical(dict(incident_id=incident_id, sequence=seq, action=action,
                               actor=actor, payload=payload,
                               timestamp=datetime.now(timezone.utc).isoformat()))
        digest = hashlib.sha256((previous + event).encode()).hexdigest()
        c.execute('INSERT INTO audit VALUES (%s,%s,%s,%s,%s)', (incident_id, seq, previous, digest, event))

    def create_incident(self, incident_id, snapshot, evidence_references, *, idempotency_key):
        return self._mutate(incident_id, 0, idempotency_key, 'create',
                            dict(snapshot=snapshot, evidence_references=evidence_references))

    def transition(self, incident_id, to_state, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'transition', dict(to_state=to_state))

    def save_draft(self, incident_id, plan, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'draft', dict(plan=plan))

    def submit_review(self, incident_id, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'submit', {})

    def review(self, incident_id, decision, rationale, draft_version, *, authorization,
               expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'review',
                            dict(decision=decision, rationale=rationale, draft_version=draft_version), authorization)

    def create_report(self, incident_id, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'report', {})

    def save_investigation(self, incident_id, findings, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'investigate', dict(findings=findings))

    def ask_question(self, incident_id, question, *, authorization, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'question', dict(question=question), authorization)

    def answer_question(self, incident_id, question_id, answer, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'answer', dict(question_id=question_id, answer=answer))

    def record_repair(self, incident_id, work_performed, *, authorization, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'repair', dict(work_performed=work_performed), authorization)

    def save_verification(self, incident_id, verification, *, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'verify', dict(verification=verification))

    def close(self, incident_id, decision, rationale, *, authorization, expected_version, idempotency_key):
        return self._mutate(incident_id, expected_version, idempotency_key, 'close', dict(decision=decision, rationale=rationale), authorization)

    # Actions only an authenticated human may take; agents and the orchestrator act as 'system'.
    HUMAN_ACTIONS = ('review', 'question', 'repair', 'close')
    QUESTION_STATES = ('pending_review', 'verification_ready')
    REPAIR_STATES = ('approved', 'reported', 'follow_up_required')

    def _artifact(self, c, incident_id, kind, payload):
        from psycopg.types.json import Jsonb
        artifact = dict(id=str(uuid4()), kind=kind, recorded_at=datetime.now(timezone.utc).isoformat(), **payload)
        c.execute('INSERT INTO incident_artifacts VALUES (%s,%s,%s,%s)', (artifact['id'], incident_id, kind, Jsonb(artifact)))
        return artifact

    def _mutate(self, incident_id, expected_version, key, action, payload, authorization=None):
        from psycopg.types.json import Jsonb
        if not isinstance(key, str) or not key.strip():
            raise WorkflowError('idempotency_key_required', 422)
        fingerprint = hashlib.sha256(canonical(dict(action=action, payload=payload, expected_version=expected_version)).encode()).hexdigest()
        with self.connect() as c:
            # Serializes creation as well as all existing-incident mutations.
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.schema + ':' + incident_id,))
            actor = self._authenticate(c, authorization) if action in self.HUMAN_ACTIONS else 'system'
            if action in self.HUMAN_ACTIONS:
                fingerprint = hashlib.sha256((fingerprint + actor).encode()).hexdigest()
            old = c.execute('SELECT * FROM requests WHERE incident_id=%s AND key=%s', (incident_id,key)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise WorkflowError('idempotency_key_reused')
                return old['response']
            row = c.execute('SELECT * FROM incidents WHERE id=%s FOR UPDATE', (incident_id,)).fetchone()
            if action == 'create':
                if row:
                    raise WorkflowError('incident_already_exists')
                record = dict(id=incident_id, state='observed', version=1, **payload)
                canonical(record)
                c.execute('INSERT INTO incidents VALUES (%s,%s,%s,%s)', (incident_id,'observed',1,Jsonb(record)))
            else:
                if not row:
                    raise WorkflowError('incident_not_found',404)
                if row['version'] != expected_version:
                    raise WorkflowError('version_conflict')
                record = row['record']
                state = row['state']
                if action == 'transition':
                    allowed = {'observed':'localized_unconfirmed', 'localized_unconfirmed':'quantified_or_unavailable'}
                    if allowed.get(state) != payload['to_state']:
                        raise WorkflowError('invalid_transition')
                    record['state'] = payload['to_state']
                elif action == 'draft':
                    if state not in ('quantified_or_unavailable','plan_drafted'):
                        raise WorkflowError('draft_not_allowed')
                    plan = payload['plan']
                    validate_plan(plan)
                    version = record.get('draft_version',0) + 1
                    draft = dict(id=str(uuid4()), version=version, plan=plan,
                                 frozen_snapshot=record['snapshot'], evidence_references=record['evidence_references'],
                                 advisory_only=True, equipment_control=False)
                    c.execute('INSERT INTO drafts VALUES (%s,%s,%s,%s)', (incident_id,version,draft['id'],Jsonb(draft)))
                    record.update(state='plan_drafted', draft_version=version, work_order=draft)
                    record.pop('review', None)
                    record.pop('revision_required_after',None)
                elif action == 'submit':
                    if state != 'plan_drafted':
                        raise WorkflowError('submission_not_allowed')
                    if record.get('revision_required_after',0) >= record['draft_version']:
                        raise WorkflowError('revised_draft_required')
                    record['state'] = 'pending_review'
                elif action == 'review':
                    if state != 'pending_review':
                        raise WorkflowError('review_not_pending')
                    if payload['draft_version'] != record['draft_version']:
                        raise WorkflowError('draft_version_conflict')
                    if payload['decision'] not in ('approve','reject','request_changes') or not payload['rationale'].strip():
                        raise WorkflowError('invalid_review',422)
                    review = dict(id=str(uuid4()), engineer_id=actor, actor_type='human', **payload)
                    c.execute('INSERT INTO reviews VALUES (%s,%s,%s)', (review['id'],incident_id,Jsonb(review)))
                    record.update(state={'approve':'approved','reject':'rejected','request_changes':'plan_drafted'}[payload['decision']], review=review)
                    if payload['decision']=='request_changes':
                        record['revision_required_after']=record['draft_version']
                elif action == 'report':
                    if state != 'approved' or record.get('review',{}).get('decision') != 'approve':
                        raise WorkflowError('approved_report_required')
                    report = dict(schema_version='1.0', incident_id=incident_id, status='approved',
                                  work_order=record['work_order'], review=record['review'],
                                  investigation=record.get('investigation'), questions=record.get('questions', []),
                                  issued_at=datetime.now(timezone.utc).isoformat(), advisory_only=True)
                    c.execute('INSERT INTO reports VALUES (%s,%s)', (incident_id,Jsonb(report)))
                    record.update(state='reported', report=report)
                elif action == 'investigate':
                    if state not in ('quantified_or_unavailable','plan_drafted'):
                        raise WorkflowError('investigation_not_allowed')
                    record['investigation'] = self._artifact(c, incident_id, 'investigation', dict(findings=payload['findings']))
                elif action == 'question':
                    if state not in self.QUESTION_STATES:
                        raise WorkflowError('question_not_allowed')
                    if not payload['question'].strip():
                        raise WorkflowError('question_required',422)
                    question = self._artifact(c, incident_id, 'question', dict(question=payload['question'], asked_by=actor,
                                                                              asked_in_state=state, actor_type='human'))
                    record.setdefault('questions',[]).append(dict(question, status='pending', answer=None))
                elif action == 'answer':
                    pending = next((q for q in record.get('questions',[]) if q['id']==payload['question_id']), None)
                    if pending is None or pending['status'] != 'pending':
                        raise WorkflowError('question_not_pending')
                    pending.update(status='answered', answer=self._artifact(c, incident_id, 'answer', dict(
                        question_id=payload['question_id'], answer=payload['answer'])))
                elif action == 'repair':
                    if state not in self.REPAIR_STATES or record.get('review',{}).get('decision') != 'approve':
                        raise WorkflowError('approved_plan_required_for_repair_record')
                    if not payload['work_performed'].strip():
                        raise WorkflowError('work_performed_required',422)
                    # A human's account of field work; the system itself performs no physical action.
                    repair = self._artifact(c, incident_id, 'repair', dict(work_performed=payload['work_performed'], recorded_by=actor,
                                                                          actor_type='human', physical_action_by_system=False))
                    record.update(state='repair_recorded', repair=repair)
                    record.pop('verification', None)
                elif action == 'verify':
                    if state != 'repair_recorded':
                        raise WorkflowError('verification_not_allowed')
                    record.update(state='verification_ready', verification=self._artifact(c, incident_id, 'verification', dict(
                        repair_id=record['repair']['id'], **payload['verification'])))
                elif action == 'close':
                    if state != 'verification_ready':
                        raise WorkflowError('closure_not_allowed')
                    if payload['decision'] not in ('close','keep_open') or not payload['rationale'].strip():
                        raise WorkflowError('invalid_closure',422)
                    closure = self._artifact(c, incident_id, 'closure', dict(engineer_id=actor, actor_type='human', **payload))
                    record.update(state='closed' if payload['decision']=='close' else 'follow_up_required', closure=closure)
                record['version'] += 1
                c.execute('UPDATE incidents SET state=%s,version=%s,record=%s WHERE id=%s',
                          (record['state'],record['version'],Jsonb(record),incident_id))
            self._audit(c, incident_id, action, actor, dict(request=payload, result=record))
            c.execute('INSERT INTO requests VALUES (%s,%s,%s,%s)', (incident_id,key,fingerprint,Jsonb(record)))
            return record

    def reserve_draft_job(self, incident_id, key, request, expected_version, states=('quantified_or_unavailable','plan_drafted')):
        """Reserve one model attempt atomically; retries never silently incur a second call."""
        fingerprint=hashlib.sha256(canonical(request).encode()).hexdigest()
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(self.schema+':'+incident_id,))
            old=c.execute('SELECT *, now()-started_at > interval \'15 minutes\' AS expired FROM draft_jobs WHERE incident_id=%s AND key=%s FOR UPDATE',(incident_id,key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:
                    raise WorkflowError('idempotency_key_reused')
                if old['status']=='running' and not old['expired']:
                    return False
                if old['status']=='completed':
                    return False
                raise WorkflowError('model_attempt_finished_or_interrupted; retry with a new request key',503)
            row=c.execute('SELECT state,version FROM incidents WHERE id=%s FOR UPDATE',(incident_id,)).fetchone()
            if not row:raise WorkflowError('incident_not_found',404)
            if row['version']!=expected_version:raise WorkflowError('version_conflict')
            if states is not None and row['state'] not in states:raise WorkflowError('draft_not_allowed')
            c.execute('INSERT INTO draft_jobs (incident_id,key,fingerprint,status) VALUES (%s,%s,%s,\'running\')',(incident_id,key,fingerprint))
            return True

    def finish_draft_job(self,incident_id,key,status,result):
        from psycopg.types.json import Jsonb
        with self.connect() as c:
            c.execute('UPDATE draft_jobs SET status=%s,result=%s WHERE incident_id=%s AND key=%s',(status,Jsonb(result),incident_id,key))

    def get_incident(self, incident_id):
        with self.connect() as c:
            row = c.execute('SELECT record FROM incidents WHERE id=%s',(incident_id,)).fetchone()
            if not row:
                raise WorkflowError('incident_not_found',404)
            return row['record']

    def get_audit(self, incident_id):
        with self.connect() as c:
            return c.execute('SELECT * FROM audit WHERE incident_id=%s ORDER BY sequence',(incident_id,)).fetchall()

    def verify_audit(self, incident_id):
        previous = '0' * 64
        for seq, row in enumerate(self.get_audit(incident_id),1):
            if row['sequence'] != seq or row['previous_hash'] != previous:
                return False
            previous = hashlib.sha256((previous + row['event_text']).encode()).hexdigest()
            if previous != row['hash']:
                return False
        return True


def validate_plan(plan):
    if not isinstance(plan, dict) or not plan.get('text'):
        raise WorkflowError('plan_text_required',422)
    origin = plan.get('source_origin')
    run = plan.get('model_run')
    if origin == 'deterministic':
        if plan.get('generation_method') != 'deterministic_template' or run is not None:
            raise WorkflowError('deterministic_provenance_required',422)
    elif origin == 'llm':
        if not isinstance(run,dict) or any(not run.get(k) for k in ('provider','model','run_id','generated_at')):
            raise WorkflowError('model_run_provenance_required',422)
    else:
        raise WorkflowError('source_origin_required',422)


def deterministic_plan(text):
    return dict(text=text, source_origin='deterministic', generation_method='deterministic_template')


def report_pdf(report):
    """Render an already-issued structured report; does not approve or mutate state."""
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from xml.sax.saxutils import escape
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    flow = [Paragraph('Methane incident report',styles['Title']), Spacer(1,12)]
    order=report.get('work_order',{})
    snapshot=order.get('frozen_snapshot',{})
    calculation=snapshot.get('quantification',{})
    review=report.get('review',{})
    sections={
        'Demonstration provenance': 'Synthetic facility and telemetry. Recorded GasVid footage is a separate physical scene; gas annotations remain pending. This advisory report does not certify emissions or authorize plant control.',
        'Incident': f"{report.get('incident_id','unknown')} | issued {report.get('issued_at','unknown')} | candidate {snapshot.get('candidate_asset_id','unavailable')} (unconfirmed)",
        'Replay clock': json.dumps(snapshot.get('clock',{})),
        'Emission estimate': json.dumps({k:calculation.get(k) for k in ('status','reason','rate_kg_h','unit','method','version','sensitivity_kg_h','independent_validation')}),
        'Sensor fit comparison': json.dumps({k:(calculation.get('telemetry_attribution') or {}).get(k) for k in ('status','best_fit_asset_id','camera_candidates','note')}),
        'Calculation assumptions': '\n'.join(calculation.get('assumptions',[])),
        'Investigation findings': '\n'.join(f"[{f['kind']}] {f['statement']}" for f in
                                             (((report.get('investigation') or {}).get('findings') or {}).get('content') or {}).get('findings', [])) or 'No separate investigation step recorded',
        'Engineer questions': '\n'.join(f"Q: {q['question']} | A: {((q.get('answer') or {}).get('answer') or {}).get('content',{}).get('answer','unanswered')}"
                                         for q in report.get('questions', [])) or 'None asked',
        'Proposed work order': order.get('plan',{}).get('text','unavailable'),
        'Draft generation': order.get('plan',{}).get('source_origin','unavailable'),
        'Human review': json.dumps(review),
        'Evidence integrity': 'Frozen snapshot SHA-256: '+hashlib.sha256(canonical(snapshot).encode()).hexdigest()+'. Full observation values, candidate fits and references are retained in the companion JSON report.',
    }
    for title,text in sections.items():
        flow.append(Paragraph(escape(title),styles['Heading2']))
        # Split large structured text into readable paragraphs; never execute markup.
        for line in str(text).splitlines() or ['unavailable']:
            flow.append(Paragraph(escape(line),styles['BodyText']))
        flow.append(Spacer(1,8))
    readings=snapshot.get('measurements',[])
    if readings:
        flow.append(Paragraph('Measurement window summary',styles['Heading2']))
        for sid in sorted({r['sensor_id'] for r in readings}):
            rows=[r for r in readings if r['sensor_id']==sid]
            good=[r['value'] for r in rows if r.get('quality_flag')=='good' and r.get('value') is not None]
            summary=f"{sid}: {len(rows)} observations; {len(good)} good; "
            summary+=f"min {min(good):.5g}, max {max(good):.5g} {rows[0]['unit']}" if good else 'no valid values'
            flow.append(Paragraph(escape(summary),styles['BodyText']))
    flow.append(Paragraph('Evidence references (full list in JSON)',styles['Heading2']))
    for ref in order.get('evidence_references',[])[:15]:
        flow.append(Paragraph(escape(ref.get('uri',ref.get('id','unknown'))),styles['BodyText']))
    SimpleDocTemplate(stream).build(flow)
    return stream.getvalue()
