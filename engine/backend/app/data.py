import json
from pathlib import Path
from .contracts import Facility
DATA = Path(__file__).resolve().parents[2] / "data"
# Override permits a runtime mount containing only public data.
import os
DATA = Path(os.environ.get("ATELIC_DATA_DIR", DATA))
def load_json(path):
    return json.loads((DATA / path).read_text())
def load_facility():
    header = load_json("facility/facility.json")
    return Facility.model_validate({**header, **{k:load_json(f"facility/{k}.json") for k in ["assets","connections","cameras","sensors"]}})
DOCUMENT_KINDS = {"PROC": "procedure", "DS": "datasheet", "MAN": "manual", "PID": "drawing"}
def load_documents():
    """Canonical synthetic markdown documents with ID, title and kind from the file itself."""
    documents = []
    for path in sorted((DATA / "documents").glob("*.md")):
        text = path.read_text()
        title = text.splitlines()[0].lstrip("# ").split(": ", 1)[-1]
        documents.append(dict(id=path.stem, title=title, kind=DOCUMENT_KINDS[path.stem.split("-")[0]], text=text))
    return documents
FIELD_REPORT_FIELDS = {"Report type": "report_type", "Work order": "work_order_id", "Assets": "asset_ids",
                       "Date": "date", "Time (UTC)": "time_utc", "Author role": "author_role"}
def load_field_reports():
    """Synthetic field reports (survey, work sheet, round log); metadata from the header lines of each file."""
    reports = []
    for path in sorted((DATA / "field_reports").glob("*.md")):
        text = path.read_text()
        report = dict(id=path.stem, title=text.splitlines()[0].lstrip("# ").split(": ", 1)[-1], text=text)
        for line in text.splitlines():
            key, _, value = line.partition(": ")
            if key in FIELD_REPORT_FIELDS:
                report[FIELD_REPORT_FIELDS[key]] = [v.strip() for v in value.split(",")] if key == "Assets" else value.strip()
        if set(FIELD_REPORT_FIELDS.values()) - report.keys():
            raise ValueError(f"field report {path.stem} is missing header fields")
        report["body"] = text.split("\n\n", 2)[-1].split("\n\n", 1)[-1].strip()
        reports.append(report)
    return reports
