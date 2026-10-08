"""Pipeline step: data-plausibility audit (plan_llm_data_audit.md, Phase 5).

Adopted by the pre-registered rule (comparison/audit_findings.md): before
scoring, every candidate site's raw inputs go through the candidate
generator and the frozen LLM auditor. The step changes no number. Its
output is a run record per site (db/raw/audit/pipeline_runs/) and rows
in the review queue (comparison/audit_review_queue.csv); a person
accepts or rejects each row, and src/audit_apply.py writes accepted rows
to the correction tables that scoring reads. The report's §6 says per
site how many flags are still waiting for review.

Skipped, with a warning and a "not run" line in the report, when no
Claude API credentials are available (ANTHROPIC_API_KEY in the shell or
the git-ignored .env) or with `refresh --no-audit`. Each refresh may
spend at most MAX_USD_PER_REFRESH at list prices; pipeline runs do not
count towards the evaluation budget of audit_llm.TOTAL_BUDGET_USD.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
PIPELINE_RUN_DIR = PROJECT_ROOT / "db" / "raw" / "audit" / "pipeline_runs"
PIPELINE_WORKLIST_DIR = PROJECT_ROOT / "db" / "raw" / "audit" / "pipeline_worklists"
REVIEW_QUEUE_CSV = PROJECT_ROOT / "comparison" / "audit_review_queue.csv"

# About $1 per site with the frozen prompt; a refresh of the three
# example sites cost $2-3 in the evaluation.
MAX_USD_PER_REFRESH = 5.00


def run(db_path: Path = DB_PATH, enabled: bool = True) -> dict:
    """Audit every candidate site. Returns per-site results; a site that
    fails carries an 'error' (the CLI lists it and goes on)."""
    if not enabled:
        print("audit disabled (--no-audit)")
        return {"status": "disabled"}
    import audit_candidates as ac
    import audit_llm
    from demand_model import latest_visitor_fit
    from models import CANDIDATE_NODES

    audit_llm.load_dotenv()
    try:
        audit_llm.preflight(audit_llm.Auditor(max_usd=0).client)
    except (audit_llm.AuditError, ImportError) as exc:
        reason = str(exc).split(". ")[0]
        print(f"SKIPPED audit: {reason}")
        return {"status": "skipped", "error": f"audit not run: {reason}"}

    fit, fit_path = latest_visitor_fit()
    nearest = ac._nearest_station_fn()
    left = MAX_USD_PER_REFRESH
    results = []
    for node in CANDIDATE_NODES:
        try:
            audit_llm.check_allowed(node.area_id)
            if left <= 0:
                raise audit_llm.AuditError(f"refresh budget ${MAX_USD_PER_REFRESH:.2f} used up")
            ctx = ac.candidate_context(node, db_path, fetch=True)
            wl = ac.build_worklist(ctx, fit, nearest)
            wl["provenance"]["visitor_fit"] = fit_path.name
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            PIPELINE_WORKLIST_DIR.mkdir(parents=True, exist_ok=True)
            (PIPELINE_WORKLIST_DIR / f"{node.area_id}_{stamp}.json").write_text(
                json.dumps(wl, ensure_ascii=False, indent=1))
            auditor = audit_llm.Auditor(max_usd=left)  # one per site: its record carries its own cost
            record = auditor.audit(wl)
            if not audit_llm.reached_api(record):
                raise audit_llm.AuditError("no API request got through")
            path = audit_llm.save_run(record, wl, run_dir=PIPELINE_RUN_DIR, queue=REVIEW_QUEUE_CSV)
            left -= record["cost_usd"]
            flags = sum(1 for f in record["findings"] + record["new_entities"] if f["verdict"] != "plausible")
            print(f"[{node.area_id}] {len(wl['candidates'])} candidates, {flags} flags to review, "
                  f"${record['cost_usd']:.2f} -> {path.relative_to(PROJECT_ROOT)}")
            results.append({"area_id": node.area_id, "flags": flags, "cost_usd": record["cost_usd"],
                            "run_file": str(path.relative_to(PROJECT_ROOT))})
        except Exception as exc:  # noqa: BLE001 -- one site's failure must not stop the others
            print(f"SKIPPED [{node.area_id}] audit: {type(exc).__name__}: {exc}")
            results.append({"area_id": node.area_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"status": "ran", "spent_usd": round(MAX_USD_PER_REFRESH - left, 4), "sites": results}


# --------------------------------------------------------------------------
# Report line
# --------------------------------------------------------------------------


def latest_run(area_id: str, run_dir: Path | None = None) -> tuple[dict, str] | None:
    run_dir = run_dir or PIPELINE_RUN_DIR
    files = sorted(run_dir.glob(f"{area_id}_2*.json")) if run_dir.exists() else []
    if not files:
        return None
    path = files[-1]
    try:
        ref = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        ref = str(path)
    return json.loads(path.read_text()), ref


def summary_line(area_id: str, run_dir: Path | None = None, queue: Path | None = None) -> str:
    """One line for the report's §6: what the latest audit flagged for
    this site and how much of it a person has reviewed."""
    queue = queue or REVIEW_QUEUE_CSV
    found = latest_run(area_id, run_dir)
    if found is None:
        return ("AI data audit: not run for this site — its raw inputs (registrations, visitor conversions, "
                "competitor records) have not been checked for plausibility.")
    run, ref = found
    rows = []
    if queue.exists():
        with queue.open(encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["run_file"] == ref]
    status = {s: sum(1 for r in rows if r["review_status"] == s) for s in ("accepted", "applied", "rejected", "pending")}
    when = run["audited_at"][:10]
    return (f"AI data audit ({when}, prompt `{run['prompt_sha256'][:12]}`): {len(rows)} flag(s) — "
            f"{status['accepted'] + status['applied']} accepted, {status['rejected']} rejected, "
            f"{status['pending']} pending review in `comparison/audit_review_queue.csv`. The audit only sees "
            f"records its candidate rules list; its recall outside them is low (comparison/audit_findings.md).")


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False))
