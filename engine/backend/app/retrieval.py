"""Cited document retrieval via real Qdrant, with disclosed lexical vectors.

This small offline baseline is NOT a pretrained semantic embedding model.
Only canonical example procedures/work orders/field reports are indexed; never truth/download trees.
"""
import hashlib
import math
import os
import re
import uuid
import httpx
from .data import load_documents, load_field_reports, load_json

COLLECTION = "atelic_engineering_lexical_v1"
DIMENSIONS = 512


def lexical_vector(text):
    vector = [0.0] * DIMENSIONS
    for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(v*v for v in vector))
    return [v/norm for v in vector] if norm else vector


class RetrievalUnavailable(RuntimeError):
    pass


class Documents:
    def __init__(self):
        self.client = httpx.Client(base_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6443"),
                                   headers={"api-key": os.environ["QDRANT_API_KEY"]}, timeout=10)

    def close(self):
        self.client.close()

    def health(self):
        try:
            return self.client.get(f"/collections/{COLLECTION}").is_success
        except httpx.HTTPError:
            return False

    def seed(self, facility):
        response = self.client.get(f"/collections/{COLLECTION}")
        if response.status_code == 404:
            self.client.put(f"/collections/{COLLECTION}", json={"vectors": {"size": DIMENSIONS, "distance": "Cosine"}}).raise_for_status()
        else:
            response.raise_for_status()
        entries = []
        for document in load_documents():
            doc_id = document["id"]
            assets = [a.id for a in facility.assets if doc_id in a.document_ids]
            entries.append(dict(document_id=doc_id, title=document["title"], section="complete_example", asset_ids=assets,
                                text=document["text"], source_kind="simulated", operational_approval=False,
                                citation=f"document:{doc_id}#complete_example", kind=document["kind"]))
        for record in load_json("maintenance.json"):
            entries.append(dict(document_id=record["id"], section="record", asset_ids=[record["asset_id"]],
                                text=". ".join(record[k] for k in ("issue", "finding", "action", "follow_up") if record.get(k)),
                                source_kind="simulated", operational_approval=False,
                                citation=f"document:{record['id']}#record", kind="maintenance", date=record["date"]))
        for report in load_field_reports():
            entries.append(dict(document_id=report["id"], title=report["title"], section="report", asset_ids=report["asset_ids"],
                                text=report["text"], source_kind="simulated", operational_approval=False,
                                citation=f"document:{report['id']}#report", kind="field_report", date=report["date"],
                                work_order_id=report["work_order_id"]))
        points = []
        for entry in entries:
            entry["content_sha256"] = hashlib.sha256(entry["text"].encode()).hexdigest()
            entry["retrieval_method"] = "sha256_token_hash_cosine_v1_not_neural"
            points.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, entry["citation"])),
                           "vector": lexical_vector(entry["text"]), "payload": entry})
        self.client.put(f"/collections/{COLLECTION}/points", params={"wait": "true"}, json={"points": points}).raise_for_status()
        return len(points)

    def search(self, query, asset_id, limit=5, kind=None, as_of=None):
        must = [{"key": "asset_ids", "match": {"value": asset_id}}]
        if kind:
            must.append({"key": "kind", "match": {"any": [kind] if isinstance(kind, str) else list(kind)}})
        try:
            response = self.client.post(f"/collections/{COLLECTION}/points/query", json={
                "query": lexical_vector(query), "filter": {"must": must},
                "limit": min(20, max(1, limit)), "with_payload": True,
            })
            response.raise_for_status()
            found = response.json()["result"]["points"]
            result=[{**p["payload"], "retrieval_score": p["score"], "score_kind": "lexical_similarity_not_confidence"}
                    for p in found if not as_of or p["payload"].get("date", "") <= as_of[:10]]
            return sorted(result,key=lambda p:(-p['retrieval_score'],p['document_id']))
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise RetrievalUnavailable("Qdrant document retrieval unavailable") from exc
