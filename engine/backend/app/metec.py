"""Measured, source-separated METEC benchmark. No access to release truth or raw schedules."""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

METHOD='measured-gaussian-candidate-fit-v1'


def utc(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(parsed,datetime) or parsed.tzinfo is None:raise ValueError('Timezone-aware UTC timestamp required')
    return parsed.astimezone(timezone.utc)


def local_position(lat,lon,height,origin=(40.59603590,-105.14032186)):
    if not all(math.isfinite(x) for x in (lat,lon,height)) or not -90<=lat<=90 or not -180<=lon<=180 or height<0:
        raise ValueError('Invalid recorded coordinates')
    return [math.radians(lon-origin[1])*6371008.8*math.cos(math.radians(origin[0])),
            math.radians(lat-origin[0])*6371008.8,height]


def response_ppm_per_kg_h(source,receptor,speed,direction,temperature_k,pressure_pa,spread=1.):
    if len(source)!=3 or len(receptor)!=3 or source[2]<0 or receptor[2]<0:raise ValueError('Three nonnegative-height metric coordinates required')
    if not all(math.isfinite(v) for v in (*source,*receptor,speed,direction,temperature_k,pressure_pa,spread)):
        raise ValueError('Finite physical inputs required')
    if speed<.5 or not 0<=direction<360 or not 200<=temperature_k<=330 or not 50000<=pressure_pa<=110000 or spread<=0:
        raise ValueError('Invalid physical regime/units')
    east,north=receptor[0]-source[0],receptor[1]-source[1]
    theta=math.radians(direction)
    along=-east*math.sin(theta)-north*math.cos(theta)
    cross=east*math.cos(theta)-north*math.sin(theta)
    if along<=0:return 0.
    sy=max(.5,.12*along)*spread;sz=max(.4,.08*along)*spread
    reflected=math.exp(-.5*((receptor[2]-source[2])/sz)**2)+math.exp(-.5*((receptor[2]+source[2])/sz)**2)
    density=math.exp(-.5*(cross/sy)**2)*reflected/(3600*2*math.pi*speed*sy*sz)
    return density*8.314462618*temperature_k/(pressure_pa*.01604)*1e6


def estimate(facility,case,observations,candidate_id,as_of_utc):
    """Evaluate one supplied candidate, never discover it from hidden release labels."""
    result=dict(method=METHOD,status='rate_unavailable',rate_kg_h=None,unit='kg/h',
        candidate_id=candidate_id,source_confirmed=False,origin_type='inferred_from_recorded_measurements',
        calibration_status='experimental_baseline_not_field_validated',input_ids=[],
        assumptions=['Steady reflected open-terrain Gaussian; no obstacle correction',
            'Fixed sigma_y=max(0.5,0.12*x), sigma_z=max(0.4,0.08*x); not calibrated to these labels',
            'Meteorological FROM-north wind; measured pressure and kelvin temperature',
            'Per-inlet pre-event background; equal-weight minute observations',
            'Two physical horizontal inlet sites; exact source attribution can remain ambiguous',
            'Time is closed-bin availability time; current release schedules/rates are not inputs'])
    def fail(reason):return {**result,'reason':reason}
    try:
        end=utc(as_of_utc);start=utc(case['analysis_start_utc'])
        if end<start:return fail('analysis_window_not_yet_available')
        end=min(end,utc(case['analysis_end_utc']))
        source=next((p for p in facility['release_points'] if p['id']==candidate_id),None)
        if source is None:return fail('unknown_candidate')
        sensors={s['id']:s for s in facility['sensors'] if s['variable']=='methane'}
        seen=set();history=[]
        for row in observations:
            t=utc(row['timestamp_utc'])
            if t>end:continue
            key=(row['sensor_id'],t)
            if key in seen:return fail('duplicate_measurement')
            seen.add(key)
            if row['quality_flag']=='good':history.append(row)
        background={};background_ids=[]
        for sid in sensors:
            rows=[r for r in history if r['sensor_id']==sid and utc(case['background_start_utc'])<=utc(r['interval_start_utc'])
                  and utc(r['timestamp_utc'])<=utc(case['background_end_utc']) and r['value'] is not None]
            if len(rows)<3:continue
            if any(r['unit']!='ppm' for r in rows):return fail('invalid_methane_units_or_values')
            y=np.array([r['value'] for r in rows],dtype=float)
            if not np.isfinite(y).all() or (y<0).any():return fail('invalid_methane')
            center=float(np.median(y));mad=float(1.4826*np.median(np.abs(y-center)))
            background[sid]=(float(np.quantile(y,.05)),max(.01,3*mad))
            background_ids.extend(r['observation_id'] for r in rows)
        weather={r['timestamp_utc']:r for r in history if r['sensor_id']==facility['weather_sensor_id']}
        samples=[]
        for row in history:
            if row['sensor_id'] not in background or utc(row['interval_start_utc'])<start:continue
            w=weather.get(row['timestamp_utc'])
            if not w:continue
            if row['unit']!='ppm' or row['value'] is None or not math.isfinite(row['value']) or row['value']<0:
                return fail('invalid_methane_units_or_values')
            if w['unit']!='compound_meteorology':return fail('invalid_weather_units')
            if w['wind_speed_m_s']<.5:return fail('invalid_low_wind')
            response_ppm_per_kg_h(source['position_m'],sensors[row['sensor_id']]['position_m'],
                w['wind_speed_m_s'],w['wind_from_degrees'],w['temperature_k'],w['pressure_pa'])
            samples.append((row,w,sensors[row['sensor_id']]['position_m']))
        if len({s[0]['sensor_id'] for s in samples})<2 or len({s[0]['timestamp_utc'] for s in samples})<5:
            return fail('insufficient_sensors_or_closed_minutes')
        speeds=np.array([w['wind_speed_m_s'] for r,w,p in samples]);angles=np.radians([w['wind_from_degrees'] for r,w,p in samples])
        coherence=float(abs(np.mean(np.exp(1j*angles))))
        if speeds.std()/speeds.mean()>.5 or coherence<math.cos(math.radians(20)):
            return fail('unstable_wind')
        enhancement=np.array([r['value']-background[r['sensor_id']][0] for r,w,p in samples])
        thresholds=np.array([background[r['sensor_id']][1] for r,w,p in samples])
        def fit(speed_factor=1.,rotation=0.,spread=1.,indices=None):
            ix=np.arange(len(samples)) if indices is None else np.asarray(indices)
            a=np.array([response_ppm_per_kg_h(source['position_m'],samples[i][2],samples[i][1]['wind_speed_m_s']*speed_factor,
                (samples[i][1]['wind_from_degrees']+rotation)%360,samples[i][1]['temperature_k'],samples[i][1]['pressure_pa'],spread) for i in ix])
            y=enhancement[ix];denominator=float(a@a)
            if denominator<1e-12:return None
            q=max(0.,float(a@y)/denominator)
            return dict(rate_kg_h=q,residual_rms_ppm=float(np.sqrt(np.mean((a*q-y)**2))),response=a)
        fitted=fit()
        if fitted is None:return fail('no_downwind_response')
        informative=fitted['response']>1e-4
        resolved={r['sensor_id'] for (r,w,p),signal,threshold,ok in zip(samples,enhancement,thresholds,informative) if ok and signal>threshold}
        if len(resolved)<2:return fail('no_resolved_signal_at_two_inlets')
        if fitted['residual_rms_ppm']>max(.15,float(np.sqrt(np.mean(enhancement**2)))*.75):
            return fail('poor_model_fit')
        result['input_ids']=sorted(set(background_ids+[r['observation_id'] for r,w,p in samples]+[w['observation_id'] for r,w,p in samples]))
        times=np.array([utc(r['timestamp_utc']).timestamp() for r,w,p in samples]);middle=np.median(times)
        halves=[fit(indices=np.flatnonzero(condition)) for condition in (times<middle,times>=middle)]
        if all(halves) and abs(halves[1]['rate_kg_h']-halves[0]['rate_kg_h'])>.5*max(h['rate_kg_h'] for h in halves):
            return fail('non_steady_signal')
        sensitivity=[]
        for factor,angle,spread in ((.8,0,1),(1.2,0,1),(1,-10,1),(1,10,1),(1,0,.7),(1,0,1.3)):
            if speeds.min()*factor<.5:continue
            trial=fit(factor,angle,spread)
            if trial is not None:sensitivity.append(trial['rate_kg_h'])
        q=fitted['rate_kg_h']
        return {**result,'status':'estimated_experimental','reason':None,'rate_kg_h':q,
            'background_ppm':{k:v[0] for k,v in background.items()},'residual_rms_ppm':fitted['residual_rms_ppm'],
            'sensitivity_kg_h':dict(min=min([q]+sensitivity),max=max([q]+sensitivity),kind='assumption_sensitivity_not_confidence_interval'),
            'samples':len(samples),'closed_minutes':len(set(times)),'wind_resultant':coherence,
            'unit_evidence':facility['unit_evidence']}
    except (KeyError,TypeError,ValueError,OverflowError,StopIteration):return fail('invalid_input')


class RecordedBenchmark:
    """Runtime reader is confined to public derived artifacts; release truth stays elsewhere."""
    def __init__(self,root=None):
        if root is None:
            from .data import DATA
            root=DATA/'benchmarks/metec2024/public'
        self.root=Path(root)
    def read(self):
        facility=json.loads((self.root/'facility.json').read_text())
        case=json.loads((self.root/'incident.json').read_text())
        rows=[json.loads(line) for line in (self.root/'measurements.jsonl').read_text().splitlines()]
        return facility,case,rows
    def history(self,as_of_utc=None):
        facility,case,rows=self.read();end=utc(as_of_utc or case['history_end_utc'])
        return dict(incident=case,measurements=[r for r in rows if utc(r['timestamp_utc'])<=end],
            backend='recorded_public_artifact',origin_type='recorded_aggregated',as_of_utc=end.isoformat(),
            paired_video=None,source_rate_truth_available=False)
