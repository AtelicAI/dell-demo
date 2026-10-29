"""Long-term sensor history before an incident: deterministic episode detection and location fits.

Reads only public 5-minute averages (no generator truth). Each methane excursion above the shared
background is an episode; every positioned asset is fitted with the same Gaussian response as the
live calculation and labeled with the same consistency rule. Fits on 5-minute averages are indicative.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import math
import numpy as np
from .quantification import _response, label_fits

SCENARIO_ID = 'HIST-FAC-001'
INDICATIVE_KG_H = .05  # a 50 g/h release: the observability yardstick
MIN_SLOTS = 2
MAX_GAP_SLOTS = 1
ASSUMPTIONS = ['5-minute averages; regional background = lowest methane reading at each time (sensors share one background field)',
               'Episode = at least 2 slots (gap of at most 1) where a sensor exceeds max(detection limit, 3x robust background noise)',
               'Location fits use the live Gaussian response with 5-minute mean wind; wind meander within 5 minutes is unresolved, '
               'so fits only narrow the area and no rate is estimated from history',
               'Silence while the wind blows away from every sensor is not evidence that nothing leaked']
QUIET_GAP_DAYS = 3
LOAD_CHANGE_WINDOW_MINUTES = 90


def _t(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))


def analyze(facility, rows, focus_asset_ids=(), load_changes=None, keep_locations=False):
    """load_changes: the operating log ({evidence_id, events}); keep_locations keeps each episode's consistent locations."""
    f = facility.model_dump() if hasattr(facility, 'model_dump') else facility
    sensors = {s['id']: s for s in f['sensors']}
    assets = {a['id']: a for a in f['assets']}
    methane = sorted(s for s in sensors if sensors[s]['variable'] == 'methane')
    slots = defaultdict(dict)
    for r in rows:
        if r['quality_flag'] == 'good' and r['value'] is not None:
            slots[_t(r['timestamp_utc'])][r['sensor_id']] = r['value']
    times = sorted(t for t, v in slots.items() if all(m in v for m in methane))
    if not times:
        return dict(status='no_history', reason='no complete methane slots', episodes=[], assumptions=ASSUMPTIONS)
    interval = min((b - a).total_seconds() for a, b in zip(times, times[1:])) if len(times) > 1 else 300.
    background = {t: min(slots[t][m] for m in methane) for t in times}
    excess = {m: np.array([slots[t][m] - background[t] for t in times]) for m in methane}
    noise = {m: 1.4826 * float(np.median(np.abs(excess[m] - np.median(excess[m])))) for m in methane}
    threshold = {m: max(sensors[m]['detection_limit'], 3 * noise[m]) for m in methane}

    def wind(t):
        v = slots[t]
        return (v.get(next(s for s in sensors if sensors[s]['variable'] == 'wind_speed')),
                v.get(next(s for s in sensors if sensors[s]['variable'] == 'wind_direction')))

    hot = [i for i, t in enumerate(times) if any(excess[m][i] > threshold[m] for m in methane)]
    groups = []
    for i in hot:
        if groups and (times[i] - times[groups[-1][-1]]).total_seconds() <= (MAX_GAP_SLOTS + 1) * interval:
            groups[-1].append(i)
        else:
            groups.append([i])
    episodes = []
    for group in (g for g in groups if len(g) >= MIN_SLOTS):
        idx = range(group[0], group[-1] + 1)
        samples = [(sensors[m], excess[m][i], *wind(times[i])) for i in idx for m in methane]
        samples = [s for s in samples if s[2] is not None and s[3] is not None and s[2] >= .5]
        fits = []
        for aid, a in sorted(assets.items()):
            response = np.array([_response(a['position'], s['position'], u, d) for s, y, u, d in samples]) if samples else np.array([])
            informative = response > .01
            if len({tuple(s['position']) for (s, *_), ok in zip(samples, informative) if ok}) < 2:
                fits.append(dict(candidate_asset_id=aid))
                continue
            y = np.array([v for _, v, _, _ in samples])
            q = max(0., float(response @ y / (response @ response)))
            detected = {s['id'] for (s, v, _, _), r in zip(samples, response) if r > .01 and v > threshold[s['id']]}
            fits.append(dict(candidate_asset_id=aid, rate_kg_h=q, residual_rms_ppm=float(np.sqrt(np.mean((q * response - y) ** 2))),
                             detected_locations=len(detected)))
        peaks = {m: round(float(max(excess[m][i] for i in idx)), 3) for m in methane}
        gate = max(.5, .25 * max(peaks.values()))
        label_fits(fits, 3 * float(np.median([noise[m] for m in methane])), gate)
        consistent = [x for x in fits if x.get('fit_status') in ('best_fit', 'comparable_fit')]
        best = next((x for x in fits if x.get('fit_status') == 'best_fit'), None)
        speeds = [w[0] for w in map(wind, (times[i] for i in idx)) if w[0] is not None]
        directions = [w[1] for w in map(wind, (times[i] for i in idx)) if w[1] is not None]
        start = times[group[0]]
        episodes.append(dict(
            episode_id='EP-' + start.strftime('%Y%m%d-%H%M'), evidence_id='history-episode:EP-' + start.strftime('%Y%m%d-%H%M'),
            start_utc=start.isoformat(), duration_minutes=round(((times[group[-1]] - start).total_seconds() + interval) / 60),
            max_peak_excess_ppm=max(peaks.values()), sensors_above_threshold=sorted(m for m in methane if peaks[m] > threshold[m]),
            mean_wind_speed_m_s=round(float(np.mean(speeds)), 2) if speeds else None,
            mean_wind_from_degrees=round(math.degrees(math.atan2(sum(math.sin(math.radians(d)) for d in directions),
                                                                 sum(math.cos(math.radians(d)) for d in directions))) % 360, 1) if directions else None,
            testable=best is not None, consistent_location_count=len(consistent),
            consistent_locations=sorted(x['candidate_asset_id'] for x in consistent),
            focus_assets={x['candidate_asset_id']: x.get('fit_status') for x in fits if x['candidate_asset_id'] in focus_asset_ids}))

    def observable(aid, t):
        u, d = wind(t)
        return u is not None and d is not None and u >= .5 and any(
            INDICATIVE_KG_H * _response(assets[aid]['position'], sensors[m]['position'], u, d) > threshold[m] for m in methane)

    def fraction(aid, span):
        usable = [t for t in span if wind(t)[0] is not None and wind(t)[0] >= .5]
        return round(sum(observable(aid, t) for t in usable) / len(usable), 3) if usable else None

    focus = [a for a in focus_asset_ids if a in assets]
    note = 'Share of 5-minute slots in which a 50 g/h release from this asset would reach a sensor above threshold'
    observability = [dict(asset_id=a, observable_fraction=fraction(a, times), note=note) for a in focus]
    # Quiet stretches: no episode for several days, with how observable the focus assets were meanwhile.
    edges = [times[0]] + [x for e in episodes for x in (_t(e['start_utc']), _t(e['start_utc']) + timedelta(minutes=e['duration_minutes']))] + [times[-1]]
    quiet = []
    for a, b in zip(edges[::2], edges[1::2]):
        if (b - a).total_seconds() >= QUIET_GAP_DAYS * 86400:
            span = [t for t in times if a < t < b]
            quiet.append(dict(start_utc=a.isoformat(), end_utc=b.isoformat(), days=round((b - a).total_seconds() / 86400, 1),
                              observable_fraction={x: fraction(x, span) for x in focus}))
    testable = [e for e in episodes if e['testable']]
    consistency = defaultdict(int)
    for e in testable:
        for aid in e['consistent_locations']:
            consistency[aid] += 1
    weekly = defaultdict(lambda: dict(episodes=0, max_peak_excess_ppm=0.))
    for e in episodes:
        if not keep_locations:
            e.pop('consistent_locations', None)
        start = _t(e['start_utc'])
        week = weekly[(start - timedelta(days=start.weekday())).date().isoformat()]
        week['episodes'] += 1
        week['max_peak_excess_ppm'] = round(max(week['max_peak_excess_ppm'], e['max_peak_excess_ppm']), 3)
    hours = defaultdict(int)
    for e in episodes:
        hours[_t(e['start_utc']).hour] += 1
    alignment = None
    if load_changes:
        # Minutes since the latest load change at or before each episode start (within the window).
        changes = sorted(_t(c['time_utc']) for c in load_changes['events'])
        for e in episodes:
            start = _t(e['start_utc'])
            before = [c for c in changes if c <= start and (start - c).total_seconds() <= LOAD_CHANGE_WINDOW_MINUTES * 60]
            e['minutes_after_load_change'] = round((start - before[-1]).total_seconds() / 60) if before else None
        aligned = sum(e['minutes_after_load_change'] is not None for e in episodes)
        expected = min(1., len(changes) * LOAD_CHANGE_WINDOW_MINUTES * 60 / max(1., (times[-1] - times[0]).total_seconds()))
        alignment = dict(evidence_id=load_changes['evidence_id'], window_minutes=LOAD_CHANGE_WINDOW_MINUTES,
                         episodes_starting_after_load_change=aligned, episodes=len(episodes), load_changes=len(changes),
                         share_expected_by_chance=round(expected, 3),
                         note='Episodes starting within the window after a K-201 load change, against the share of time those windows cover')
    return dict(status='analyzed', evidence_id=f"history:{SCENARIO_ID}:{times[0].date()}:{times[-1].isoformat()}",
                scenario_id=SCENARIO_ID, period_start_utc=times[0].isoformat(), period_end_utc=times[-1].isoformat(),
                interval_seconds=int(interval), complete_slots=len(times),
                detection_threshold_ppm={m: round(threshold[m], 3) for m in methane},
                episode_count=len(episodes), episodes_by_week=dict(sorted(weekly.items())), load_change_alignment=alignment,
                episode_start_hour_utc=dict(sorted(hours.items())), quiet_stretches=quiet, observability=observability,
                location_consistency=dict(testable_episodes=len(testable),
                                          episodes_consistent_with=dict(sorted(consistency.items(), key=lambda kv: -kv[1])),
                                          note='How many testable episodes each assumed source location explains; '
                                               'nearby locations are rarely separable on 5-minute averages'),
                episodes=episodes, assumptions=ASSUMPTIONS,
                origin='simulated history; wind and background parameters derived from measured METEC 2024 data')


def hourly_series(facility, rows):
    """Hourly maximum excess over the shared background per methane sensor, for charts."""
    f = facility.model_dump() if hasattr(facility, 'model_dump') else facility
    methane = sorted(s['id'] for s in f['sensors'] if s['variable'] == 'methane')
    slots = defaultdict(dict)
    for r in rows:
        if r['quality_flag'] == 'good' and r['value'] is not None and r['sensor_id'] in methane:
            slots[_t(r['timestamp_utc'])][r['sensor_id']] = r['value']
    hours = defaultdict(lambda: dict.fromkeys(methane, 0.))
    for t, v in slots.items():
        if len(v) == len(methane):
            floor = min(v.values())
            hour = hours[t.replace(minute=0, second=0, microsecond=0).isoformat()]
            for m in methane:
                hour[m] = max(hour[m], round(v[m] - floor, 3))
    return dict(unit='ppm above shared background', interval='1h max of 5-minute averages', sensors=methane,
                points=[dict(hour_utc=h, **values) for h, values in sorted(hours.items())])
