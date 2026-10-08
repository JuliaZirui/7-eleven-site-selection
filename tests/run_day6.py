"""Day 6 runner: execute the four fixed test-case categories plus the
supplementary suites (scoring/report, multi-source POIs, and the v2
agent-policy suites), then an end-to-end reproducibility run, and log
time / cost / output quality per category.

  - Time: wall-clock per category (perf_counter).
  - Cost: real API calls (always 0 -- network is blocked in every test),
    mocked API calls exercised, LLM tokens (0: the LLM policy is only
    ever run against a fake client here), EUR cost.
  - Output quality: tests passed / failed / errored, with failure detail.

End-to-end check: copy the real database to a temp dir, re-run the
metrics layer and the report with network blocked, and compare every
business-fit / evidence-quality score and the ranking against the
latest recorded analysis_runs row. Same inputs must give the same
outputs -- the reproducibility claim the interview narrative needs.

Writes reports/day6_test_log_<stamp>.json and .md. The real database
is only ever read (copied), never written.

Run:
    python3 tests/run_day6.py
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402  (sets sys.path for src/)

PROJECT_ROOT = helpers.PROJECT_ROOT
REAL_DB = PROJECT_ROOT / "db" / "site_selection.db"
REPORTS_DIR = PROJECT_ROOT / "reports"
SCORE_TOLERANCE = 1e-9

CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("missing_data", "Missing data", "test_missing_data"),
    ("duplicate_records", "Duplicate records", "test_duplicate_records"),
    ("api_failure", "API failure", "test_api_failure"),
    ("overnight_hours", "Overnight-spanning hours", "test_overnight_hours"),
    ("day5_supplementary", "Day 5 scoring & report (supplementary)", "test_day5_scoring"),
    ("multi_source_pois", "Multi-source POI coverage (supplementary)", "test_multi_source_pois"),
    ("agent_policy", "Agent executor & policies (v2)", "test_agent_policy"),
    ("llm_policy", "LLM policy, mocked client (v2)", "test_llm_policy"),
    ("policy_scenarios", "Pre-registered policy scenarios (v2)", "test_policy_scenarios"),
    ("cli", "Entry point, site input & offline demo", "test_cli"),
    ("audit_candidates", "Data audit: corrections switch & candidates", "test_audit_candidates"),
    ("audit_prompt", "Data audit: answer-key leakage guard", "test_audit_prompt"),
    ("audit_llm", "Data audit: LLM auditor, mocked client", "test_audit_llm"),
    ("audit_pipeline", "Data audit: pipeline step, report line, apply", "test_audit_pipeline"),
)


def run_category(module_name: str) -> dict:
    suite = unittest.defaultTestLoader.loadTestsFromName(module_name)
    stream = io.StringIO()
    calls_before = helpers.NETWORK_CALLS["count"]
    start = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=0, buffer=True).run(suite)
    elapsed = time.perf_counter() - start
    problems = [
        {"test": t.id().split(".", 1)[1], "kind": kind, "detail": tb.strip().splitlines()[-1]}
        for kind, items in (("failure", result.failures), ("error", result.errors))
        for t, tb in items
    ]
    return {
        "tests": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "failed": len(result.failures),
        "errors": len(result.errors),
        "seconds": round(elapsed, 3),
        "mocked_api_calls": helpers.NETWORK_CALLS["count"] - calls_before,
        "real_api_calls": 0,
        "llm_tokens": 0,
        "cost_eur": 0.0,
        "problems": problems,
    }


def _blocked(*a, **kw):
    raise helpers.UnmockedNetworkCall("end-to-end run attempted a network call")


def end_to_end_reproducibility() -> dict:
    import metrics_pipeline
    import report

    src = sqlite3.connect(REAL_DB)
    baseline_row = src.execute(
        "SELECT run_id, notes, run_at FROM analysis_runs WHERE report_ref IS NOT NULL ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    src.close()
    if baseline_row is None:
        return {"status": "skipped", "reason": "no recorded analysis_runs row to compare against"}
    baseline_id, notes, baseline_run_at = baseline_row
    # Freshness is judged against the run time; re-scoring "as of now"
    # would differ from the baseline by elapsed time alone.
    baseline_run_at = datetime.fromisoformat(baseline_run_at)
    baseline = json.loads((PROJECT_ROOT / json.loads(notes)["scores_ref"]).read_text())

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "site_selection.db"
        shutil.copy(REAL_DB, db)
        shutil.copytree(PROJECT_ROOT / "db" / "raw" / "agent_log", root / "db" / "raw" / "agent_log")
        shutil.copy(PROJECT_ROOT / "store_hypothesis.md", root / "store_hypothesis.md")

        patches = [
            mock.patch("httpx.get", side_effect=_blocked),
            mock.patch("httpx.post", side_effect=_blocked),
            # metrics keeps the real PROJECT_ROOT: it only *reads* the Day 2 raw LOR files.
            mock.patch.object(report, "PROJECT_ROOT", root),
            mock.patch.object(report, "REPORTS_DIR", root / "reports"),
            mock.patch.object(report, "AGENT_LOG_DIR", root / "db" / "raw" / "agent_log"),
            mock.patch.object(report, "STORE_HYPOTHESIS_PATH", root / "store_hypothesis.md"),
        ]
        for p in patches:
            p.start()
        try:
            out = io.StringIO()
            t0 = time.perf_counter()
            with mock.patch("sys.stdout", out):
                metrics_pipeline.run(db)
                t1 = time.perf_counter()
                report_path = report.run(db, run_at=baseline_run_at)
            t2 = time.perf_counter()
            scores_path = next((root / "reports").glob("site_scores_*.json"))
            rerun = json.loads(scores_path.read_text())
            report_lines = len(report_path.read_text().splitlines())
        finally:
            for p in reversed(patches):
                p.stop()

    diffs = []
    for area_id, base in baseline["results"].items():
        new = rerun["results"][area_id]
        for label, a, b in (
            ("business_fit", base["business_fit"], new["business_fit"]),
            ("evidence_quality", base["evidence_quality"]["score"], new["evidence_quality"]["score"]),
        ):
            if abs(a - b) > SCORE_TOLERANCE:
                diffs.append(f"{area_id}.{label}: {a:.6f} -> {b:.6f}")
    if baseline["sensitivity"]["base_rank"] != rerun["sensitivity"]["base_rank"]:
        diffs.append(f"rank: {baseline['sensitivity']['base_rank']} -> {rerun['sensitivity']['base_rank']}")
    if baseline["sensitivity"]["monte_carlo"] != rerun["sensitivity"]["monte_carlo"]:
        diffs.append("monte_carlo top_share / mean_rank changed")

    return {
        "status": "reproducible" if not diffs else "DIFFERS",
        "baseline_run_id": baseline_id,
        "differences": diffs,
        "metrics_seconds": round(t1 - t0, 3),
        "report_seconds": round(t2 - t1, 3),
        "citations_verified": len(rerun["citations"]),
        "report_lines": report_lines,
        "real_api_calls": 0,
        "llm_tokens": 0,
        "cost_eur": 0.0,
    }


def _markdown(log: dict) -> str:
    lines = [
        "# Day 6 Test Log",
        "",
        f"{log['run_at'][:19]}Z · `python3 tests/run_day6.py` · Python {log['python']}",
        "",
        "| Category | Tests | Passed | Failed | Errors | Time (s) | Mocked API calls | Real API calls | LLM tokens | Cost (EUR) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, label, _ in CATEGORIES:
        c = log["categories"][key]
        lines.append(
            f"| {label} | {c['tests']} | {c['passed']} | {c['failed']} | {c['errors']} | {c['seconds']:.3f} | "
            f"{c['mocked_api_calls']} | {c['real_api_calls']} | {c['llm_tokens']} | {c['cost_eur']:.2f} |"
        )
    t = log["totals"]
    lines.append(
        f"| **Total** | **{t['tests']}** | **{t['passed']}** | **{t['failed']}** | **{t['errors']}** | "
        f"**{t['seconds']:.3f}** | **{t['mocked_api_calls']}** | **0** | **0** | **0.00** |"
    )
    problems = [(k, p) for k, c in log["categories"].items() for p in c["problems"]]
    lines += ["", "## Failures", ""]
    lines += [f"- `{k}` · {p['test']} ({p['kind']}): {p['detail']}" for k, p in problems] or ["None."]

    e = log["end_to_end"]
    lines += ["", "## End-to-end reproducibility", ""]
    if e["status"] == "skipped":
        lines.append(f"Skipped: {e['reason']}.")
    else:
        lines += [
            f"Re-ran metrics → scoring → report on a copy of the real database, network blocked, and compared "
            f"against analysis run #{e['baseline_run_id']}.",
            "",
            f"- Result: **{e['status']}**" + (f" — {'; '.join(e['differences'])}" if e["differences"] else
                                               " — every fit / evidence score, the ranking, and the Monte Carlo shares match exactly"),
            f"- Metrics layer: {e['metrics_seconds']:.3f}s · scoring + sensitivity + report: {e['report_seconds']:.3f}s",
            f"- Citations re-verified: {e['citations_verified']} · report length: {e['report_lines']} lines",
            "- Real API calls: 0 · LLM tokens: 0 · cost: EUR 0.00",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    run_at = datetime.now(timezone.utc)
    log = {"run_at": run_at.isoformat(), "python": sys.version.split()[0], "categories": {}}
    for key, label, module in CATEGORIES:
        log["categories"][key] = run_category(module)
        c = log["categories"][key]
        print(f"{label:<42} {c['passed']}/{c['tests']} passed  {c['seconds']:.3f}s  mocked calls={c['mocked_api_calls']}")

    cats = log["categories"].values()
    log["totals"] = {k: sum(c[k] for c in cats) for k in ("tests", "passed", "failed", "errors", "mocked_api_calls")}
    log["totals"]["seconds"] = round(sum(c["seconds"] for c in cats), 3)

    log["end_to_end"] = end_to_end_reproducibility()
    e = log["end_to_end"]
    print(f"End-to-end reproducibility: {e['status']}" + (f" {e['differences']}" if e.get("differences") else ""))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run_at.strftime("%Y%m%dT%H%M%SZ")
    (REPORTS_DIR / f"day6_test_log_{stamp}.json").write_text(json.dumps(log, indent=2, ensure_ascii=False))
    md_path = REPORTS_DIR / f"day6_test_log_{stamp}.md"
    md_path.write_text(_markdown(log))
    print(f"Log: {md_path.relative_to(PROJECT_ROOT)}")

    ok = log["totals"]["failed"] == 0 and log["totals"]["errors"] == 0 and e["status"] != "DIFFERS"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
