"""Client for the Atelic engine (the investigation backend in engine/).

The engine owns the evidence: Neo4j graph, InfluxDB telemetry, Qdrant documents, the PostgreSQL workflow
record and the Nemotron agents. This service only translates its records into the screens' shapes.
The engineer credential stays here, server side; it is never sent to the browser or to a model.
"""
import os
import uuid

import httpx
from fastapi import HTTPException

ENGINE_URL = os.getenv("ENGINE_URL", "http://127.0.0.1:8000").rstrip("/")
ENGINEER_TOKEN = os.getenv("ENGINEER_TOKEN", "")

_client = httpx.Client(base_url=ENGINE_URL, timeout=httpx.Timeout(60.0, connect=5.0))


def _raise(r):
    try:
        detail = r.json().get("detail", r.text)
    except ValueError:
        detail = r.text
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(str(d.get("msg", d)) for d in detail)
    raise HTTPException(status_code=r.status_code if r.status_code < 500 else 502, detail=f"Engine: {detail}")


def get(path, params=None, ok=(200,)):
    try:
        r = _client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Engine unreachable ({type(exc).__name__})") from exc
    if r.status_code not in ok:
        _raise(r)
    return r.json() if r.status_code == 200 else None


def try_get(path, params=None):
    """GET that returns None on 404/409 (record not created yet, answer key still locked)."""
    try:
        r = _client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Engine unreachable ({type(exc).__name__})") from exc
    if r.status_code in (404, 409):
        return None
    if r.status_code != 200:
        _raise(r)
    return r.json()


def post(path, body=None, human=False, timeout=60.0):
    headers = {"Authorization": f"Bearer {ENGINEER_TOKEN}"} if human else {}
    try:
        r = _client.post(path, json=body or {}, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Engine unreachable ({type(exc).__name__})") from exc
    if r.status_code not in (200, 201, 202):
        _raise(r)
    return r.json()


def key(prefix):
    return f"ui-{prefix}-{uuid.uuid4().hex[:12]}"


def stream(path, headers):
    """Open a streaming GET (video bytes with Range support)."""
    req = _client.build_request("GET", path, headers=headers)
    return _client.send(req, stream=True)
