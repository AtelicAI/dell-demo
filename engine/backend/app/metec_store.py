"""Namespace-scoped measured benchmark stores; no synthetic data replacement or truth ingestion."""
import json
import os
from datetime import timedelta
from .metec import utc

BUCKET='atelic-metec-2024'


def influx_history(case,as_of_utc):
    from influxdb_client import InfluxDBClient
    end=min(utc(as_of_utc),utc(case['history_end_utc']))
    quote=json.dumps
    flux=f'''from(bucket:{quote(BUCKET)})
      |> range(start:time(v:{quote(case['history_start_utc'])}),stop:time(v:{quote((end+timedelta(microseconds=1)).isoformat())}))
      |> filter(fn:(r)=>r._measurement=="recorded_benchmark" and r.incident_id=={quote(case['id'])} and r._field=="payload_json")'''
    with InfluxDBClient(url=os.getenv('INFLUX_URL','http://127.0.0.1:8286'),token=os.environ['INFLUX_TOKEN'],org=os.getenv('INFLUX_ORG','atelic'),timeout=30000) as client:
        rows=[]
        for table in client.query_api().query(flux):
            for record in table.records:
                row=json.loads(record.get_value())
                if utc(row['timestamp_utc'])!=record.get_time() or utc(row['timestamp_utc'])>end:
                    raise ValueError('Historian/source timestamp mismatch')
                rows.append(row)
    if not rows:raise ValueError('No recorded benchmark observations available at this clock')
    return sorted(rows,key=lambda r:(r['timestamp_utc'],r['sensor_id']))


def graph_context(graph,facility_id,as_of_utc):
    nodes=graph.query('''MATCH(n) WHERE n.facility_id=$facility AND
        (n:BenchmarkFacility OR n:EquipmentGroup OR n:ReleasePoint OR n:MeasuredSensor OR n:RecordedIncident OR
         (n:MeasurementWindow AND n.end_utc<=$asof)) RETURN n{.*} AS properties,labels(n) AS labels''',
        facility=facility_id,asof=utc(as_of_utc).isoformat())
    if not nodes:raise ValueError('Measured benchmark graph is not ingested')
    ids=[n['properties']['id'] for n in nodes]
    edges=graph.query('''MATCH(a)-[r]->(b) WHERE a.id IN $ids AND b.id IN $ids
        RETURN a.id AS from_id,type(r) AS relationship,b.id AS to_id,r{.*} AS properties''',ids=ids)
    return dict(nodes=nodes,edges=edges,backend='neo4j',facility_id=facility_id,
        origin_type='recorded_geometry_and_measurement_references',source_confirmed=False,
        warning='Proximity is not causal attribution; no piping or camera connectivity is asserted')


def ingest_graph(graph,facility,case,observations):
    """Merge only METEC namespace labels and metadata; never current release truth."""
    namespace=facility['id'];allowed=('BenchmarkFacility','EquipmentGroup','ReleasePoint','MeasuredSensor','RecordedIncident','MeasurementWindow')
    for label in allowed:
        graph.query(f'CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR(n:{label}) REQUIRE n.id IS UNIQUE')
    graph.query('''MERGE(f:BenchmarkFacility {id:$id}) SET f.name=$name,f.facility_id=$id,
        f.origin_type='recorded_geometry',f.coordinate_reference='local_ENU_m' ''',id=namespace,name=facility['name'])
    groups=[dict(id=namespace+'-GROUP-'+p['group_id'],kind=p['equipment_group_type']) for p in facility['release_points']]
    graph.query('''UNWIND $rows AS r MERGE(g:EquipmentGroup {id:r.id}) SET g.facility_id=$facility,g.kind=r.kind,
        g.origin_type='published_equipment_group' WITH g MATCH(f:BenchmarkFacility {id:$facility}) MERGE(g)-[:PART_OF]->(f)''',rows=groups,facility=namespace)
    points=[dict(id=p['id'],tag=p['source_tag'],x=p['position_m'][0],y=p['position_m'][1],z=p['position_m'][2],
                 group=namespace+'-GROUP-'+p['group_id']) for p in facility['release_points']]
    graph.query('''UNWIND $rows AS r MERGE(p:ReleasePoint {id:r.id}) SET p.facility_id=$facility,p.source_tag=r.tag,
        p.x_m=r.x,p.y_m=r.y,p.z_m=r.z,p.origin_type='recorded_geometry'
        WITH p,r MATCH(g:EquipmentGroup {id:r.group}) MERGE(p)-[:PART_OF]->(g)''',rows=points,facility=namespace)
    sensors=[dict(id=s['id'],variable=s['variable'],x=s['position_m'][0],y=s['position_m'][1],z=s['position_m'][2]) for s in facility['sensors']]
    graph.query('''UNWIND $rows AS r MERGE(s:MeasuredSensor {id:r.id}) SET s.facility_id=$facility,
        s.variable=r.variable,s.x_m=r.x,s.y_m=r.y,s.z_m=r.z,s.origin_type='recorded_geometry'
        WITH s MATCH(f:BenchmarkFacility {id:$facility}) MERGE(s)-[:PART_OF]->(f)''',rows=sensors,facility=namespace)
    graph.query('''MERGE(i:RecordedIncident {id:$id}) SET i.facility_id=$facility,i.source_confirmed=false,
        i.origin_type='recorded_controlled_experiment',i.source_dataset='metec-fenceline-2024'
        WITH i MATCH(p:ReleasePoint {facility_id:$facility}) MERGE(i)-[:HAS_CANDIDATE]->(p)''',id=case['id'],facility=namespace)
    windows=[dict(id='MW-'+r['observation_id'],sensor_id=r['sensor_id'],start=r['interval_start_utc'],end=r['timestamp_utc'],
                  qc=r['quality_flag'],reference=r['observation_id'],n=r['sample_count']) for r in observations]
    graph.query('''UNWIND $rows AS r MERGE(w:MeasurementWindow {id:r.id}) SET w.facility_id=$facility,
        w.start_utc=r.start,w.end_utc=r.end,w.quality_flag=r.qc,w.observation_id=r.reference,
        w.sample_count=r.n,w.origin_type='recorded_aggregated'
        WITH w,r MATCH(s:MeasuredSensor {id:r.sensor_id}),(i:RecordedIncident {id:$incident})
        MERGE(s)-[:HAS_MEASUREMENT_WINDOW]->(w) MERGE(w)-[:SUPPORTS]->(i)''',
        rows=windows,facility=namespace,incident=case['id'])
    return graph_context(graph,namespace,case['history_end_utc'])
