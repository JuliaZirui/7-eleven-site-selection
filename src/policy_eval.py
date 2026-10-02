"""Run the pre-registered scenarios (policy_scenarios.py) for one or more
follow-up policies and write the comparison.

    python3 src/policy_eval.py --policies rule impact_rule
    python3 src/policy_eval.py --policies llm --repeats 5            # Claude API, costs money
    python3 src/policy_eval.py --policies llm --scenarios S01 S09 --repeats 1   # cost probe
    python3 src/policy_eval.py --report-only [--grades comparison/policy_questions_graded.csv]

Every run uses a fresh in-memory database built from db/schema.sql and
simulated live tools, so no data API is called and the real database
is never touched. Raw results go to comparison/policy_eval_runs/ (one
file per policy and invocation); the report always uses the newest
file per policy.

Field questions are exported for blind grading: policy_questions_blind.csv
has no policy label and a shuffled order; the key is in
policy_questions_key.csv. Fill the `score` column (0-3, rubric in
policy_scenarios.md), save as policy_questions_graded.csv, and rerun
with --report-only --grades.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_followup import TOOL_FLAG_FIELD_RESEARCH, CallBudget  # noqa: E402
from agent_policy import LIVE_TOOLS, ImpactRulePolicy, RulePolicy, run_agent  # noqa: E402
from models import CANDIDATE_NODES  # noqa: E402
from policy_scenarios import SCENARIOS, Scenario, check  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = PROJECT_ROOT / "db" / "schema.sql"
OUT_DIR = PROJECT_ROOT / "comparison"
RUNS_DIR = OUT_DIR / "policy_eval_runs"
DO_NOTHING = ("S07", "S15")
POLICY_LABELS = {"rule": "A. Rule", "impact_rule": "B. Impact rule", "llm": "C. LLM"}


def _policy(name: str):
    if name == "rule":
        return RulePolicy()
    if name == "impact_rule":
        return ImpactRulePolicy()
    if name == "llm":
        from agent_llm_policy import LlmPolicy

        return LlmPolicy()
    raise ValueError(name)


def _tools(s: Scenario) -> dict:
    def make(tool):
        def f(conn, node):
            if tool in s.failing_tools:
                return {"status": "error", "error": "simulated outage"}
            return {"status": "ok", "detail": {"simulated": True, "tool": tool, "area_id": node.area_id}}
        return f
    return {t: make(t) for t in LIVE_TOOLS}


def run_scenario(s: Scenario, policy_name: str) -> dict:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    try:
        out = run_agent(conn, list(CANDIDATE_NODES), CallBudget(*s.budget), _policy(policy_name),
                        context=s.context, gaps=list(s.gaps), tool_funcs=_tools(s))
    finally:
        conn.close()
    verdict = check(s, out["log"], out["run"])
    first = out["log"][0] if out["log"] else None
    record = out["run"].get("policy_record") or {}
    return {
        "scenario": s.sid,
        **verdict,
        "first_action": [first["gap_id"], first["tool"].split("+")[0]] if first else ["finish", None],
        "finish_reason": out["run"]["finish_reason"],
        "policy_fallback": out["run"]["policy_fallback"],
        "questions": {e["gap_id"]: _question(e) for e in out["log"] if e["gap_id"] in s.graded},
        "cost_usd": record.get("cost_usd"),
        "latency_s": record.get("latency_s"),
        "usage": record.get("usage"),
        "log": out["log"],
        "run": out["run"],
    }


def _question(entry: dict) -> str | None:
    r = entry["result"]
    return r.get("question") or (r.get("fallback") or {}).get("question")


def evaluate(policy_name: str, scenarios: list[Scenario], repeats: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs = []
    for s in scenarios:
        for rep in range(repeats):
            r = run_scenario(s, policy_name)
            r["repeat"] = rep
            runs.append(r)
            cost = f"  ${r['cost_usd']:.3f} {r['latency_s']:.1f}s" if r["cost_usd"] is not None else ""
            print(f"{policy_name:<12} {s.sid} #{rep}  {'agree' if r['agree'] else 'DISAGREE'}"
                  f"  live={r['live_calls']} rej={r['guard_rejections']}{cost}"
                  + (f"  ({'; '.join(r['failures'])})" if r["failures"] else "")
                  + (f"\n    !! FELL BACK TO RULES -- not the model's decision: {r['policy_fallback'][:200]}"
                     if r["policy_fallback"] else ""), flush=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{policy_name}_{stamp}.json"
    path.write_text(json.dumps({"policy": policy_name, "stamp": stamp, "repeats": repeats,
                                "scenarios": [s.sid for s in scenarios], "runs": runs},
                               ensure_ascii=False, indent=1, default=str))
    print(f"-> {path.relative_to(PROJECT_ROOT)}")
    return path


# ---- report ------------------------------------------------------------------


def _latest_results() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for p in sorted(RUNS_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        latest[data["policy"]] = {**data, "file": p.name}
    return latest


def _load_grades(path: Path | None) -> dict[tuple[str, str, str, int], int]:
    if not path or not path.exists():
        return {}
    key = {r["item_id"]: r for r in csv.DictReader((OUT_DIR / "policy_questions_key.csv").open())}
    grades = {}
    for row in csv.DictReader(path.open()):
        if row.get("score", "").strip():
            k = key[row["item_id"]]
            grades[(k["policy"], k["scenario"], k["gap_id"], int(k["repeat"]))] = int(row["score"])
    return grades


def export_questions(results: dict[str, dict]) -> int:
    items = []
    for policy, data in results.items():
        gap_text = {s.sid: {f"g{i + 1}": g.detail for i, g in enumerate(s.gaps)} for s in SCENARIOS}
        for r in data["runs"]:
            for gap_id, q in r["questions"].items():
                items.append({"policy": policy, "scenario": r["scenario"], "gap_id": gap_id, "repeat": r["repeat"],
                              "gap": gap_text[r["scenario"]][gap_id], "question": q or ""})
    random.Random(20261002).shuffle(items)
    with (OUT_DIR / "policy_questions_blind.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "scenario", "gap", "question", "score"])
        for i, it in enumerate(items):
            w.writerow([f"q{i + 1:03d}", it["scenario"], it["gap"], it["question"], ""])
    with (OUT_DIR / "policy_questions_key.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "policy", "scenario", "gap_id", "repeat"])
        for i, it in enumerate(items):
            w.writerow([f"q{i + 1:03d}", it["policy"], it["scenario"], it["gap_id"], it["repeat"]])
    return len(items)


def write_report(grades_path: Path | None = None) -> Path:
    results = _latest_results()
    order = [p for p in ("rule", "impact_rule", "llm") if p in results]
    n_questions = export_questions(results)
    grades = _load_grades(grades_path)
    sids = [s.sid for s in SCENARIOS]

    def agg(policy):
        runs = results[policy]["runs"]
        by_s: dict[str, list] = {}
        for r in runs:
            by_s.setdefault(r["scenario"], []).append(r)
        agree_rate = {sid: sum(r["agree"] for r in rs) / len(rs) for sid, rs in by_s.items()}
        main = [sid for sid in by_s if sid not in DO_NOTHING]
        consistency = {sid: Counter(tuple(r["first_action"]) for r in rs).most_common(1)[0][1] / len(rs)
                       for sid, rs in by_s.items()}
        g = [v for (p, *_), v in grades.items() if p == policy]
        costs = [r["cost_usd"] for r in runs if r["cost_usd"] is not None]
        lat = [r["latency_s"] for r in runs if r["latency_s"] is not None]
        reps = results[policy]["repeats"]
        return {
            "by_s": by_s, "agree_rate": agree_rate,
            "agree_total": sum(agree_rate.values()), "n": len(by_s),
            "agree_main": sum(agree_rate[s] for s in main), "n_main": len(main),
            "agree_do_nothing": sum(agree_rate.get(s, 0) for s in DO_NOTHING if s in by_s),
            "live": sum(r["live_calls"] for r in runs) / reps,
            "wasted": sum(r["wasted_live_calls"] for r in runs) / reps,
            "rejections": sum(r["guard_rejections"] for r in runs) / reps,
            "fallbacks": sum(1 for r in runs if r["policy_fallback"]),
            "runs": len(runs), "repeats": reps,
            "consistency": (sum(consistency.values()) / len(consistency)) if reps > 1 else None,
            "question_mean": (sum(g) / len(g)) if g else None, "graded": len(g),
            "cost_per_run": (sum(costs) / len(costs)) if costs else None, "cost_total": sum(costs) if costs else None,
            "latency_per_run": (sum(lat) / len(lat)) if lat else None,
        }

    A = {p: agg(p) for p in order}
    L = []
    L.append("# Follow-up policy evaluation")
    L.append("")
    L.append(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `python3 src/policy_eval.py`. "
             "Scenarios, expected decisions and the rubric were pre-registered in "
             "[policy_scenarios.md](policy_scenarios.md) before any policy ran. Live tools are simulated, so no "
             "data API was called.")
    L.append("")
    L.append("Result files: " + ", ".join(f"`policy_eval_runs/{results[p]['file']}`" for p in order) + ".")
    if "llm" not in A:
        L.append("")
        L.append("**Policy C (LLM) has not been run yet.** This table compares the two rule baselines only.")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| Metric | " + " | ".join(POLICY_LABELS[p] for p in order) + " |")
    L.append("| --- | " + " | ".join("---" for _ in order) + " |")

    def row(label, fn):
        L.append(f"| {label} | " + " | ".join(fn(A[p]) for p in order) + " |")

    row("Runs per scenario", lambda a: str(a["repeats"]))
    row("Agreement, all scenarios", lambda a: f"{a['agree_total']:.1f} / {a['n']}")
    row("…excluding do-nothing pair (S07, S15)", lambda a: f"{a['agree_main']:.1f} / {a['n_main']}")
    row("…do-nothing pair only", lambda a: f"{a['agree_do_nothing']:.1f} / {len(DO_NOTHING)}")
    row("Live calls per pass over all scenarios", lambda a: f"{a['live']:.1f}")
    row("…of which wasted", lambda a: f"{a['wasted']:.1f}")
    row("Guard rejections per pass", lambda a: f"{a['rejections']:.1f}")
    row("Runs handed to rules after an API failure", lambda a: str(a["fallbacks"]))
    row("Consistency of first decision", lambda a: f"{a['consistency']:.0%}" if a["consistency"] is not None else "n/a (1 run)")
    row("Field-question score (0–3, blind)", lambda a: f"{a['question_mean']:.2f} (n={a['graded']})" if a["question_mean"] is not None else "not graded yet")
    row("Cost per scenario run", lambda a: f"${a['cost_per_run']:.3f}" if a["cost_per_run"] is not None else "$0 (no model)")
    row("Latency per scenario run", lambda a: f"{a['latency_per_run']:.1f} s" if a["latency_per_run"] is not None else "< 0.01 s")
    L.append("")
    L.append("Agreement with several runs per scenario counts the share of runs that agree, so it can be fractional.")
    L.append("")
    L.append("## Per scenario")
    L.append("")
    L.append("| # | Scenario | " + " | ".join(POLICY_LABELS[p] for p in order) + " |")
    L.append("| --- | --- | " + " | ".join("---" for _ in order) + " |")
    titles = {s.sid: s.title for s in SCENARIOS}
    for sid in sids:
        cells = []
        for p in order:
            rs = A[p]["by_s"].get(sid)
            if not rs:
                cells.append("–")
                continue
            ok = sum(r["agree"] for r in rs)
            mark = "✓" if ok == len(rs) else ("✗" if ok == 0 else f"{ok}/{len(rs)}")
            fails = sorted({f for r in rs for f in r["failures"]})
            cells.append(mark + (f" ({'; '.join(fails)})" if fails else ""))
        L.append(f"| {sid} | {titles[sid]} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## Field questions")
    L.append("")
    L.append(f"{n_questions} graded questions exported to `policy_questions_blind.csv` (shuffled, no policy label; "
             "key in `policy_questions_key.csv`). " + (f"{len(grades)} graded so far." if grades else "Not graded yet."))
    path = OUT_DIR / "policy_eval.md"
    path.write_text("\n".join(L) + "\n")
    print(f"-> {path.relative_to(PROJECT_ROOT)}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--policies", nargs="+", choices=["rule", "impact_rule", "llm"], default=["rule", "impact_rule"])
    ap.add_argument("--scenarios", nargs="+", help="subset, e.g. S01 S09 (default: all)")
    ap.add_argument("--repeats", type=int, help="runs per scenario (default 1 for rules, 5 for llm)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--grades", type=Path, help="graded copy of policy_questions_blind.csv")
    args = ap.parse_args()

    if not args.report_only:
        chosen = [s for s in SCENARIOS if not args.scenarios or s.sid in args.scenarios]
        for p in args.policies:
            # Rule policies are deterministic; repeating them only checks that.
            evaluate(p, chosen, args.repeats or (5 if p == "llm" else 1))
    write_report(args.grades)


if __name__ == "__main__":
    main()
