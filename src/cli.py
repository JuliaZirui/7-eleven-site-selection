"""Single entry point: run the demo, the checks, or the full pipeline.

    python3 src/cli.py demo                 # offline, ~2 s: report from the committed database
    python3 src/cli.py check                # offline: test suite + reproducibility check
    python3 src/cli.py validate [--sites F] # offline: check a site input file
    python3 src/cli.py steps                # pipeline order, network use, outputs
    python3 src/cli.py config               # effective configuration (never prints secrets)
    python3 src/cli.py refresh [--sites F] [--db PATH] [--only STEP ...]   # network: fetch + full pipeline

Every command logs to the console and to logs/<command>_<stamp>.log.
Exit codes: 0 = done; 1 = done with warnings (a source was skipped and
the previous snapshot stayed current, or a non-essential step failed);
2 = failed (bad input, missing database, a step the report needs failed,
or a test failed).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from site_input import SiteInputError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
LOG_DIR = PROJECT_ROOT / "logs"
DEMO_OUT = PROJECT_ROOT / "out" / "demo"

EXIT_OK, EXIT_WARN, EXIT_FAIL = 0, 1, 2

log = logging.getLogger("sitesel")


# --------------------------------------------------------------------------
# Pipeline steps: order, network use, outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    key: str
    module: str
    network: str  # "offline" or the services it calls
    writes: str
    essential: bool  # the report cannot be built if this step fails


STEPS: tuple[Step, ...] = (
    Step("init", "init_db", "offline", "creates the database if missing; adds an areas row per input site", True),
    Step("lor", "lor_pipeline", "Berlin Geoportal WFS (gdi.berlin.de)",
         "LOR boundaries + MSS 2025 population snapshots", False),
    Step("poi", "poi_pipeline", "Overpass API; Overture Maps (S3); Foursquare OS Places via Hugging Face (HF_TOKEN)",
         "competitor snapshots per source, then the conflated competitor set", False),
    Step("ihk", "ihk_pipeline", "Berlin Geoportal IHK Gewerbedaten WFS", "registered-business snapshots", False),
    Step("attractions", "attraction_pipeline", "Overpass API; Wikidata; Wikimedia pageviews",
         "gateway-attraction snapshots (visitor demand)", False),
    Step("metrics", "metrics_pipeline", "offline", "area_metrics rows (counts, demand, opening-hours windows)", True),
    Step("audit", "audit_pipeline",
         "Claude API with web search (ANTHROPIC_API_KEY or .env; skipped without); Overpass once per new site",
         "plausibility flags in the review queue (comparison/audit_review_queue.csv); changes no number", False),
    Step("followup", "agent_followup",
         "only for a live refetch the agent chooses (max 4 calls); Claude API with --policy llm",
         "tool log (db/raw/agent_log/); evidence gaps flagged for field research", False),
    Step("report", "report", "offline", "reports/site_report_run<N>_<stamp>.md + scores JSON + analysis_runs row", True),
)
STEP_KEYS = tuple(s.key for s in STEPS)

# Failure messages -> what it means and what to do. Matched on the
# exception type name and message, first match wins.
HINTS: tuple[tuple[str, str], ...] = (
    (r"HF_TOKEN is not set",
     "Foursquare skipped (optional source). Set HF_TOKEN to include it; OSM + Overture still ran."),
    (r"Foursquare|FSQ|fsq",
     "Foursquare (optional source) could not be read. OSM + Overture are used without it."),
    (r"Overpass|overpass",
     "Overpass mirrors failed or returned an incomplete dataset. The previous OSM snapshot stays current; "
     "retry later."),
    (r"Overture|stac\.overturemaps|duckdb|IO Error",
     "Overture's S3/STAC could not be read. The previous Overture snapshot stays current; retry later."),
    (r"LOR|MSS|gdi\.berlin\.de.*(lor|mss)",
     "Berlin Geoportal (LOR/MSS) unreachable. The previous population snapshot stays current."),
    (r"IHK|gewerbedaten", "IHK WFS unreachable. The previous business snapshot stays current."),
    (r"[Ww]ikidata|[Ww]ikimedia|pageviews|Attraction",
     "Wikidata/Wikimedia unreachable. The previous attraction snapshot stays current."),
    (r"no OSM source snapshot", "This site has never been fetched: run `refresh` with network access first."),
    (r"no such table", "The database is not initialised: run `refresh` (it creates it) or check --db."),
    (r"audit not run",
     "Data-plausibility audit skipped: set ANTHROPIC_API_KEY (or put it in .env) to run it, or pass --no-audit. "
     "The report marks the sites as unaudited; nothing else changes."),
    (r"ANTHROPIC|anthropic|API key|authentication",
     "Claude API unavailable: the LLM policy falls back to the impact rule. Set ANTHROPIC_API_KEY or use "
     "--policy impact_rule."),
    (r"network blocked|UnmockedNetworkCall", "This command must run offline but a step tried to reach the network."),
)


def hint_for(text: str) -> str | None:
    for pattern, hint in HINTS:
        if re.search(pattern, text):
            return hint
    return None


def _errors(result, area: str | None = None, source: str | None = None) -> list[str]:
    """Every 'error' anywhere in a step's return value, labelled with its
    site (and source, if nested under one), first line only: the
    pipelines record a skipped source per area instead of raising. An
    area-level error that repeats a source's error is not listed twice."""
    found: list[str] = []
    if isinstance(result, dict):
        area = result.get("area_id", area)
        for k, v in result.items():
            if k != "error":
                found += _errors(v, area, k if isinstance(v, dict) else source)
        err = result.get("error")
        if isinstance(err, str) and not any(f.endswith(f": {err.splitlines()[0]}") for f in found):
            found.insert(0, f"{area or '?'}{f' / {source}' if source else ''}: {err.splitlines()[0]}")
    elif isinstance(result, (list, tuple)):
        for v in result:
            found += _errors(v, area, source)
    return found


def _display(path: Path) -> str:
    """Repo-relative if inside the repository, else absolute."""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


class _LogStream(io.TextIOBase):
    """stdout of a pipeline module -> log lines under its step name.
    'SKIPPED' lines become warnings."""

    def __init__(self, prefix: str, level: int = logging.INFO):
        self.prefix, self.level, self.buf, self.in_skip = prefix, level, "", False

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._emit(line)
        return len(s)

    def flush(self) -> None:
        if self.buf:
            self._emit(self.buf)
            self.buf = ""

    def _emit(self, line: str) -> None:
        if not line.strip():
            return
        if re.match(r"\s*SKIPPED", line):
            self.in_skip, level = True, logging.WARNING
        elif self.in_skip and re.match(r"\s*https?://", line):
            level = logging.DEBUG  # per-mirror retry details of a SKIPPED message: log file only
        else:
            self.in_skip, level = False, self.level
        log.log(level, f"{self.prefix}{line.rstrip()}")


@contextlib.contextmanager
def captured(prefix: str, level: int = logging.INFO):
    stream = _LogStream(prefix, level)
    with contextlib.redirect_stdout(stream):
        yield
    stream.flush()


def setup_logging(command: str, verbose: bool = False) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{command}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log"
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    file = logging.FileHandler(path, encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(console)
    log.addHandler(file)
    log.propagate = False
    return path


# --------------------------------------------------------------------------
# Site input
# --------------------------------------------------------------------------


def use_sites(path: str | None) -> Path:
    """Point every pipeline module at this site input. Must run before
    any of them is imported (models reads the file at import)."""
    if path:
        os.environ["SITE_INPUT"] = str(Path(path).resolve())
    from site_input import site_input_path

    return site_input_path()


def check_sites(path: Path) -> list[str] | None:
    """Validate the input file, then its stations against the GTFS
    snapshot. Returns the area_ids, or None after logging the errors."""
    from site_input import SiteInputError, parse_sites

    try:
        sites = parse_sites(path)
    except SiteInputError as exc:
        log.error(f"Site input invalid: {exc}")
        return None
    from attraction_pipeline import rail_stations

    known = rail_stations()
    bad = [(x["area_id"], x["gtfs_stations"][0]) for x in sites if x["gtfs_stations"][0] not in known]
    for area_id, name in bad:
        word = re.sub(r"^(S\+U|S|U)\s+|\s*\(Berlin\)$", "", name).split()[0].lower()
        close = [k for k in known if word in k.lower()][:3]
        log.error(f"Site input invalid: {area_id}: gtfs_stations[0] {name!r} is not a VBB S/U parent station"
                  + (f" (similar: {', '.join(close)})" if close else ""))
    if bad:
        return None
    for x in sites:
        log.info(f"  {x['area_id']:22s} {x['name']:24s} ({x['lat']:.5f}, {x['lon']:.5f}) r={x['radius_m']}m "
                 f"station={x['gtfs_stations'][0]}")
    return [x["area_id"] for x in sites]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_steps(args) -> int:
    print(f"{'#':>2}  {'step':12s} {'module':27s} {'network':10s} writes")
    for i, s in enumerate(STEPS, 1):
        net = "offline" if s.network == "offline" else ("if needed" if s.network.startswith("only") else "NETWORK")
        print(f"{i:>2}  {s.key:12s} {'src/' + s.module + '.py':27s} {net:10s} {s.writes}")
        if s.network != "offline":
            print(f"{'':44s}calls: {s.network}")
    return EXIT_OK


def cmd_config(args) -> int:
    path = use_sites(args.sites)
    rows = [
        ("Site input", str(path), "SITE_INPUT or --sites; default examples/berlin_three_nodes.json"),
        ("Database", str(Path(args.db or DB_PATH)), "--db; default db/site_selection.db"),
        ("Store concept", "store_hypothesis.md", "read by the report"),
    ]
    for label, value, note in rows:
        log.info(f"{label:16s} {value}  ({note})")
    env = (
        ("HF_TOKEN", "refresh, poi step", "Foursquare OS Places (gated dataset). Unset: source skipped, OSM + Overture "
                                         "still used."),
        ("ANTHROPIC_API_KEY", "refresh --policy llm", "Claude for follow-up decisions. Unset: falls back to the "
                                                     "impact rule."),
        ("ANTHROPIC_WORKSPACE_ID", "refresh --policy llm", "Optional: bill a specific Claude workspace."),
        ("SITE_INPUT", "all commands", "Path of the site input file (same as --sites)."),
    )
    for name, used_by, effect in env:
        state = "set" if os.environ.get(name) else "not set"
        log.info(f"{name:24s} {state:8s} used by {used_by}: {effect}")
    return EXIT_OK


def cmd_validate(args) -> int:
    path = use_sites(args.sites)
    log.info(f"Validating {path}")
    if check_sites(path) is None:
        return EXIT_FAIL
    log.info("Site input OK")
    return EXIT_OK


def _recorded_run_at(db: Path) -> datetime | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT run_at FROM analysis_runs WHERE report_ref IS NOT NULL ORDER BY run_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return datetime.fromisoformat(row[0]) if row else None


def _blocked(*a, **kw):
    raise RuntimeError("network blocked: the demo runs offline")


def cmd_demo(args) -> int:
    """Rebuild metrics and the report from a copy of the committed
    database, with the network blocked; nothing in the repo changes."""
    path = use_sites(None)
    from site_input import DEFAULT_SITE_INPUT

    if path.resolve() != DEFAULT_SITE_INPUT.resolve():
        log.error(f"The demo uses the committed database, which holds the example sites only; SITE_INPUT points to "
                  f"{path}. Unset SITE_INPUT, or score your own sites with `refresh --sites FILE --db NEW.db`.")
        return EXIT_FAIL
    if not DB_PATH.exists():
        log.error(f"{_display(DB_PATH)} not found: the demo needs the committed database "
                  f"(git checkout db/site_selection.db).")
        return EXIT_FAIL
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "db" / "raw").mkdir(parents=True)
    db = out / "db" / "site_selection.db"
    shutil.copy(DB_PATH, db)
    shutil.copytree(PROJECT_ROOT / "db" / "raw" / "agent_log", out / "db" / "raw" / "agent_log")
    shutil.copy(PROJECT_ROOT / "store_hypothesis.md", out / "store_hypothesis.md")

    run_at = _recorded_run_at(db) if args.as_of == "recorded" else None
    if run_at is not None:
        log.info(f"Input: examples/berlin_three_nodes.json + committed snapshots; evidence freshness judged as of the "
                 f"last recorded run ({run_at:%Y-%m-%d %H:%M} UTC), so the figures match the README")
    else:
        run_at = datetime.now(timezone.utc)
        log.info("Input: examples/berlin_three_nodes.json + committed snapshots; freshness judged as of now "
                 "(evidence quality can be lower than in the README as the snapshots age)")

    import metrics_pipeline
    import report

    patches = [
        mock.patch("httpx.get", side_effect=_blocked),
        mock.patch("httpx.post", side_effect=_blocked),
        mock.patch.object(report, "PROJECT_ROOT", out),
        mock.patch.object(report, "REPORTS_DIR", out / "reports"),
        mock.patch.object(report, "AGENT_LOG_DIR", out / "db" / "raw" / "agent_log"),
        mock.patch.object(report, "STORE_HYPOTHESIS_PATH", out / "store_hypothesis.md"),
    ]
    t0 = time.perf_counter()
    try:
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            log.info("[1/2] metrics: recompute area_metrics from the stored snapshots (offline)")
            with captured("      "):
                metrics_pipeline.run(db)
            log.info("[2/2] report: score, sensitivity analysis, citation check (offline)")
            with captured("      ", logging.DEBUG):  # the summary below replaces its console lines
                report_path = report.run(db, run_at=run_at)
    except Exception as exc:  # noqa: BLE001 -- turned into a message + exit code
        log.error(f"Demo failed: {type(exc).__name__}: {exc}")
        hint = hint_for(f"{type(exc).__name__}: {exc}")
        if hint:
            log.error(f"  -> {hint}")
        log.debug("traceback", exc_info=True)
        return EXIT_FAIL
    scores = json.loads(next((out / "reports").glob("site_scores_*.json")).read_text())
    _print_summary(scores)
    log.info(f"Done in {time.perf_counter() - t0:.1f}s. Report: {_display(report_path)}")
    log.info("The repository's database and reports/ were not changed.")
    return EXIT_OK


def _print_summary(scores: dict) -> None:
    from models import CANDIDATE_NODES

    names = {n.area_id: n.name for n in CANDIDATE_NODES}
    sens = scores["sensitivity"]
    log.info(f"{'Node':22s} {'Fit':>6s} {'Evidence':>9s}  {'tier':12s} {'#1 share':>8s}")
    for a in sens["base_rank"]:
        r = scores["results"][a]
        eq = r["evidence_quality"]
        fit = "n/a" if r["business_fit"] is None else f"{r['business_fit']:.1f}"
        log.info(f"{names.get(a, a):22s} {fit:>6s} {eq['score']:9.1f}  {eq['tier']:12s} "
                 f"{sens['monte_carlo']['top_share'][a]:8.0%}")
    log.info(f"Top rank robust across weightings: {sens['top_rank_robust']}")


def cmd_check(args) -> int:
    log.info("Running tests/run_day6.py: test suites + offline end-to-end reproducibility check")
    proc = subprocess.run([sys.executable, str(PROJECT_ROOT / "tests" / "run_day6.py")],
                          cwd=PROJECT_ROOT, capture_output=True, text=True)
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.strip():
            log.log(logging.ERROR if re.search(r"FAIL|ERROR|Traceback|not reproducible", line) else logging.INFO,
                    f"  {line}")
    if proc.returncode != 0:
        log.error("Checks failed: see the lines above and the test log under reports/.")
        return EXIT_FAIL
    log.info("All checks passed.")
    return EXIT_OK


def run_dir(db: Path) -> Path | None:
    """Where a non-default database keeps its reports and agent logs:
    db/my_sites.db -> db/my_sites/. None for the case-study database,
    which writes to reports/ and db/raw/agent_log/ as before. Keeping
    them apart matters: the report cites the newest agent log in its
    folder, so a run on other sites must not land next to the case study's."""
    return None if db.resolve() == DB_PATH.resolve() else db.resolve().parent / db.stem


def _run_step(step: Step, db: Path, args) -> dict:
    import importlib

    mod = importlib.import_module(step.module)
    own = run_dir(db)
    if own is not None and step.key in ("followup", "report"):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(mod, "AGENT_LOG_DIR", own / "agent_log"))
            if step.key == "report":
                stack.enter_context(mock.patch.object(mod, "REPORTS_DIR", own / "reports"))
            return _call_step(step, mod, db, args)
    return _call_step(step, mod, db, args)


def _call_step(step: Step, mod, db: Path, args) -> dict:
    if step.key == "init":
        if db.exists():
            added = mod.seed_areas(db)
            print(f"database exists: {db.name}" + (f"; added areas {', '.join(added)}" if added else ""))
            return {"added": added}
        db.parent.mkdir(parents=True, exist_ok=True)
        mod.init_db(db)
        print(f"created {db}")
        return {}
    if step.key == "followup":
        return {"log": mod.run(db_path=db, policy=args.policy)}
    if step.key == "audit":
        return {"audit": mod.run(db_path=db, enabled=not getattr(args, "no_audit", False))}
    if step.key == "report":
        return {"report": str(mod.run(db))}
    return {"result": mod.run(db)}


def cmd_refresh(args) -> int:
    path = use_sites(args.sites)
    log.info(f"Site input: {path}")
    if check_sites(path) is None:
        return EXIT_FAIL
    db = Path(args.db or DB_PATH).resolve()
    try:
        db.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        log.error(f"--db must be inside the repository (e.g. db/my_sites.db): reports cite paths relative to it. "
                  f"Got {db}.")
        return EXIT_FAIL
    if not db.exists() and args.only and "init" not in args.only:
        log.error(f"{db} does not exist; run without --only (or include init) to create it.")
        return EXIT_FAIL
    own = run_dir(db)
    log.info(f"Database: {_display(db)}" + (f"; reports and agent logs go to {_display(own)}/" if own else ""))
    if not os.environ.get("HF_TOKEN") and (not args.only or "poi" in args.only):
        log.warning("HF_TOKEN not set: the Foursquare source will be skipped (optional; OSM + Overture still run).")
    steps = [s for s in STEPS if not args.only or s.key in args.only]

    outcomes = []
    for i, step in enumerate(steps, 1):
        where = "offline" if step.network == "offline" else (
            "network if needed" if step.network.startswith("only") else "network")
        log.info(f"[{i}/{len(steps)}] {step.key} ({where}): {step.writes}")
        t0 = time.perf_counter()
        try:
            with captured("      "):
                result = _run_step(step, db, args)
        except Exception as exc:  # noqa: BLE001 -- recorded per step, run continues where it can
            msg = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
            outcomes.append((step, "failed", time.perf_counter() - t0, [msg]))
            log.error(f"      {step.key} failed: {msg}")
            hint = hint_for(msg)
            if hint:
                log.error(f"      -> {hint}")
            log.debug("traceback", exc_info=True)
            if step.essential:
                log.error(f"      {step.key} is needed for the report; stopping.")
                break
            log.warning("      continuing: later steps use the latest stored snapshots")
            continue
        errors = _errors(result)
        status = "partial" if errors else "ok"
        outcomes.append((step, status, time.perf_counter() - t0, errors))
        # The SKIPPED lines above already name each failure; here, one
        # line per kind of failure with what it means and where it hit.
        by_hint: dict[str, list[str]] = {}
        for e in errors:
            label, _, msg = e.partition(": ")
            hint = hint_for(msg)
            if hint:
                by_hint.setdefault(hint, []).append(label)
            else:
                log.warning(f"      skipped {e}")
        for hint, labels in by_hint.items():
            log.warning(f"      -> {hint} [{', '.join(labels)}]")

    log.info("Summary:")
    for step, status, secs, errors in outcomes:
        log.info(f"  {step.key:12s} {status:8s} {secs:6.1f}s" + (f"  ({len(errors)} issue(s))" if errors else ""))
    not_run = [s.key for s in steps if s.key not in {o[0].key for o in outcomes}]
    if not_run:
        log.info(f"  not run: {', '.join(not_run)}")
    if any(st == "failed" and s.essential for s, st, _, _ in outcomes):
        return EXIT_FAIL
    if any(st != "ok" for _, st, _, _ in outcomes):
        log.warning("Finished with warnings: the report rests partly on earlier snapshots (see 'skipped' lines).")
        return EXIT_WARN
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python3 src/cli.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 done, 1 done with warnings, 2 failed. "
                                        "Logs: logs/<command>_<stamp>.log")
    ap.add_argument("-v", "--verbose", action="store_true", help="also show debug lines (tracebacks)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="offline: rebuild the report from the committed database (~2 s)")
    p.add_argument("--out", default=str(DEMO_OUT), help="output directory (default: out/demo)")
    p.add_argument("--as-of", choices=["recorded", "now"], default="recorded",
                   help="judge data freshness as of the last recorded run (default, matches the README) or now")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("check", help="offline: test suites + end-to-end reproducibility check")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("validate", help="offline: check a site input file")
    p.add_argument("--sites", help="site input JSON (default: SITE_INPUT or examples/berlin_three_nodes.json)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("steps", help="list the pipeline steps in order, with network use and outputs")
    p.set_defaults(func=cmd_steps)

    p = sub.add_parser("config", help="show the effective configuration (secrets shown as set / not set)")
    p.add_argument("--sites")
    p.add_argument("--db")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("refresh", help="NETWORK: fetch fresh source data and run the full pipeline")
    p.add_argument("--sites", help="site input JSON (default: SITE_INPUT or examples/berlin_three_nodes.json)")
    p.add_argument("--db", help="database path (default: db/site_selection.db; created if missing)")
    p.add_argument("--only", nargs="+", choices=STEP_KEYS, metavar="STEP",
                   help=f"run only these steps, in pipeline order ({', '.join(STEP_KEYS)})")
    p.add_argument("--no-audit", action="store_true",
                   help="skip the data-plausibility audit (otherwise it runs when Claude API credentials are set)")
    p.add_argument("--policy", choices=["impact_rule", "rule", "llm"], default="impact_rule",
                   help="follow-up policy (llm needs ANTHROPIC_API_KEY)")
    p.set_defaults(func=cmd_refresh)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = setup_logging(args.command, args.verbose)
    if args.command != "steps":
        log.info(f"{args.command}: log file {log_path.relative_to(PROJECT_ROOT)}")
    try:
        return args.func(args)
    except SiteInputError as exc:
        log.error(f"Site input invalid: {exc}")
        return EXIT_FAIL
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 -- last resort: never a bare traceback
        log.error(f"Unexpected error: {type(exc).__name__}: {exc}")
        log.error(f"  Full traceback in {log_path.relative_to(PROJECT_ROOT)}")
        log.debug("traceback", exc_info=True)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
