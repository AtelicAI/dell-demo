"""Build synthetic public fixture data; never imported by runtime investigation."""
import json, math, random, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from app.contracts import Facility, DetectionEvent, Measurement, IncidentTransition, EvidenceReference, ReviewDecision
from app.dispersion import concentration_ppm
from app.process import solve_process
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"

def write(path, obj):
    p=DATA/path;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2)+"\n")

def generate():
    tags=[("V-101","separator",2,6),("P-101","pipe",5,6),("XV-104","valve",7,6),("P-102","pipe",9,6),("K-201","compressor",12,6),("P-201","pipe",15,6),("XV-2104","valve",18,6),("F-205","flange",20,6),("P-202","pipe",22,6),("XV-2110","valve",25,6),("P-203","pipe",27,6),("XV-105","valve",2,10),("P-105","pipe",6,10),("XV-2105","valve",18,10),("VENT-01","vent",21,10),("XV-2111","valve",25,10)]
    assets=[dict(id=t,name={"V-101":"Inlet separator","K-201":"Gas compressor","XV-2104":"Discharge isolation valve","F-205":"Discharge flange"}.get(t,t),kind=k,area="Compression skid",position=[x,y,1.2],dimensions=[2 if k in ("separator","compressor") else 1,1,2],pressure_class="Synthetic design: PN40",document_ids=["PROC-001"]) for t,k,x,y in tags]
    chain=[t for t,_,_,_ in tags[:11]]
    edges=[dict(from_id=a,to_id=b,line_id="L-201") for a,b in zip(chain,chain[1:])]
    edges += [dict(from_id=a,to_id=b,line_id="L-VENT") for a,b in [("V-101","XV-105"),("XV-105","P-105"),("XV-2104","XV-2105"),("XV-2105","VENT-01"),("XV-2110","XV-2111")]]
    regions=[dict(asset_id=t,polygon=p) for t,p in [("XV-2104",[[.42,.44],[.59,.44],[.59,.72],[.42,.72]]),("F-205",[[.57,.46],[.70,.46],[.70,.70],[.57,.70]]),("P-201",[[.25,.45],[.40,.45],[.40,.65],[.25,.65]])]]
    cameras=[dict(id="CAM-01",position=[10,-5,4],orientation_degrees=[0,25,0],fov_degrees=60,resolution=[1280,720],calibration_status="illustrative_uncalibrated",video_id=None,regions=regions)]
    sensors=[]
    for i,(x,y) in enumerate([(25,6),(30,6),(35,8),(18,0),(8,6)],1):
        sensors.append(dict(id=f"CH4-{i:02}",asset_id="XV-2104" if i<4 else "K-201",variable="methane",unit="ppm",position=[x,y,1.2],sample_interval_seconds=1,uncertainty=.12,detection_limit=.1,lag_seconds=8))
    for tag,asset,var,unit in [("PT-101","V-101","pressure","bar(a)"),("PT-201","K-201","pressure","bar(a)"),("PT-2104","XV-2104","pressure","bar(a)"),("TT-101","V-101","temperature","K"),("TT-201","K-201","temperature","K"),("FT-201","P-201","flow","kg/h"),("MET-01-WS","K-201","wind_speed","m/s"),("MET-01-WD","K-201","wind_direction","deg_from_north")]:
        sensors.append(dict(id=tag,asset_id=asset,variable=var,unit=unit,position=[12,6,3] if tag.startswith("MET") else next(a['position'] for a in assets if a['id']==asset),sample_interval_seconds=1,uncertainty=.02 if var=="pressure" else .1,detection_limit=0,lag_seconds=0))
    header=dict(id="FAC-001",name="Representative compression skid",coordinate_reference="local_ENU_m",geometry_version="1.0")
    if (DATA/'facility/facility.json').exists():
        from app.data import load_facility
        f=load_facility()
    else:
        f=Facility.model_validate({**header,"assets":assets,"connections":edges,"cameras":cameras,"sensors":sensors})
        write(Path("facility/facility.json"),header)
        for k in ("assets","connections","cameras","sensors"):write(Path(f"facility/{k}.json"),[v.model_dump() for v in getattr(f,k)])
    source=next(a for a in f.assets if a.id=='XV-2104')
    source_x,source_y,source_height=source.position
    process=solve_process(f)
    process_predictions={r['sensor_id']:r['value'] for r in process['measurements']}
    write(Path('generated/process_solution.json'),process)
    # Rates match the metered releases behind the recorded footage (GasVid MOV_1242, Wang et al. Table 1):
    # class 0 (5.3 g/h, below camera detection) before onset, class 1 (277.7 g/h) from the alert onward.
    source_rate_kg_h=.2777
    weep_rate_kg_h=.0053
    def release_rate(scenario_id,seconds):
        if seconds>=1200:return 0.
        return source_rate_kg_h if scenario_id!='SCN-002' and seconds>=600 else weep_rate_kg_h
    leak_process=solve_process(f,{'withdrawals_kg_s':{source.id:source_rate_kg_h/3600}})
    leak_process_predictions={r['sensor_id']:r['value'] for r in leak_process['measurements']}
    write(Path('ground_truth/process_leak_solution.json'),leak_process)
    # Maintenance history is canonical and hand-curated once it exists; only bootstrap placeholders.
    if not (DATA/"maintenance.json").exists():
        maintenance=[dict(id=f"WO-{i:03}",asset_id=["XV-2104","K-201","V-101","F-205","XV-2105","PT-201","XV-2110","XV-2104"][i-1],date=f"2026-09-{i+2:02}",issue="Synthetic inspection record",action="Example visual inspection; no confirmed present fault",status="closed",origin_type="simulated",document_id="PROC-001") for i in range(1,9)]
        # Work orders refer to equipment, not instrumentation IDs.
        maintenance[5]['asset_id']='K-201'
        write(Path("maintenance.json"),maintenance)
    for i,title in enumerate(["Visual inspection","Engineering isolation review","Packing inspection review"],1):
        if not (DATA/f"documents/PROC-{i:03}.md").exists(): (DATA/f"documents/PROC-{i:03}.md").write_text(f"# PROC-{i:03}: {title}\n\nSynthetic example document. Not an operational procedure.\nRecord observations, verify equipment identity and obtain an authorized engineering review. No plant actuation is provided by this application.\n")
    start=datetime(2026,10,23,8,0,tzinfo=timezone.utc)
    scenarios=[dict(id=s,incident_id=i,start_utc=start.isoformat(),duration_seconds=1800,replay_limit_seconds=1199,seed=42,title=title) for s,i,title in [("SCN-001","INC-001","Representative methane observation"),("SCN-002","INC-002","Control: release below camera detection"),("SCN-003","INC-003","Ambiguous observation; wind unavailable")]]
    write(Path("scenarios/scenarios.json"),scenarios)
    event=dict(event_id="DET-001",incident_id="INC-001",scenario_id="SCN-001",camera_id="CAM-01",timestamp_utc=(start+timedelta(seconds=600)).isoformat(),clip_time_seconds=None,mode="synthetic_annotation_fixture",origin_polygon=[[.51,.50],[.64,.50],[.64,.65],[.51,.65]],confidence=None,provenance=dict(source_kind="simulated",source_dataset="atelic-synthetic-fixture",generator_version="1.0",reference="scenarios/detections.json#DET-001"))
    events=[event,{**event,"event_id":"DET-003","incident_id":"INC-003","scenario_id":"SCN-003","provenance":{**event['provenance'],"reference":"scenarios/detections.json#DET-003"}}]
    write(Path("scenarios/detections.json"),[DetectionEvent.model_validate(e).model_dump(mode="json") for e in events])
    if not (DATA/"video_manifest.json").exists(): write(Path("video_manifest.json"),dict(status="unavailable",file=None,fps=None,rights="unverified",source="No recorded footage installed",mapping="Synthetic annotation fixture, not GasVid or model inference"))
    write(Path("ground_truth/private_truth.json"),dict(source_asset_id="XV-2104",source_position=list(source.position),rate_kg_h=source_rate_kg_h,pre_onset_weep_kg_h=weep_rate_kg_h,rate_basis="GasVid MOV_1242 metered class 1 (277.7 g/h) and class 0 (5.3 g/h); Wang et al., Applied Energy 2020, Table 1",onset_seconds=600,validation_only_counterfactual_stop_seconds=1200,warning="Generator/validation only; not investigation evidence. Recovery is not an approval."))
    out=DATA/"generated";out.mkdir(exist_ok=True)
    with (out/"measurements.jsonl").open('w') as output, (DATA/'ground_truth/clean_concentrations.jsonl').open('w') as clean_output:
        for scenario in scenarios:
            rng=random.Random(scenario['seed']); previous={s.id:2. for s in f.sensors if s.variable=='methane'}
            drift_rng=random.Random(scenario['seed']+988)
            drift={s.id:0. for s in f.sensors if s.variable=='methane'}
            for t in range(1801):
                speed=2.5+.15*math.sin(t/180)
                direction=270+6*math.sin(t/240)
                for sensor in f.sensors:
                    qc='good'; value=0.
                    if sensor.variable=='methane':
                        dx,dy=sensor.position[0]-source_x,sensor.position[1]-source_y
                        transport=max(0,dx)/speed
                        source_t=t-transport
                        rate=release_rate(scenario['id'],source_t)
                        clean=2.+concentration_ppm(rate,dx,dy,sensor.position[2],source_height,speed,direction)
                        clean_output.write(json.dumps({'scenario_id':scenario['id'],'sensor_id':sensor.id,'scenario_seconds':t,'clean_ppm':clean})+'\n')
                        alpha=1-math.exp(-1/sensor.lag_seconds)
                        previous[sensor.id]+=alpha*(clean-previous[sensor.id])
                        drift[sensor.id]+=drift_rng.gauss(0,.0005)
                        value=max(0.,previous[sensor.id]+rng.gauss(0,sensor.uncertainty)+drift[sensor.id])
                        if t%179==0 and t>0:qc='missing';value=None
                    elif sensor.variable=='wind_speed':value=speed
                    elif sensor.variable=='wind_direction':value=direction
                    elif sensor.variable in ('pressure','temperature','flow'):
                        # Same source mass as dispersion; steady states, not a transient solver.
                        predictions=leak_process_predictions if release_rate(scenario['id'],t)>=source_rate_kg_h else process_predictions
                        prediction=predictions[sensor.id]
                        if prediction is None:qc='missing';value=None
                        else:value=prediction+rng.gauss(0,sensor.uncertainty)
                    if scenario['id']=='SCN-003' and sensor.variable.startswith('wind') and t>=600:qc='missing';value=None
                    row=Measurement(observation_id=f"{scenario['id']}:{sensor.id}:{t}",timestamp_utc=start+timedelta(seconds=t),scenario_id=scenario['id'],sensor_id=sensor.id,asset_id=sensor.asset_id,value=round(value,5) if value is not None else None,unit=sensor.unit,quality_flag=qc,generator_version='gaussian-process-2')
                    output.write(row.model_dump_json()+"\n")
    write(Path("provenance.json"),dict(facility="simulated",telemetry="simulated; Gaussian forward + solved steady process baseline",detector="synthetic_annotation_fixture",video="recorded GasVid metadata ready; annotations pending; independent scene",runtime="local development; Dell deployment not verified",physics="idealized Gaussian open-terrain forward model, not validated skid CFD",recovery="post-1200s counterfactual validation only; not served by interactive replay"))
    for name,model in [("facility",Facility),("detection_event",DetectionEvent),("measurement",Measurement),("incident_transition",IncidentTransition),("evidence_reference",EvidenceReference),("review_decision",ReviewDecision)]:
        p=ROOT/f"docs/contracts/{name}.schema.json";p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(model.model_json_schema(),indent=2)+"\n")
    # Static engineering schematic is derived from the same canonical geometry.
    elements=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 380"><title>Synthetic representative facility; not an operational P&amp;ID</title><rect width="760" height="380" fill="#f7faf7"/>']
    positions={a.id:(50+a.position[0]*23,50+a.position[1]*20) for a in f.assets}
    for c in f.connections:
        x,y=positions[c.from_id];xx,yy=positions[c.to_id]
        elements.append(f'<path d="M{x} {y} L{xx} {yy}" stroke="#92afa0" stroke-width="4"/>')
    for a in f.assets:
        x,y=positions[a.id]
        elements.append(f'<g id="{a.id}"><rect x="{x-15}" y="{y-12}" width="30" height="24" rx="4" fill="white" stroke="#237d62"/><text x="{x}" y="{y+30}" text-anchor="middle" font-family="sans-serif" font-size="10">{a.id}</text></g>')
    elements.append('<text x="40" y="335" font-family="sans-serif" font-size="12">Synthetic equipment connectivity · FAC-001 · geometry 1.0</text></svg>')
    p=DATA/'geometry/skid.svg';p.parent.mkdir(exist_ok=True);p.write_text(''.join(elements))
    if not (DATA/'source_manifest.json').exists(): write(Path('source_manifest.json'),{'schema_version':'1.0','sources':[{'id':'atelic-synthetic-fixture','kind':'simulated','version':'gaussian-fixture-1','seed':42,'license':'Project-authored synthetic demonstration data','records':['facility/','documents/','maintenance.json','scenarios/','generated/measurements.jsonl']},{'id':'gasvid','kind':'recorded','status':'not_acquired','url':'https://github.com/weathon/Lang-Gas','rights':'unverified','checkpoint':'not_installed'},{'id':'tadi','kind':'recorded','status':'not_acquired','purpose':'independent physics validation only'}]})
    print(f"Generated {len(f.assets)} assets, {len(f.sensors)} sensors, 3 scenarios; {1801*len(f.sensors)*3} observations")
if __name__=='__main__': generate()
