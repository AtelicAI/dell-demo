"""Cache the Overview basemap around the satellite plume for the offline build.

Run: python3 scripts/cache_basemap.py [radius_km [max_zoom]]
Writes frontend/public/tiles/{z}/{y}/{x}.jpg and tiles/basemap.json (bounds, zoom range, attribution) for the
plume in data/carbon_mapper/verified_plume.json. The tiles are not committed (see .gitignore): the offline image build
runs this script, and only the offline build reads them (overview.js, when /status says offline).
"""
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "tiles"
URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ATTRIBUTION = "Imagery © Esri, Maxar, Earthstar Geographics"
MIN_ZOOM = 11


def tile(lat, lon, z):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def fetch(url, ctx, tries=5):
    """The tile server returns the odd 502 under load: retry with a growing pause."""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30, context=ctx) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)


def main(radius_km=4.0, max_zoom=17):
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    rec = json.loads((ROOT / "data" / "carbon_mapper" / "verified_plume.json").read_text())
    lat, lon = rec["coordinates"]["lat"], rec["coordinates"]["lon"]
    dlat, dlon = radius_km / 111.32, radius_km / (111.32 * math.cos(math.radians(lat)))
    south, north, west, east = lat - dlat, lat + dlat, lon - dlon, lon + dlon
    count = 0
    for z in range(MIN_ZOOM, max_zoom + 1):
        x0, y0 = tile(north, west, z)
        x1, y1 = tile(south, east, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                path = OUT / str(z) / str(y) / f"{x}.jpg"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(fetch(URL.format(z=z, y=y, x=x), ctx))
                count += 1
    (OUT / "basemap.json").write_text(json.dumps(dict(
        observation_id=rec["satellite_observation_id"], bounds=[[south, west], [north, east]],
        min_zoom=MIN_ZOOM, max_zoom=max_zoom, attribution=ATTRIBUTION), indent=1) + "\n")
    print(f"{count} tiles for {rec['satellite_observation_id']} in {OUT}")


if __name__ == "__main__":
    main(*(float(a) for a in sys.argv[1:2]), *(int(a) for a in sys.argv[2:3]))
