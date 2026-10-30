"""Fetch one Carbon Mapper plume and cache it for the Overview screen.

Run: python3 scripts/fetch_carbon_mapper.py [plume_id [source_name]]
Writes data/carbon_mapper/verified_plume.json and data/carbon_mapper/plume.png.

Values are copied from the public Carbon Mapper API as-is (provenance: provider_reported).
The plume image URL from the API is signed and expires within hours, so the PNG is cached locally
with its black background made transparent for overlay on the satellite basemap. Needs Pillow.
"""
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

API = "https://api.carbonmapper.org/api/v1"
PORTAL = "https://data.carbonmapper.org"
DEFAULT_PLUME = "tan20251209t082620c40s4001-A"  # CH4, oil and gas (1B2), small site in the Eastern Province, Saudi Arabia: regional context only
# Source cluster as linked from the portal; without one, the API picks the plume's 100 m cluster.
DEFAULT_SOURCE = None  # the plume's own 100 m cluster (3 quantified detections)
OUT = Path(__file__).resolve().parent.parent / "data" / "carbon_mapper"

INSTRUMENTS = {"tan": "Tanager-1", "emi": "EMIT", "gao": "GAO", "av3": "AVIRIS-3", "ang": "AVIRIS-NG"}
# IPCC source categories Carbon Mapper uses as sectors.
SECTORS = {"1A1": "Energy industries", "1B2": "Oil and gas", "6A": "Solid waste", "4B": "Livestock",
           "1B1a": "Coal mining"}


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def transparent_png(raw):
    # Carbon Mapper plume PNGs are RGB on black; black = no enhancement detected.
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    im.putdata([(r, g, b, 0 if r + g + b < 24 else 255) for r, g, b, _ in im.getdata()])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def main(plume_id, source_name=None):
    p = json.loads(get(f"{API}/catalog/plume/{plume_id}"))
    src = json.loads(get(f"{API}/catalog/source/{source_name}" if source_name
                         else f"{API}/catalog/source/plume/name/{plume_id}"))
    lon, lat = p["geometry_json"]["coordinates"]
    s = src.get("source") or {}

    record = {
        "_comment": "Fetched from the public Carbon Mapper API by scripts/fetch_carbon_mapper.py. Do not edit by hand; re-run the script.",
        "status": "verified" if p.get("status") == "published" else "pending",
        "provenance": "provider_reported",
        "satellite_observation_id": p["plume_id"],
        "provider": "Carbon Mapper",
        "gas": p["gas"],
        "sector": p.get("sector"),
        "sector_label": SECTORS.get(p.get("sector")),
        "observed_at": p["scene_timestamp"],
        "coordinates": {"lat": lat, "lon": lon},
        "instrument": INSTRUMENTS.get(p["instrument"], p["instrument"]),
        "platform": p.get("platform"),
        "gsd_m": p.get("gsd"),
        "scene_id": p["scene_id"],
        # Null when Carbon Mapper has not published a rate for this plume. Never filled from elsewhere.
        "rate_kg_h": p.get("emission_auto"),
        "uncertainty_kg_h": p.get("emission_uncertainty_auto"),
        "plume_bounds": p["plume_bounds"],  # [west, south, east, north]
        "plume_image": "plume.png",
        "source": {
            "name": src.get("source_name"),
            "rate_kg_h": s.get("emission_auto"),
            "uncertainty_kg_h": s.get("emission_uncertainty_auto"),
            "persistence": s.get("persistence"),
            "overpasses": len(src.get("observation_dates") or []),
            "detections": len(src.get("detection_dates") or []),
            "plumes": len(src.get("plumes") or []),
            # Quantified plumes only, oldest first: the provider's rate history for this source.
            "history": sorted(
                ({"plume_id": q["plume_id"], "observed_at": q["scene_timestamp"],
                  "rate_kg_h": q["emission_auto"], "uncertainty_kg_h": q["emission_uncertainty_auto"]}
                 for q in src.get("plumes") or [] if q.get("emission_auto") is not None),
                key=lambda h: h["observed_at"]),
        },
        "source_url": f"{PORTAL}/?plume_id={p['plume_id']}" + (f"&details={src['source_name']}" if source_name else "")
                      + f"#13.64/{lat:.5f}/{lon:.5f}",
        "api_url": f"{API}/catalog/plume/{p['plume_id']}",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "license": None,
        "license_status": "pending",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "plume.png").write_bytes(transparent_png(get(p["plume_png"])))
    (OUT / "verified_plume.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"{p['plume_id']}: {p['gas']} plume at {lat:.5f}, {lon:.5f}, rate {record['rate_kg_h']}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(*sys.argv[1:3])
    else:
        main(DEFAULT_PLUME, DEFAULT_SOURCE)
