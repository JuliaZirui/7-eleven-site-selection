"""Write accepted audit findings to the correction tables (plan §5).

A person reviews comparison/audit_review_queue.csv and sets review_status
to `accepted` or `rejected` (with their name in `reviewer`). This script
appends every accepted row to the correction table its issue type maps to,
then marks it `applied`, so it is never written twice. Scoring reads only
the correction tables; the queue itself never reaches a score.

    python3 src/audit_apply.py            # dry run: what would be written
    python3 src/audit_apply.py --write    # append and mark applied

Mapping (the tables keep their columns; provenance goes into `basis` or
`found_by`):
  company_wide_headcount -> worker_onsite_verified.csv   (needs a proposed range)
  missing_entity         -> worker_onsite_unregistered.csv (needs a range and coordinates)
  fame_vs_attendance     -> venue_attendance_verified.csv (needs visitors_per_year)
  category_error         -> poi_corrections_verified.csv  (exclude the record)
  duplicate              -> poi_corrections_verified.csv  (exclude the record the reviewer names
                            in reviewer_note as `exclude=<external_ref>`)
A row that lacks what its table needs is reported and left `accepted`.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION = PROJECT_ROOT / "db" / "raw" / "calibration"
REVIEW_QUEUE_CSV = PROJECT_ROOT / "comparison" / "audit_review_queue.csv"
TABLES = {
    "company_wide_headcount": CALIBRATION / "worker_onsite_verified.csv",
    "missing_entity": CALIBRATION / "worker_onsite_unregistered.csv",
    "fame_vs_attendance": CALIBRATION / "venue_attendance_verified.csv",
    "category_error": CALIBRATION / "poi_corrections_verified.csv",
    "duplicate": CALIBRATION / "poi_corrections_verified.csv",
}


class ApplyError(ValueError):
    pass


def _run_record(row: dict) -> dict:
    return json.loads((PROJECT_ROOT / row["run_file"]).read_text())


def _worklist_record(row: dict) -> dict:
    """The candidate's record from the worklist the run audited (pipeline
    runs keep it next to the run; evaluation runs use the committed one)."""
    run = _run_record(row)
    area = run["node"]["area_id"]
    for d in (PROJECT_ROOT / "db" / "raw" / "audit" / "pipeline_worklists", PROJECT_ROOT / "comparison" / "audit_runs" / "worklists"):
        for p in sorted(d.glob(f"{area}*.json"), reverse=True) if d.exists() else []:
            wl = json.loads(p.read_text())
            c = next((c for c in wl["candidates"] if c["candidate_id"] == row["candidate_id"]), None)
            if c is not None:
                return c
    return {}


def _finding(row: dict) -> dict:
    run = _run_record(row)
    return next(f for f in run["findings"] + run["new_entities"] if f["candidate_id"] == row["candidate_id"])


def _range(row: dict, unit: str) -> tuple[float, float, float]:
    if row["proposed_unit"] != unit or not row["proposed_central"]:
        raise ApplyError(f"needs a proposed range in {unit}")
    return float(row["proposed_low"]), float(row["proposed_central"]), float(row["proposed_high"])


def _basis(row: dict) -> str:
    who = row["reviewer"].strip() or "unnamed reviewer"
    note = f" Reviewer: {row['reviewer_note'].strip()}" if row["reviewer_note"].strip() else ""
    return f"{row['reason']} [added by audit_llm ({row['queue_id']}), reviewed by {who}]{note}"


def _sources(row: dict) -> str:
    return " ".join(s["url"] for s in json.loads(row["sources"] or "[]"))


def table_row(row: dict) -> tuple[Path, dict, str]:
    """(table, row to append, key that must not already be in the table)."""
    issue = row["issue_type"]
    if issue not in TABLES:
        raise ApplyError(f"issue type {issue!r} has no correction table")
    table = TABLES[issue]
    if issue == "company_wide_headcount":
        rec = _worklist_record(row).get("record", {})
        low, mid, high = _range(row, "persons_on_site")
        oid = row["record_ref"].removeprefix("ihk:")
        return table, {"opendata_id": oid, "area_id": row["area_id"], "registered_class": rec.get("employees_range", ""),
                       "identified_as": "", "identification": "", "onsite_low": low, "onsite_central": mid,
                       "onsite_high": high, "confidence": "audit", "basis": _basis(row), "sources": _sources(row),
                       "station_check": "", "weekend_share": "", "weekend_basis": ""}, oid
    if issue == "missing_entity":
        f = _finding(row)
        rec = _worklist_record(row).get("record", {})
        lat, lon = f.get("lat", rec.get("lat")), f.get("lon", rec.get("lon"))
        if lat is None or lon is None:
            raise ApplyError("needs coordinates")
        low, mid, high = _range(row, "persons_on_site")
        name = f.get("name") or rec.get("name") or row["record_ref"]
        return table, {"name": name, "area_id": row["area_id"], "lat": lat, "lon": lon, "onsite_low": low,
                       "onsite_central": mid, "onsite_high": high, "confidence": "audit", "basis": _basis(row),
                       "sources": _sources(row), "weekend_share": "", "weekend_basis": ""}, name
    if issue == "fame_vs_attendance":
        title = (_worklist_record(row).get("record", {}).get("wikipedia_titles") or {}).get("dewiki")
        if not title:
            raise ApplyError("needs the venue's German Wikipedia title")
        low, mid, high = _range(row, "visitors_per_year")
        return table, {"de_title": title, "annual_low": low, "annual_central": mid, "annual_high": high,
                       "basis": _basis(row), "sources": _sources(row)}, title
    refs = row["record_ref"].split("|")
    if issue == "duplicate":
        m = re.search(r"exclude=(\S+)", row["reviewer_note"])
        if not m or m.group(1) not in refs:
            raise ApplyError(f"duplicate: name the record to drop in reviewer_note as exclude=<one of {refs}>")
        refs = [m.group(1)]
    if len(refs) != 1:
        raise ApplyError("category error on a pair: name the record in reviewer_note as exclude=<ref>")
    return table, {"external_ref": refs[0], "area_id": row["area_id"], "name": "", "action": "exclude",
                   "reason": _basis(row), "evidence": _sources(row), "found_by": "audit_llm",
                   "reviewed_on": date.today().isoformat()}, refs[0]


def _key_column(table: Path) -> str:
    with table.open(encoding="utf-8") as f:
        return next(csv.reader(f))[0]


def apply(write: bool = False, queue: Path = REVIEW_QUEUE_CSV) -> list[str]:
    with queue.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        columns = list(rows[0].keys()) if rows else []
    report = []
    for row in rows:
        if row["review_status"] != "accepted":
            continue
        try:
            table, new, key = table_row(row)
        except (ApplyError, StopIteration, FileNotFoundError) as exc:
            report.append(f"left accepted  {row['queue_id']}: {exc}")
            continue
        with table.open(encoding="utf-8") as f:
            existing = {r[_key_column(table)] for r in csv.DictReader(f)}
        if key in existing:
            report.append(f"already in {table.name}  {row['queue_id']} ({key}); marked applied")
        else:
            report.append(f"{'append' if write else 'would append'} to {table.name}  {row['queue_id']} ({key})")
            if write:
                with table.open(encoding="utf-8") as f:
                    header = next(csv.reader(f))
                text = table.read_text(encoding="utf-8")
                with table.open("a", newline="", encoding="utf-8") as f:
                    if text and not text.endswith("\n"):
                        f.write("\n")  # never glue the new row onto the last one
                    csv.DictWriter(f, fieldnames=header).writerow(new)
        if write:
            row["review_status"] = "applied"
    if write:
        with queue.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
    return report


if __name__ == "__main__":
    lines = apply(write="--write" in sys.argv[1:])
    print("\n".join(lines) if lines else "No accepted rows in the review queue.")
