"""Derive synthetic-history parameters from measured METEC 2024 data (run with .venv-detector: pandas).

Real data shapes the synthetic plant history; it is never presented as plant measurements:
- wind: the measured METEC minute series (speed unchanged, direction rotated so the dominant sector
  matches the plant scenario's westerly design wind), time-shifted onto the plant calendar;
- background methane: diurnal profile and AR(1) variability of AERIS-2m minutes with no METEC release
  active, re-centred on the plant's 2.0 ppm background.
Release records are used only to exclude release periods; no release rate or location is copied.
"""
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'data/benchmarks/metec2024/cache'
OUT = ROOT / 'data/generated/history_calibration.json'
TARGET_START = '2026-09-06T00:00:00+00:00'
TARGET_END = '2026-10-23T20:00:00+00:00'  # SCN-001 start (scripts/generate.py)
ROTATION = -40.  # degrees added to the measured FROM direction
QUIET_BEFORE, QUIET_AFTER = pd.Timedelta(minutes=5), pd.Timedelta(minutes=20)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    weather = pd.read_parquet(CACHE / 'weather.parquet').dropna(subset=['epoch_minute']).set_index('epoch_minute')
    aeris = pd.read_parquet(CACHE / 'aeris2.parquet').dropna(subset=['epoch_minute'])
    releases = pd.read_csv(ROOT / 'data/Gas-Data/Releases_Information.csv')
    start = pd.Timestamp(TARGET_START)
    minutes = int((pd.Timestamp(TARGET_END) - start).total_seconds() // 60)
    source_start = pd.to_datetime(weather.index.min(), unit='s', utc=True).ceil('D')
    wind = []
    for i in range(minutes):
        epoch = int((source_start + pd.Timedelta(minutes=i)).timestamp())
        if epoch not in weather.index:
            wind.append(None)
            continue
        row = weather.loc[epoch]
        direction = (math.degrees(math.atan2(row.wind_sin, row.wind_cos)) + ROTATION) % 360
        wind.append([round(float(row.wind_speed), 3), round(direction, 1)])
    if sum(w is not None for w in wind) < .9 * minutes:
        raise SystemExit('METEC wind coverage too low for the plant history window')

    # Quiet minutes: no METEC release active from 5 min before start to 20 min after end.
    t = pd.to_datetime(aeris.epoch_minute, unit='s', utc=True)
    active = np.zeros(len(aeris), bool)
    begin = pd.to_datetime(releases.UTCStart, format='%m/%d/%Y %H:%M', utc=True)
    end = pd.to_datetime(releases.UTCEnd, format='%m/%d/%Y %H:%M', utc=True)
    for b, e in zip(begin, end):
        active |= ((t >= b - QUIET_BEFORE) & (t <= e + QUIET_AFTER)).values
    quiet = pd.Series(aeris.methane_ppm.values[~active], index=t[~active])
    median = float(quiet.median())
    hourly = (quiet.groupby(quiet.index.hour).median() - median).reindex(range(24)).fillna(0.)
    slots = quiet.resample('5min').mean().dropna()
    residual = slots - hourly.reindex(slots.index.hour).values - median
    # Robust AR(1) on consecutive quiet slots; trimmed so release tails do not set the noise level.
    pairs = residual.index.to_series().diff() == pd.Timedelta(minutes=5)
    x, y = residual.shift(1)[pairs].values, residual[pairs].values
    keep = (np.abs(x) < np.quantile(np.abs(residual), .95)) & (np.abs(y) < np.quantile(np.abs(residual), .95))
    phi = float(np.clip(np.polyfit(x[keep], y[keep], 1)[0], 0, .99))
    sigma = float(np.std(y[keep] - phi * x[keep]))

    manifest = json.loads((CACHE / 'manifest.json').read_text())
    OUT.write_text(json.dumps(dict(
        method='metec2024-derived-history-parameters-v1',
        note='Parameters derived from measured METEC 2024 data for a synthetic plant history; not plant measurements',
        sources=dict(weather_cache_sha256=sha(CACHE / 'weather.parquet'), aeris2_cache_sha256=sha(CACHE / 'aeris2.parquet'),
                     releases_sha256=sha(ROOT / 'data/Gas-Data/Releases_Information.csv'),
                     cache_manifest=[{k: s[k] for k in ('sensor_id', 'source_file', 'source_sha256')} for s in manifest['sources']]),
        wind=dict(source_start_utc=source_start.isoformat(), target_start_utc=TARGET_START, target_end_utc=TARGET_END,
                  interval_seconds=60, rotation_degrees=ROTATION, speed_scale=1.,
                  basis='Measured METEC 2024 sonic anemometer minute means, time-shifted; direction rotated so the dominant '
                        'sector matches the plant scenario westerly design wind; speeds unchanged',
                  minutes=wind),
        background=dict(plant_median_ppm=2., metec_quiet_median_ppm=median, quiet_minutes=int(len(quiet)),
                        hourly_deviation_ppm=[round(float(v), 4) for v in hourly.values], ar1_phi=phi, ar1_sigma_ppm=sigma,
                        interval_seconds=300,
                        basis='AERIS-2m minutes with no METEC release active (5 min before start to 20 min after end); '
                              'diurnal profile and AR(1) variability re-centred on the plant 2.0 ppm background')),
        separators=(',', ':')))
    print(f'Wrote {OUT}: {minutes} wind minutes, {len(quiet)} quiet methane minutes, phi={phi:.3f}, sigma={sigma:.4f} ppm')


if __name__ == '__main__':
    main()
