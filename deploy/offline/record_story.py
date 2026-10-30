"""Record every agent step the offline demo can reach, by playing the story through the UI API once, live.

Run against a stack that has the NVIDIA key and records (docker-compose.yml, MODEL_REPLAY on):
    python3 deploy/offline/record_story.py http://localhost:8099 <login-password>
For each incident (INC-001, then INC-002) it triggers the alert, waits for the draft, asks every scripted question,
requests changes with the scripted reason, approves the revision, records a repair, waits for the verifier, asks the
verifier questions and closes. The scripted texts are read from frontend/public/js/story.js, so the recordings are
keyed by exactly what the offline buttons send. Afterwards copy the new recordings into engine/data/recorded_runs:
    docker compose -p <project> cp engine:/srv/recordings/. engine/data/recorded_runs/
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

STORY = Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "story.js"
REVIEWER = "Demo engineer"
REPAIR = ("OGI re-survey requested in WO-008 found an indication at the XV-2104 stem packing; the authorized crew repacked "
          "the stem per DS-XV-2104 and a follow-up OGI survey of XV-2104 and F-205 showed no indication.")


def scripted(name):
    block = re.search(rf"const {name} = \[(.*?)\];", STORY.read_text(), re.S).group(1)
    return re.findall(r"'([^']*)'", block)  # the scripted texts contain no quotes


class UI:
    def __init__(self, base, password):
        self.base = base.rstrip("/")
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.post("/login", {"username": "atelic", "password": password})

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + "/api" + path, method=method, headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.http.open(req, timeout=60) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:500]!r}")

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def wait(self, what, fetch, done, timeout=1200):
        started = time.time()
        while time.time() - started < timeout:
            value = fetch()
            if done(value):
                print(f"  {what}: {round(time.time() - started)} s", flush=True)
                return value
            time.sleep(5)
        raise SystemExit(f"Timed out waiting for {what}")


def answered(w):
    return w["questions"] and all(q["status"] == "answered" for q in w["questions"])


def play(ui, inc, chips, verify_chips, reason):
    print(f"\n{inc}", flush=True)
    wo = lambda: ui.get(f"/incidents/{inc}/work-order")
    ui.post(f"/incidents/{inc}/trigger", {})
    w = ui.wait("first draft", wo, lambda w: w["versions"] and w.get("engine_state") == "pending_review", 1800)
    w = ui.post(f"/incidents/{inc}/work-order/edit", {"base_version": w["versions"][-1]["version"], "fields": {"priority": "high"}, "by": REVIEWER})
    w = ui.post(f"/incidents/{inc}/work-order/submit", {"version": w["versions"][-1]["version"], "by": REVIEWER})
    for q in chips:
        ui.post(f"/incidents/{inc}/questions", {"question": q, "asked_by": REVIEWER})
        w = ui.wait(f"answer: {q[:50]}", wo, answered)
    sub = w["submitted"]
    ui.post(f"/incidents/{inc}/review", {"decision": "request_changes", "reviewer": REVIEWER, "reason": reason,
                                          "version": sub["version"], "hash": sub["hash"]})
    before = len(w["versions"])
    w = ui.wait("revised draft", wo, lambda w: len(w["versions"]) > before and w.get("engine_state") == "pending_review")
    if not (w.get("draft") or {}).get("fields", {}).get("priority"):
        w = ui.post(f"/incidents/{inc}/work-order/edit", {"base_version": w["versions"][-1]["version"], "fields": {"priority": "high"}, "by": REVIEWER})
    w = ui.post(f"/incidents/{inc}/work-order/submit", {"version": w["versions"][-1]["version"], "by": REVIEWER})
    sub = w["submitted"]
    ui.post(f"/incidents/{inc}/review", {"decision": "approve", "reviewer": REVIEWER, "version": sub["version"], "hash": sub["hash"]})
    ui.post(f"/incidents/{inc}/repair", {"by": REVIEWER, "work_performed": REPAIR})
    report = lambda: ui.get(f"/incidents/{inc}/report")
    ui.wait("verifier", report, lambda r: r["follow_up"]["state"] == "verification_ready")
    for q in verify_chips:
        ui.post(f"/incidents/{inc}/questions", {"question": q, "asked_by": REVIEWER})
        ui.wait(f"answer: {q[:50]}", report, lambda r: answered(r["follow_up"]))
    # Every agent step must come from the model (live or replayed), never the labelled fallback template.
    r, w = report(), wo()
    steps = [("verifier", (r["follow_up"].get("verification") or {}).get("source_origin"))]
    steps += [(q["question"][:50], q.get("source_origin")) for q in w["questions"] + r["follow_up"]["questions"]]
    bad = [s for s in steps if s[1] != "llm"]
    print(f"  agent steps from the model: {len(steps) - len(bad)} of {len(steps)}" + (f"; fallback: {bad}" if bad else ""), flush=True)
    ui.post(f"/incidents/{inc}/close", {"decision": "close", "by": REVIEWER, "rationale": "Verifier and post-repair readings agree."})


def main(base, password):
    ui = UI(base, password)
    ui.post("/demo/reset", {})  # start from a clean slate; steps already recorded replay
    chips, verify_chips, reason = scripted("CHIPS"), scripted("VERIFY_CHIPS"), scripted("REVIEW_REASONS")[0]
    for inc in ("INC-001", "INC-002"):
        play(ui, inc, chips, verify_chips, reason)
    print("\nDone. Recordings:", json.dumps(ui.get("/status").get("services"), indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
