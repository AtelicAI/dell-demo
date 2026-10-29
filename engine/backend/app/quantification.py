"""Public-observation inverse calculation. No generator or hidden-label access."""
import math
from datetime import datetime, timedelta
import numpy as np

METHOD = 'nonnegative-gaussian-candidate-fit'
ASSUMPTIONS = ['Steady open-terrain reflected Gaussian; local ENU; wind FROM north clockwise',
               'Methane at 293.15 K and 101325 Pa; fitted constant background 0..5 ppm',
               'Unit-rate spread sigma_y=max(0.5,0.12*x), sigma_z=max(0.4,0.08*x)',
               'Same-family synthetic consistency only; no obstacle or real-release validation',
               'Window 60 s; exact same-time wind; at least 3 samples at 2 distinct downwind locations',
               'Sensitivity envelope is not a confidence interval or validated statistical uncertainty',
               'Steady plume required: early/late half-window rates must agree within 30%',
               'Candidate comparison: a source location is consistent only if its fit error is within 2x the best fit or best + 3 sensor uncertainties']
STEADY_TOLERANCE = .3
COMPARABLE_FACTOR = 2.
NOISE_MULTIPLE = 3.


def _time(x):
    t = x if isinstance(x, datetime) else datetime.fromisoformat(x.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t


def _response(source, receptor, speed, direction):
    """Separately derived unit kg/h response; deliberately no forward-model import."""
    east, north, height = np.asarray(receptor)-np.asarray(source)
    theta = math.radians(direction)
    along = -east*math.sin(theta)-north*math.cos(theta)
    cross = east*math.cos(theta)-north*math.sin(theta)
    if along <= 0:
        return 0.
    lateral, vertical = max(.5, .12*along), max(.4, .08*along)
    z, h = receptor[2], source[2]
    reflection = math.exp(-.5*((z-h)/vertical)**2)+math.exp(-.5*((z+h)/vertical)**2)
    mass_density = math.exp(-.5*(cross/lateral)**2)*reflection/(3600*2*math.pi*speed*lateral*vertical)
    return mass_density*(8.314462618*293.15)/(101325*.01604)*1e6


def label_fits(fits, noise_ppm, gate_ppm):
    """Mark each assumed source location; only consistent locations keep a rate."""
    residuals = [f['residual_rms_ppm'] for f in fits if 'residual_rms_ppm' in f]
    best = min(residuals, default=None)
    for f in fits:
        if 'residual_rms_ppm' not in f:
            f.pop('status', None)
            f.update(fit_status='cannot_test', reason='no_downwind_sensor_coverage', rate_kg_h=None)
            continue
        error = f['residual_rms_ppm']
        consistent = (error <= max(COMPARABLE_FACTOR*best, best+noise_ppm) and error <= gate_ppm
                      and f['detected_locations'] >= 2)
        f['fit_status'] = ('best_fit' if error == best else 'comparable_fit') if consistent else 'poor_fit'
        f['error_ratio_to_best'] = round(error/best, 2) if best else None
        if not consistent:
            # Rates from fits that do not explain the readings are not estimates.
            f['rate_kg_h'] = None
            f['background_ppm'] = None


def compare_candidates(calculation, camera_asset_ids):
    """Does the telemetry agree with the camera? Same Gaussian assumptions; not source confirmation."""
    fits = calculation.get('candidate_fits', [])
    if calculation.get('status') != 'estimated' and calculation.get('reason') != 'candidate_inconsistent_with_readings':
        return dict(status='not_assessed', reason=calculation.get('reason'),
                    note='Location comparison needs a window that yields a valid fit')
    def view(f):
        return {k: f.get(k) for k in ('candidate_asset_id', 'fit_status', 'rate_kg_h', 'residual_rms_ppm', 'error_ratio_to_best')}
    best = next((f for f in fits if f['fit_status'] == 'best_fit'), None)
    camera = [view(f) for f in fits if f['candidate_asset_id'] in camera_asset_ids]
    elsewhere = [view(f) for f in fits if f['candidate_asset_id'] not in camera_asset_ids and f['fit_status'] in ('best_fit', 'comparable_fit')]
    consistent_camera = [f['candidate_asset_id'] for f in camera if f['fit_status'] in ('best_fit', 'comparable_fit')]
    status = ('no_consistent_location' if best is None else
              'disagrees' if not consistent_camera else
              'partly_agrees' if elsewhere else
              'agrees_single' if len(consistent_camera) == 1 else 'agrees_ambiguous')
    return dict(status=status, best_fit_asset_id=best and best['candidate_asset_id'], camera_candidates=camera,
                consistent_camera_candidates=consistent_camera, consistent_outside_camera_view=elsewhere,
                note='Comparison of assumed source locations under the same Gaussian assumptions; not a confirmed source')


def quantify(facility, measurements, candidate_asset_id, as_of_utc):
    """Fit each positioned canonical asset; report requested candidate, never confirmation.

    Returns JSON-compatible dictionary. Invalid/insufficient inputs return
    status=rate_unavailable, rate_kg_h=None and a machine-readable reason.
    """
    result = dict(method=METHOD, version='1.0', status='rate_unavailable', rate_kg_h=None,
                  unit='kg/h', candidate_asset_id=candidate_asset_id, source_confirmed=False,
                  assumptions=ASSUMPTIONS.copy(), input_ids=[], candidate_fits=[])
    def fail(reason):
        if reason != 'candidate_inconsistent_with_readings':
            # The window itself is unusable, so no location's fit is an estimate either.
            for f in result['candidate_fits']:
                f.update(fit_status='not_assessed', rate_kg_h=None, background_ppm=None)
        return {**result, 'reason': reason}
    try:
        from scipy.optimize import lsq_linear
    except ImportError:
        return fail('scipy_unavailable')
    try:
        f = facility.model_dump() if hasattr(facility, 'model_dump') else facility
        end = _time(as_of_utc)
        if f['coordinate_reference'] != 'local_ENU_m':
            return fail('invalid_geometry')
        assets = {a['id']: a for a in f['assets']}
        sensors = {s['id']: s for s in f['sensors']}
        if candidate_asset_id not in assets:
            return fail('unknown_candidate')
        for obj in list(assets.values())+list(sensors.values()):
            p = obj['position']
            if len(p)!=3 or not all(math.isfinite(v) for v in p) or p[2]<0:
                return fail('invalid_geometry')
        rows, seen, scenarios = [], set(), set()
        units = dict(methane='ppm', wind_speed='m/s', wind_direction='deg_from_north')
        for item in measurements:
            r = item.model_dump() if hasattr(item, 'model_dump') else item
            t = _time(r['timestamp_utc'])
            if t > end or t < end-timedelta(seconds=60):
                continue
            s = sensors.get(r['sensor_id'])
            if s is None or r['asset_id'] != s['asset_id']:
                return fail('invalid_sensor_reference')
            if s['variable'] not in units:
                continue
            if s['unit'] != units[s['variable']] or r['unit'] != s['unit']:
                return fail('invalid_units')
            key = (t, s['id'])
            if key in seen:
                return fail('duplicate_measurement')
            seen.add(key)
            scenarios.add(r['scenario_id'])
            if r['quality_flag'] != 'good' or r['value'] is None:
                continue
            if not math.isfinite(r['value']) or r['value'] < 0:
                return fail('invalid_value')
            rows.append((t, s, r))
        if len(scenarios)>1:
            return fail('mixed_scenarios')
        wind = {}
        for t,s,r in rows:
            if s['variable'].startswith('wind_'):
                wind.setdefault(t,{})[s['variable']] = r
        samples = []
        for t,s,r in rows:
            if s['variable'] != 'methane':
                continue
            w = wind.get(t,{})
            if not {'wind_speed','wind_direction'} <= w.keys():
                continue
            u, direction = w['wind_speed']['value'], w['wind_direction']['value']
            if u < .5 or not 0 <= direction < 360:
                return fail('invalid_low_wind_or_direction')
            samples.append((s,r,u,direction,w))
        if not samples:
            return fail('missing_wind_or_methane')
        speeds = np.array([x[2] for x in samples])
        angles = np.radians([x[3] for x in samples])
        if np.std(speeds)/np.mean(speeds)>.3 or abs(np.mean(np.exp(1j*angles))) < math.cos(math.radians(15)):
            return fail('unstable_wind')
        result['input_ids'] = sorted({r['observation_id'] for s,r,u,d,w in samples} |
                                     {v['observation_id'] for s,r,u,d,w in samples for v in w.values()})
        y = np.array([r['value'] for s,r,u,d,w in samples])
        weights = np.array([1/max(.01,s['uncertainty']) for s,r,u,d,w in samples])
        def fit(source, speed_factor=1., rotation=0., subset=None):
            index = np.arange(len(samples)) if subset is None else subset
            part, values, scale = [samples[i] for i in index], y[index], weights[index]
            response = np.array([_response(source,s['position'],u*speed_factor,d+rotation) for s,r,u,d,w in part])
            informative = response > .01
            locations = {tuple(s['position']) for (s,r,u,d,w), ok in zip(part,informative) if ok}
            times = {r['timestamp_utc'] for (s,r,u,d,w), ok in zip(part,informative) if ok}
            if len(locations)<2 or len(times)<3:
                return None
            design = np.column_stack((response,np.ones(len(values))))
            if np.linalg.matrix_rank(design)<2:
                return None
            solved = lsq_linear(design*scale[:,None],values*scale,bounds=([0,0],[np.inf,5]),tol=1e-12)
            if not solved.success:
                return None
            q,b = solved.x
            residual = float(np.sqrt(np.mean((design@solved.x-values)**2)))
            signal_locations = {tuple(s['position']) for (s,r,u,d,w),ok in zip(part,informative)
                                if ok and r['value']-b > max(s['detection_limit'],3*s['uncertainty'])}
            return dict(rate_kg_h=float(q), background_ppm=float(b), residual_rms_ppm=residual,
                        detected_locations=len(signal_locations))
        chosen = None
        for aid,a in sorted(assets.items()):
            fitted = fit(a['position'])
            result['candidate_fits'].append(dict(candidate_asset_id=aid, **(fitted or {'status':'insufficient_geometry'})))
            if aid==candidate_asset_id:
                chosen = fitted
        gate = max(.5, .25*float(np.ptp(y)))
        label_fits(result['candidate_fits'], NOISE_MULTIPLE*float(np.median([s['uncertainty'] for s,r,u,d,w in samples])), gate)
        if chosen is None:
            return fail('insufficient_downwind_sensors_or_samples')
        if chosen['detected_locations']<2:
            return fail('no_resolved_downwind_signal')
        if chosen['residual_rms_ppm'] > gate:
            return fail('poor_model_fit')
        if next(f for f in result['candidate_fits'] if f['candidate_asset_id'] == candidate_asset_id)['fit_status'] == 'poor_fit':
            # Another location explains the readings much better; a rate for this one would mislead.
            return fail('candidate_inconsistent_with_readings')
        # Steady-plume check: earlier and later halves of the window must agree on the rate.
        times = np.array([_time(r['timestamp_utc']).timestamp() for s,r,u,d,w in samples])
        middle = float(np.median(times))
        halves = [fit(assets[candidate_asset_id]['position'], subset=np.flatnonzero(side))
                  for side in (times < middle, times >= middle)]
        if all(halves):
            early, late = halves[0]['rate_kg_h'], halves[1]['rate_kg_h']
            result['steady_state_check'] = dict(early_half_rate_kg_h=early, late_half_rate_kg_h=late, max_relative_change=STEADY_TOLERANCE)
            if abs(late-early) > STEADY_TOLERANCE*max(early, late, 1e-9):
                return fail('non_steady_plume')
        variants = [fit(assets[candidate_asset_id]['position'],u,d) for u in (.8,1.,1.2) for d in (-10,0,10)]
        rates = [v['rate_kg_h'] for v in variants if v is not None]
        return {**result, **chosen, 'status':'estimated', 'reason':None,
                'sensitivity_kg_h':{'min':min(rates), 'max':max(rates), 'wind_speed_fraction':.2,
                                    'wind_direction_degrees':10, 'kind':'assumption_sensitivity_not_confidence_interval'}}
    except (KeyError, TypeError, ValueError, OverflowError):
        return fail('invalid_input')
