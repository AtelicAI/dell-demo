"""Synthetic plant history before SCN-001: 5-minute methane/wind averages, 2026-09-06 to the incident.

Wind and background variability come from measured METEC 2024 data (scripts/calibrate_history.py);
the leak episodes are a scripted, hidden storyline consistent with the maintenance records:
XV-2104 stem packing weeps during load changes from mid-September (WO-008 sees a faint, intermittent
indication), the WO-009 re-torque quiets it for ten days, then it returns and grows (WO-012 hears
hissing on 14 Oct) until the incident. Episode truth is written to data/ground_truth/ only.
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.contracts import Measurement  # noqa: E402
from app.data import DATA, load_facility  # noqa: E402
from app.dispersion import concentration_ppm  # noqa: E402

SCENARIO_ID = 'HIST-FAC-001'
VERSION = 'history-1'
SLOT = 300
SEED = 2104
SOURCE = 'XV-2104'
LOAD_CHANGES = ((6, 0), (17, 30))  # UTC; the plant's daily load changes
WEEP_G_H = 5.3      # GasVid class 0 metered rate: the camera's below-detection release
INCIDENT_G_H = 277.7  # GasVid class 1 metered rate, used by SCN-001 from 600 s


def schedule(start, end):
    """Hidden episode truth: (start, end, rate_kg_h, note)."""
    rng = random.Random(SEED)
    episodes = []
    day = start.replace(hour=0, minute=0)
    while day < end:
        for hour, minute in LOAD_CHANGES:
            at = day.replace(hour=hour, minute=minute)
            date = at.date().isoformat()
            if '2026-09-15' <= date <= '2026-09-25':
                forced = (date, hour) in (('2026-09-18', 6), ('2026-09-25', 6))  # WO-008 survey morning; last weep before WO-009
                if forced or rng.random() < .7:
                    episodes.append((at + timedelta(minutes=rng.randint(0, 15)), timedelta(minutes=rng.randint(40, 120)),
                                     rng.uniform(8, 20) / 1000, 'packing weep during load change'))
            elif '2026-10-06' <= date <= '2026-10-22':
                if date == '2026-10-14' and hour == 17:
                    episodes.append((at + timedelta(minutes=5), timedelta(minutes=60), .140, 'load change; hissing reported (WO-012)'))
                elif rng.random() < .85:
                    growth = 10 + (at - datetime(2026, 10, 6, tzinfo=timezone.utc)).days / 16 * 70
                    episodes.append((at + timedelta(minutes=rng.randint(0, 15)), timedelta(minutes=rng.randint(45, 150)),
                                     growth * rng.uniform(.8, 1.2) / 1000, 'packing leak during load change, growing after WO-009'))
        day += timedelta(days=1)
    final = datetime(2026, 10, 23, 17, 30, tzinfo=timezone.utc)
    episodes.append((final, end - final, WEEP_G_H / 1000, 'weep from the 17:30 load change, continuing into SCN-001'))
    return [(s, s + d, q, note) for s, d, q, note in episodes if s < end]


def generate():
    facility = load_facility()
    calibration = json.loads((DATA / 'generated/history_calibration.json').read_text())
    wind_cfg, bg = calibration['wind'], calibration['background']
    start, end = datetime.fromisoformat(wind_cfg['target_start_utc']), datetime.fromisoformat(wind_cfg['target_end_utc'])
    source = next(a for a in facility.assets if a.id == SOURCE).position
    methane = [s for s in facility.sensors if s.variable == 'methane']
    met = {s.variable: s for s in facility.sensors if s.variable in ('wind_speed', 'wind_direction')}
    sensors = {s.id: s for s in facility.sensors}
    episodes = schedule(start, end)
    rng = random.Random(SEED + 1)

    def rate(t):
        return sum(q for s, e, q, _ in episodes if s <= t < e)

    slots = int((end - start).total_seconds() // SLOT)
    background = 0.
    count = 0
    with (DATA / 'generated/history.jsonl').open('w') as out:
        for i in range(slots):
            t = start + timedelta(seconds=i * SLOT)
            background = bg['ar1_phi'] * background + rng.gauss(0, bg['ar1_sigma_ppm'])
            level = bg['plant_median_ppm'] + bg['hourly_deviation_ppm'][t.hour] + background
            minutes = [(t + timedelta(minutes=k), wind_cfg['minutes'][i * 5 + k]) for k in range(5)]
            valid = [(m, w) for m, w in minutes if w is not None]
            values = {}
            for s in methane:
                plume = 0.
                for m, (speed, direction) in valid:
                    # The Gaussian form is undefined in calm air; clamp to its 0.5 m/s floor.
                    plume += concentration_ppm(rate(m), s.position[0] - source[0], s.position[1] - source[1], s.position[2],
                                               source[2], max(.5, speed), direction % 360)
                values[s.id] = level + (plume / len(valid) if valid else 0.) + rng.gauss(0, s.uncertainty / math.sqrt(SLOT))
            if valid:
                speed = sum(w[0] for _, w in valid) / len(valid)
                east = sum(math.sin(math.radians(w[1])) for _, w in valid)
                north = sum(math.cos(math.radians(w[1])) for _, w in valid)
                values[met['wind_speed'].id] = speed
                values[met['wind_direction'].id] = math.degrees(math.atan2(east, north)) % 360
            else:
                values[met['wind_speed'].id] = values[met['wind_direction'].id] = None
            for sid, value in values.items():
                s = sensors[sid]
                row = Measurement(observation_id=f'{SCENARIO_ID}:{s.id}:{t.strftime("%Y%m%dT%H%M")}', timestamp_utc=t,
                                  scenario_id=SCENARIO_ID, sensor_id=s.id, asset_id=s.asset_id, unit=s.unit,
                                  value=None if value is None else round(max(0., value), 5),
                                  quality_flag='missing' if value is None else 'good', generator_version=VERSION)
                out.write(row.model_dump_json() + '\n')
                count += 1
    # Operating log: the daily compressor load changes (public plant record, not truth).
    log, day = [], start.replace(hour=0, minute=0)
    while day <= end:
        for (hour, minute), kind, before, after in zip(LOAD_CHANGES, ('ramp_up', 'ramp_down'), (60, 100), (100, 60)):
            at = day.replace(hour=hour, minute=minute)
            if start <= at < end:
                log.append(dict(id='LC-' + at.strftime('%Y%m%d-%H%M'), asset_id='K-201', time_utc=at.isoformat(), kind=kind,
                                load_before_percent=before, load_after_percent=after, duration_minutes=20))
        day += timedelta(days=1)
    (DATA / 'operations/load_changes.json').write_text(json.dumps(dict(
        origin='simulated operating log', asset_id='K-201', evidence_id='operations:K-201:load-change-log', events=log), indent=1) + '\n')
    truth = dict(warning='Generator/validation only; never investigation evidence', source_asset_id=SOURCE,
                 episodes=[dict(start_utc=s.isoformat(), end_utc=e.isoformat(), rate_kg_h=round(q, 5), note=note) for s, e, q, note in episodes])
    (DATA / 'ground_truth/history_truth.json').write_text(json.dumps(truth, indent=2) + '\n')
    # Derived episode table for the graph projection: public data only, same analysis the agents read.
    from app.history import analyze
    rows = [json.loads(line) for line in (DATA / 'generated/history.jsonl').open()]
    derived = analyze(facility, rows, (), json.loads((DATA / 'operations/load_changes.json').read_text()), keep_locations=True)
    (DATA / 'generated/history_episodes.json').write_text(json.dumps(dict(
        origin='derived from public plant history by app.history.analyze', evidence_id=derived['evidence_id'],
        episodes=derived['episodes']), indent=1) + '\n')
    print(f'Wrote {count} history observations over {slots} 5-minute slots; {len(episodes)} hidden episodes')


if __name__ == '__main__':
    generate()
