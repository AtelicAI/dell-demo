import os
from neo4j import GraphDatabase

class Graph:
    def __init__(self):
        self.driver = GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://127.0.0.1:7690"), auth=(os.getenv("NEO4J_USER","neo4j"), os.environ["NEO4J_PASSWORD"]), connection_timeout=3, max_transaction_retry_time=3,
                                           notifications_disabled_categories=["UNRECOGNIZED"])  # incident memory may not exist yet
    def close(self): self.driver.close()
    def query(self, query, **params):
        records, summary, _ = self.driver.execute_query(query, parameters_=params, database_="neo4j")
        return [r.data() for r in records]
    LABELS=('Asset','Sensor','Camera','WorkOrder','Document','WeatherStation','Scenario','Incident','DetectionEvent',
            'FieldReport','Commitment','LoadChange','SensorEpisode')
    WORK_ORDER_RELATIONS=('FOLLOWS_UP','REVIEWS','REPEATS_SURVEY_OF','SAME_EQUIPMENT_AS')
    def ingest(self, facility, maintenance, documents=(), scenarios=(), events=(), field_reports=None, operations=None, episodes=None):
        """Atomically replace this facility's derived projection, never workflow records.

        Scenarios/events are public replay metadata only; private truth is never ingested. Field reports,
        the operating log and history episodes default to the canonical data files. Incident memory
        (IncidentRecord, written at closure) is not part of the projection and is re-linked, not deleted.
        """
        from .data import load_field_reports, load_json
        field_reports=load_field_reports() if field_reports is None else field_reports
        operations=load_json('operations/load_changes.json') if operations is None else operations
        episodes=load_json('generated/history_episodes.json')['episodes'] if episodes is None else episodes
        work_orders={w['id'] for w in maintenance}
        for w in maintenance:
            for link in w.get('related_work_orders',[]):
                if link['relation'] not in self.WORK_ORDER_RELATIONS or link['work_order_id'] not in work_orders:
                    raise ValueError('unknown work order relation or target')
        flat=[{k:v for k,v in w.items() if k not in ('related_work_orders','commitments')} for w in maintenance]
        changes=sorted(operations['events'],key=lambda c:c['time_utc'])
        asset_ids={a.id for a in facility.assets}
        if any(w['asset_id'] not in asset_ids for w in maintenance):
            raise ValueError('maintenance references unknown equipment')
        titles={d['id']:d for d in documents}
        for label in self.LABELS:
            self.query(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
        ids=list(asset_ids)+[s.id for s in facility.sensors]+[c.id for c in facility.cameras]+[w['id'] for w in maintenance]
        def replace(tx):
            # Scope is the canonical synthetic facility projection, not incidents/audit.
            tx.run("MATCH (n) WHERE ("+" OR ".join(f"n:{l}" for l in self.LABELS)+") AND (n.facility_id=$facility OR n.id IN $ids) DETACH DELETE n",facility=facility.id,ids=ids).consume()
            tx.run("UNWIND $rows AS r CREATE (a:Asset {id:r.id, name:r.name, kind:r.kind, area:r.area, pressure_class:r.pressure_class, x_m:r.position[0], y_m:r.position[1], z_m:r.position[2], origin_type:'simulated', facility_id:$facility})",rows=[a.model_dump() for a in facility.assets],facility=facility.id).consume()
            doc_ids=sorted({d for a in facility.assets for d in a.document_ids}|{w['document_id'] for w in maintenance if w.get('document_id')})
            tx.run("UNWIND $rows AS r CREATE (:Document {id:r.id, title:r.title, kind:r.kind, origin_type:'simulated', operational_approval:false, facility_id:$facility})",
                   rows=[dict(id=d,title=titles.get(d,{}).get('title'),kind=titles.get(d,{}).get('kind')) for d in doc_ids],facility=facility.id).consume()
            tx.run("UNWIND $rows AS r MATCH (a:Asset {id:r.id}) UNWIND r.document_ids AS id MATCH (d:Document {id:id}) CREATE (a)-[:HAS_DOCUMENT {provenance:'synthetic_design'}]->(d)",rows=[a.model_dump() for a in facility.assets]).consume()
            tx.run("UNWIND $rows AS r MATCH (a:Asset {id:r.from_id}), (b:Asset {id:r.to_id}) CREATE (a)-[:FLOWS_TO {line_id:r.line_id, provenance:r.verification}]->(b)",rows=[c.model_dump() for c in facility.connections]).consume()
            tx.run("UNWIND $rows AS r MATCH (a:Asset {id:r.asset_id}) CREATE (s:Sensor {id:r.id, variable:r.variable, unit:r.unit, facility_id:$facility}) CREATE (s)-[:MONITORS {provenance:'synthetic_design'}]->(a)",rows=[s.model_dump() for s in facility.sensors],facility=facility.id).consume()
            for camera in facility.cameras:
                tx.run("CREATE (:Camera {id:$id, calibration_status:'illustrative_uncalibrated', fov_degrees:$fov, x_m:$p[0], y_m:$p[1], z_m:$p[2], facility_id:$facility})",id=camera.id,fov=camera.fov_degrees,p=list(camera.position),facility=facility.id).consume()
                tx.run("UNWIND $rows AS r MATCH (c:Camera {id:$camera}), (a:Asset {id:r.asset_id}) CREATE (c)-[:ILLUSTRATES {provenance:r.provenance}]->(a)",camera=camera.id,rows=[r.model_dump() for r in camera.regions]).consume()
            tx.run("UNWIND $rows AS r MATCH (a:Asset {id:r.asset_id}) CREATE (w:WorkOrder) SET w=r, w.facility_id=$facility CREATE (w)-[:ON_ASSET {provenance:'simulated'}]->(a)",rows=flat,facility=facility.id).consume()
            for relation in self.WORK_ORDER_RELATIONS:
                tx.run(f"UNWIND $rows AS r MATCH (a:WorkOrder {{id:r.id}}), (b:WorkOrder {{id:r.target}}) CREATE (a)-[:{relation} {{provenance:'simulated'}}]->(b)",
                       rows=[dict(id=w['id'],target=l['work_order_id']) for w in maintenance for l in w.get('related_work_orders',[]) if l['relation']==relation]).consume()
            tx.run("UNWIND $rows AS r MATCH (w:WorkOrder {id:r.work_order_id}) CREATE (c:Commitment {id:r.id, text:r.text, status:r.status, origin_type:'simulated', facility_id:$facility}) CREATE (w)-[:COMMITTED_TO {provenance:'simulated'}]->(c) "
                   "WITH c, r WHERE r.fulfilled_by IS NOT NULL MATCH (f:WorkOrder {id:r.fulfilled_by}) CREATE (c)-[:FULFILLED_BY {provenance:'simulated'}]->(f)",
                   rows=[{**c,'work_order_id':w['id']} for w in maintenance for c in w.get('commitments',[])],facility=facility.id).consume()
            tx.run("UNWIND $rows AS r CREATE (f:FieldReport {id:r.id, title:r.title, report_type:r.report_type, date:r.date, time_utc:r.time_utc, author_role:r.author_role, body:r.body, origin_type:'simulated', operational_approval:false, facility_id:$facility}) "
                   "WITH f, r OPTIONAL MATCH (w:WorkOrder {id:r.work_order_id}) FOREACH (_ IN CASE WHEN w IS NULL THEN [] ELSE [1] END | CREATE (f)-[:DOCUMENTS {provenance:'simulated'}]->(w)) "
                   "WITH f, r UNWIND r.asset_ids AS aid MATCH (a:Asset {id:aid}) CREATE (f)-[:ON_ASSET {provenance:'simulated'}]->(a)",
                   rows=[{k:v for k,v in r.items() if k!='text'} for r in field_reports],facility=facility.id).consume()
            tx.run("UNWIND $rows AS r MATCH (a:Asset {id:r.asset_id}) CREATE (l:LoadChange {id:r.id, time_utc:r.time_utc, kind:r.kind, load_before_percent:r.load_before_percent, load_after_percent:r.load_after_percent, duration_minutes:r.duration_minutes, origin_type:'simulated', facility_id:$facility}) CREATE (l)-[:ON_ASSET {provenance:'operating_log'}]->(a)",
                   rows=changes,facility=facility.id).consume()
            def preceding(e):
                if e.get('minutes_after_load_change') is None:return None
                return next((c['id'] for c in reversed(changes) if c['time_utc']<=e['start_utc']),None)
            tx.run("UNWIND $rows AS r CREATE (e:SensorEpisode {id:r.episode_id, start_utc:r.start_utc, duration_minutes:r.duration_minutes, max_peak_excess_ppm:r.max_peak_excess_ppm, mean_wind_speed_m_s:r.mean_wind_speed_m_s, mean_wind_from_degrees:r.mean_wind_from_degrees, minutes_after_load_change:r.minutes_after_load_change, origin_type:'derived_from_simulated_history', facility_id:$facility}) "
                   "WITH e, r FOREACH (sid IN r.sensors_above_threshold | MERGE (s:Sensor {id:sid}) CREATE (e)-[:DETECTED_BY {provenance:'history_analysis'}]->(s)) "
                   "WITH e, r UNWIND (CASE WHEN r.consistent_locations = [] THEN [null] ELSE r.consistent_locations END) AS aid OPTIONAL MATCH (a:Asset {id:aid}) FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | CREATE (e)-[:CONSISTENT_WITH {provenance:'history_analysis_5min_indicative'}]->(a)) "
                   "WITH DISTINCT e, r WHERE r.load_change_id IS NOT NULL MATCH (l:LoadChange {id:r.load_change_id}) CREATE (e)-[:AFTER_LOAD_CHANGE {minutes:r.minutes_after_load_change, provenance:'history_analysis'}]->(l)",
                   rows=[{**e,'consistent_locations':e.get('consistent_locations',[]),'load_change_id':preceding(e)} for e in episodes],facility=facility.id).consume()
            # Incident memory survives reseeding: re-link it to the recreated assets.
            tx.run("MATCH (r:IncidentRecord {facility_id:$facility}) UNWIND r.candidate_asset_ids AS aid MATCH (a:Asset {id:aid}) "
                   "MERGE (r)-[:CONSIDERED]->(a) WITH r MATCH (a:Asset {id:r.selected_asset_id}) MERGE (r)-[:CONCERNS]->(a)",facility=facility.id).consume()
            tx.run("MATCH (w:WorkOrder {facility_id:$facility}), (d:Document {id:w.document_id}) CREATE (w)-[:REFERENCES {provenance:'simulated'}]->(d)",facility=facility.id).consume()
            stations=sorted({s.id.rsplit('-',1)[0] for s in facility.sensors if s.variable.startswith('wind_')})
            for station in stations:
                tx.run("CREATE (m:WeatherStation {id:$id, origin_type:'simulated', facility_id:$facility}) WITH m MATCH (s:Sensor) WHERE s.id STARTS WITH $id+'-' CREATE (s)-[:PART_OF {provenance:'synthetic_design'}]->(m)",id=station,facility=facility.id).consume()
            tx.run("UNWIND $rows AS r CREATE (s:Scenario {id:r.id, title:r.title, start_utc:r.start_utc, origin_type:'simulated', facility_id:$facility}) CREATE (i:Incident {id:r.incident_id, origin_type:'simulated', facility_id:$facility}) CREATE (i)-[:REPLAYS {provenance:'simulated'}]->(s)",
                   rows=[dict(id=s['id'],title=s['title'],start_utc=s['start_utc'],incident_id=s['incident_id']) for s in scenarios],facility=facility.id).consume()
            tx.run("UNWIND $rows AS r MATCH (i:Incident {id:r.incident_id}), (c:Camera {id:r.camera_id}) CREATE (e:DetectionEvent {id:r.event_id, mode:r.mode, timestamp_utc:r.timestamp_utc, reference:r.reference, facility_id:$facility}) CREATE (i)-[:TRIGGERED_BY {provenance:r.mode}]->(e) CREATE (e)-[:OBSERVED_BY {provenance:r.mode}]->(c)",
                   rows=[dict(event_id=e['event_id'],incident_id=e['incident_id'],camera_id=e['camera_id'],mode=e['mode'],timestamp_utc=str(e['timestamp_utc']),reference=e['provenance']['reference']) for e in events],facility=facility.id).consume()
        with self.driver.session(database='neo4j') as session:
            session.execute_write(replace)
    def context(self, asset_id):
        rows=self.query("MATCH (a:Asset {id:$id}) OPTIONAL MATCH (a)-[:FLOWS_TO]-(n:Asset) RETURN a{.*} AS asset, collect(DISTINCT n{.*}) AS neighbors", id=asset_id)
        if not rows: return None
        result=rows[0]
        ids=[asset_id]+[a['id'] for a in result['neighbors']]
        result['sensors']=self.query("MATCH (s:Sensor)-[:MONITORS]->(a:Asset) WHERE a.id IN $ids RETURN s{.*, asset_id:a.id} AS sensor", ids=ids)
        result['maintenance']=self.query("MATCH (w:WorkOrder)-[:ON_ASSET]->(a:Asset {id:$id}) RETURN w{.*} AS record ORDER BY w.date DESC", id=asset_id)
        result['camera_paths']=self.query("MATCH (c:Camera)-[r:ILLUSTRATES]->(a:Asset {id:$id}) RETURN c.id AS camera_id, type(r) AS relationship, a.id AS asset_id, r.provenance AS provenance",id=asset_id)
        result['process_paths']=self.query("MATCH (a:Asset {id:$id})-[r:FLOWS_TO]-(n:Asset) RETURN startNode(r).id AS from_id, type(r) AS relationship, endNode(r).id AS to_id, r.provenance AS provenance",id=asset_id)
        result['documents']=self.query("MATCH (a:Asset {id:$id})-[:HAS_DOCUMENT]->(d:Document) RETURN d{.*} AS document ORDER BY d.id",id=asset_id)
        result['field_reports']=self.query("MATCH (f:FieldReport)-[:ON_ASSET]->(a:Asset {id:$id}) OPTIONAL MATCH (f)-[:DOCUMENTS]->(w:WorkOrder) RETURN f{.*, work_order_id:w.id} AS report ORDER BY f.date",id=asset_id)
        result['commitments']=self.query("MATCH (w:WorkOrder)-[:ON_ASSET]->(:Asset {id:$id}) MATCH (w)-[:COMMITTED_TO]->(c:Commitment) OPTIONAL MATCH (c)-[:FULFILLED_BY]->(f:WorkOrder) "
                                         "RETURN c{.*, work_order_id:w.id, work_order_date:w.date, fulfilled_by:f.id, fulfilled_by_date:f.date} AS commitment ORDER BY c.id",id=asset_id)
        result['work_order_links']=self.query("MATCH (w:WorkOrder)-[:ON_ASSET]->(:Asset {id:$id}) MATCH (w)-[r]-(o:WorkOrder) "
                                              "RETURN DISTINCT startNode(r).id AS from_id, type(r) AS relationship, endNode(r).id AS to_id, startNode(r).date AS from_date, endNode(r).date AS to_date ORDER BY from_id, to_id",id=asset_id)
        result['prior_incidents']=self.query("MATCH (r:IncidentRecord)-[:CONSIDERED]->(:Asset {id:$id}) OPTIONAL MATCH (r)-[:CONCERNS]->(s:Asset) RETURN r{.*, selected_asset_id:s.id} AS incident ORDER BY r.occurred_at_utc",id=asset_id)
        result['neighbors'].sort(key=lambda n:n['id'])
        result['sensors'].sort(key=lambda n:n['sensor']['id'])
        result['camera_paths'].sort(key=lambda n:(n['camera_id'],n['asset_id']))
        result['process_paths'].sort(key=lambda n:(n['from_id'],n['to_id']))
        result['backend']='neo4j' 
        result['evidence_reference']=f'neo4j:Asset/{asset_id}'
        return result

    def remember_incident(self, record):
        """Write a closed incident into the graph as organizational memory (idempotent per incident and schema)."""
        rows=self.query("MATCH (a:Asset {id:$selected}) MATCH (c:Asset) WHERE c.id IN $candidates "
                        "MERGE (r:IncidentRecord {id:$id, workflow_schema:$schema}) SET r += $props "
                        "MERGE (r)-[:CONCERNS]->(a) MERGE (r)-[:CONSIDERED]->(c) RETURN count(DISTINCT c) AS linked",
                        id=record['id'], schema=record['workflow_schema'], selected=record['selected_asset_id'],
                        candidates=record['candidate_asset_ids'], props={k:v for k,v in record.items() if k not in ('id','workflow_schema')})
        return dict(status='written', node=f"IncidentRecord/{record['id']}", linked_assets=rows[0]['linked'] if rows else 0)
    def forget_workflow(self, schema):
        """Remove incident memory written from a disposable workflow schema (tests, rehearsals)."""
        self.query("MATCH (r:IncidentRecord {workflow_schema:$schema}) DETACH DELETE r", schema=schema)
    PLANT_LABELS=('Asset','Sensor','Camera','WeatherStation','WorkOrder','Document','FieldReport','Commitment','IncidentRecord')
    def plant(self, facility_id, labels=PLANT_LABELS):
        """Plant subgraph for display: chosen node labels and every relationship between them."""
        nodes=self.query("MATCH (n) WHERE (n.facility_id=$f OR n:IncidentRecord) AND any(l IN labels(n) WHERE l IN $labels) "
                         "RETURN labels(n)[0] AS label, n{.*} AS props ORDER BY label, n.id",f=facility_id,labels=list(labels))
        edges=self.query("MATCH (a)-[r]->(b) WHERE any(l IN labels(a) WHERE l IN $labels) AND any(l IN labels(b) WHERE l IN $labels) "
                         "AND (a.facility_id=$f OR b.facility_id=$f) RETURN a.id AS from_id, type(r) AS type, b.id AS to_id, r{.*} AS props",
                         f=facility_id,labels=list(labels))
        return dict(facility_id=facility_id, labels=list(labels), nodes=nodes, edges=edges)
    def summary(self, facility_id):
        nodes=self.query("MATCH (n) WHERE n.facility_id=$f RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC",f=facility_id)
        edges=self.query("MATCH (a)-[r]->(b) WHERE a.facility_id=$f OR b.facility_id=$f RETURN type(r) AS type, count(*) AS count ORDER BY count DESC",f=facility_id)
        return dict(facility_id=facility_id, nodes=sum(n['count'] for n in nodes), edges=sum(e['count'] for e in edges), node_labels=nodes, edge_types=edges)
