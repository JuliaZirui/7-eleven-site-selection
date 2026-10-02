"""Day 4: bounded Agent follow-up layer.

Day 3's metrics_pipeline.py is fixed Program work: no judgment, no new
API calls. This module adds the "bounded, agent-driven follow-up"
piece from project_plan_v3.md's MVP scope and "Division of
Responsibilities" table: identify gaps that actually affect the
conclusions, pick a follow-up tool, validate the call budget, update
evidence, and explain what it did -- or explain why it stopped.

Per the plan, the trigger for a follow-up call is never a raw count
("fewer than 8 POIs"): it's one of three things --

  - missing:     an area_metrics row already flagged is_missing=1, or a
                 competitor POI with no recorded opening_hours
  - stale:       a source snapshot backing this area is older than
                 STALE_SNAPSHOT_DAYS
  - conflicting: the same external_ref reported with a different
                 category or opening_hours_raw across two snapshots

Three live-API tools re-run the *existing* ingestion for a single
area (refetch_osm_pois wraps poi_pipeline.ingest_area and re-conflates;
refetch_lor_population mirrors lor_pipeline's snapshot-writing, scoped
to one node; refetch_ihk_gewerbedaten wraps ihk_pipeline.ingest_area)
-- no new data source, just a targeted, budget-aware re-fetch.
A fourth tool, flag_for_field_research, makes no network call: it
writes a development_events row with a proposed field-research
question, and is what the Agent falls back to once a gap can't be
resolved by re-fetching (a conflict, or hours OSM has simply never
recorded) or once the call budget is spent.

Run directly to process all CANDIDATE_NODES:
    python3 agent_followup.py [--policy impact_rule|rule|llm] [--weak-evidence]

Since v2 the orchestration loop lives in agent_policy.py (one executor,
pluggable policies); this module keeps detection, the tools and the
per-gap rule.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from metrics_pipeline import latest_area_snapshots, latest_snapshot_id
from models import CANDIDATE_NODES, LocationInput

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
AGENT_LOG_DIR = PROJECT_ROOT / "db" / "raw" / "agent_log"
LOR_RAW_DIR = PROJECT_ROOT / "db" / "raw" / "lor"

# A live dataset (OSM, LOR/MSS) older than this is treated as stale
# enough to be worth a fresh look before trusting it for a site
# comparison. Chosen as a "data is probably still fine but check
# before finalizing" horizon, not a hard expiry -- both source APIs
# update on their own schedules regardless of when we last asked.
STALE_SNAPSHOT_DAYS = 14

# Live-tool call budget. Deliberately small: this is a bounded
# follow-up layer, not an open-ended crawler. Two calls per area caps
# a single area from burning the whole run's budget on its own; four
# total means a run touching all three CANDIDATE_NODES can resolve at
# most one area's worth of gaps twice over (or two areas once each)
# before the Agent has to start flagging instead of calling.
DEFAULT_MAX_CALLS_TOTAL = 4
DEFAULT_MAX_CALLS_PER_AREA = 2

TOOL_REFETCH_OSM = "refetch_osm_pois"
TOOL_REFETCH_LOR = "refetch_lor_population"
TOOL_REFETCH_IHK = "refetch_ihk_gewerbedaten"
TOOL_FLAG_FIELD_RESEARCH = "flag_for_field_research"

_LIVE_TOOLS = (TOOL_REFETCH_OSM, TOOL_REFETCH_LOR, TOOL_REFETCH_IHK)

# A field question names at most this many shops; the full list stays in
# the gap's extra["names"] and the tool log.
MAX_NAMES_IN_QUESTION = 10

_POPULATION_METRICS = {"resident_population", "competitor_density_per_1000_residents"}

# source_snapshots.source_name -> the tool that can refresh it. Used by
# the STALE_SNAPSHOT branch of select_tool(); a source with no entry
# here has no automated refresh path and always gets flagged instead.
_SOURCE_TO_TOOL = {
    "osm_overpass": TOOL_REFETCH_OSM,
    "poi_conflated": TOOL_REFETCH_OSM,
    "berlin_lor_boundaries": TOOL_REFETCH_LOR,
    "berlin_mss_population": TOOL_REFETCH_LOR,
    "ihk_gewerbedaten": TOOL_REFETCH_IHK,
}


class GapType(str, Enum):
    MISSING_METRIC = "missing_metric"
    MISSING_POI_HOURS = "missing_poi_hours"
    STALE_SNAPSHOT = "stale_snapshot"
    CONFLICTING_OBSERVATION = "conflicting_observation"
    # Detected from the scoring output, not the raw tables -- see
    # agent_policy.detect_weak_evidence(). detect_gaps() never emits it.
    WEAK_EVIDENCE = "weak_evidence"


@dataclass(frozen=True)
class GapTrigger:
    area_id: str
    gap_type: GapType
    detail: str
    extra: dict = field(default_factory=dict)


class CallBudget:
    """Tracks live-API follow-up calls against a total and a per-area cap.

    Only the three refetch tools consume budget -- flag_for_field_research
    is a local DB write, not an external call, and is never rationed.
    """

    def __init__(
        self,
        max_calls_total: int = DEFAULT_MAX_CALLS_TOTAL,
        max_calls_per_area: int = DEFAULT_MAX_CALLS_PER_AREA,
    ) -> None:
        self.max_calls_total = max_calls_total
        self.max_calls_per_area = max_calls_per_area
        self.used_total = 0
        self._used_by_area: dict[str, int] = {}

    def can_call(self, area_id: str) -> bool:
        return (
            self.used_total < self.max_calls_total
            and self._used_by_area.get(area_id, 0) < self.max_calls_per_area
        )

    def record_call(self, area_id: str) -> None:
        self.used_total += 1
        self._used_by_area[area_id] = self._used_by_area.get(area_id, 0) + 1


# --------------------------------------------------------------------------
# Gap detection -- reads what Day 1-3 already wrote, no new API calls.
# --------------------------------------------------------------------------


def _snapshot_age_days(fetched_at: str, now: datetime) -> float:
    fetched = datetime.fromisoformat(fetched_at)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (now - fetched).total_seconds() / 86400.0


def _latest_metrics(conn: sqlite3.Connection, area_id: str) -> dict[str, dict]:
    """area_metrics is append-only (Day 3); collapse to the latest row
    per metric_name so a gap already resolved by a later run isn't
    re-flagged from a stale earlier row."""
    rows = conn.execute(
        """
        SELECT metric_name, is_missing, missing_reason, computed_at
        FROM area_metrics
        WHERE area_id = ?
        ORDER BY computed_at ASC, metric_id ASC
        """,
        (area_id,),
    ).fetchall()
    latest: dict[str, dict] = {}
    for metric_name, is_missing, missing_reason, computed_at in rows:
        latest[metric_name] = {
            "is_missing": bool(is_missing),
            "missing_reason": missing_reason,
            "computed_at": computed_at,
        }
    return latest


def _detect_missing_metrics(conn: sqlite3.Connection, area_id: str) -> list[GapTrigger]:
    triggers = []
    for metric_name, m in _latest_metrics(conn, area_id).items():
        if not m["is_missing"]:
            continue
        triggers.append(
            GapTrigger(
                area_id=area_id,
                gap_type=GapType.MISSING_METRIC,
                detail=f"{metric_name} is missing: {m['missing_reason']}",
                extra={"metric_name": metric_name},
            )
        )
    return triggers


def _detect_missing_poi_hours(conn: sqlite3.Connection, area_id: str, now: datetime) -> list[GapTrigger]:
    rows = conn.execute(
        """
        SELECT o.external_ref, o.name, s.fetched_at
        FROM observations o
        JOIN source_snapshots s ON s.snapshot_id = o.snapshot_id
        WHERE o.area_id = ? AND o.entity_type = 'poi' AND o.opening_hours_raw IS NULL
          AND o.snapshot_id = ?
        """,
        (area_id, latest_snapshot_id(conn, area_id, "poi")),
    ).fetchall()
    if not rows:
        return []
    refs = [r[0] for r in rows]
    names = [r[1] or r[0] for r in rows]
    max_age = max(_snapshot_age_days(r[2], now) for r in rows)
    is_stale = max_age >= STALE_SNAPSHOT_DAYS
    return [
        GapTrigger(
            area_id=area_id,
            gap_type=GapType.MISSING_POI_HOURS,
            detail=f"{len(refs)} competitor POI(s) with no recorded opening_hours: {', '.join(names)}",
            extra={"external_refs": refs, "names": names, "is_stale": is_stale, "snapshot_age_days": round(max_age, 1)},
        )
    ]


def _detect_stale_snapshots(conn: sqlite3.Connection, area_id: str, now: datetime) -> list[GapTrigger]:
    triggers = []
    # Only the newest snapshot per source: an old snapshot already
    # superseded by a refetch is history, not a staleness gap.
    for snap in latest_area_snapshots(conn, area_id):
        age = _snapshot_age_days(snap["fetched_at"], now)
        if age < STALE_SNAPSHOT_DAYS:
            continue
        triggers.append(
            GapTrigger(
                area_id=area_id,
                gap_type=GapType.STALE_SNAPSHOT,
                detail=(
                    f"{snap['source_name']} snapshot #{snap['snapshot_id']} is "
                    f"{age:.0f} days old (>= {STALE_SNAPSHOT_DAYS})"
                ),
                extra={"snapshot_id": snap["snapshot_id"], "source_name": snap["source_name"]},
            )
        )
    return triggers


def _detect_conflicting_observations(conn: sqlite3.Connection, area_id: str) -> list[GapTrigger]:
    """Same external_ref, different category or opening_hours_raw across
    two snapshots. Only possible once an area has been re-fetched (a
    single-snapshot area can't conflict with itself) -- this is the
    path a refetch_osm_pois follow-up call can itself feed into."""
    rows = conn.execute(
        """
        SELECT external_ref, category, opening_hours_raw, snapshot_id, name
        FROM observations
        WHERE area_id = ? AND entity_type = 'poi' AND external_ref IS NOT NULL
        ORDER BY external_ref, snapshot_id
        """,
        (area_id,),
    ).fetchall()
    by_ref: dict[str, list[tuple]] = {}
    for ref, category, hours, snapshot_id, name in rows:
        by_ref.setdefault(ref, []).append((category, hours, snapshot_id, name))

    triggers = []
    for ref, entries in by_ref.items():
        if len(entries) < 2:
            continue
        categories = sorted({e[0] for e in entries})
        hours_values = sorted({e[1] or "NULL" for e in entries})
        if len(categories) <= 1 and len(hours_values) <= 1:
            continue  # identical across snapshots -- not a conflict
        name = entries[-1][3] or ref
        triggers.append(
            GapTrigger(
                area_id=area_id,
                gap_type=GapType.CONFLICTING_OBSERVATION,
                detail=(
                    f"{name} ({ref}) reported inconsistently across snapshots "
                    f"{[e[2] for e in entries]}: categories={categories}, opening_hours={hours_values}"
                ),
                extra={"external_ref": ref, "snapshot_ids": [e[2] for e in entries]},
            )
        )
    return triggers


def detect_gaps(conn: sqlite3.Connection, area_id: str, now: datetime | None = None) -> list[GapTrigger]:
    now = now or datetime.now(timezone.utc)
    return (
        _detect_missing_metrics(conn, area_id)
        + _detect_missing_poi_hours(conn, area_id, now)
        + _detect_stale_snapshots(conn, area_id, now)
        + _detect_conflicting_observations(conn, area_id)
    )


# --------------------------------------------------------------------------
# Tool selection -- deterministic rules, not an open-ended LLM decision.
# Matches "The Agent demo shows tool selection... not chain-of-thought logs."
# --------------------------------------------------------------------------


def select_tool(gap: GapTrigger) -> str:
    if gap.gap_type == GapType.MISSING_METRIC:
        if gap.extra["metric_name"] in _POPULATION_METRICS:
            return TOOL_REFETCH_LOR
        return TOOL_REFETCH_OSM  # every other metric is competitor/spatial-derived
    if gap.gap_type == GapType.MISSING_POI_HOURS:
        # A same-day re-query of OSM for the exact same POIs will almost
        # certainly return byte-identical hours -- only worth a live
        # call if the backing snapshot is actually stale.
        return TOOL_REFETCH_OSM if gap.extra["is_stale"] else TOOL_FLAG_FIELD_RESEARCH
    if gap.gap_type == GapType.STALE_SNAPSHOT:
        # No entry for this source_name -- e.g. a future source added
        # without a refresh tool -- falls back to flagging rather than
        # guessing at a tool that might not even apply to it.
        return _SOURCE_TO_TOOL.get(gap.extra["source_name"], TOOL_FLAG_FIELD_RESEARCH)
    if gap.gap_type == GapType.CONFLICTING_OBSERVATION:
        # No automated tool resolves a discrepancy -- per the Division of
        # Responsibilities table, "explaining discrepancies" is Agent
        # work, not something a re-fetch can settle.
        return TOOL_FLAG_FIELD_RESEARCH
    if gap.gap_type == GapType.WEAK_EVIDENCE:
        # Weak corroboration or under-mapping is a property of the open
        # data itself; refetching the same source returns the same gap.
        return TOOL_FLAG_FIELD_RESEARCH
    raise AssertionError(f"unhandled gap type: {gap.gap_type}")


def allowed_tools(gap: GapTrigger) -> frozenset[str]:
    """What any policy may do with this gap: the rule's tool, or flag it.
    A policy chooses order, refetch-vs-flag and the question; it cannot
    route a gap to a source the rule says can't resolve it."""
    return frozenset({select_tool(gap), TOOL_FLAG_FIELD_RESEARCH})


def _field_research_question(gap: GapTrigger) -> str:
    if gap.gap_type == GapType.MISSING_METRIC:
        return f"Desk/field research needed -- {gap.detail}."
    if gap.gap_type == GapType.MISSING_POI_HOURS:
        names = gap.extra["names"]
        shown = ", ".join(names[:MAX_NAMES_IN_QUESTION])
        more = f" and {len(names) - MAX_NAMES_IN_QUESTION} more" if len(names) > MAX_NAMES_IN_QUESTION else ""
        return f"Confirm opening hours on-site for {len(names)} competitors without recorded hours: {shown}{more}."
    if gap.gap_type == GapType.STALE_SNAPSHOT:
        return f"Re-verify before finalizing the comparison -- {gap.detail}."
    if gap.gap_type == GapType.CONFLICTING_OBSERVATION:
        return f"Verify current status on-site -- {gap.detail}"
    if gap.gap_type == GapType.WEAK_EVIDENCE:
        return f"Verify on-site before relying on the {gap.extra['pillar']} score -- {gap.detail}."
    raise AssertionError(f"unhandled gap type: {gap.gap_type}")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def _tool_refetch_osm(conn: sqlite3.Connection, node: LocationInput) -> dict:
    """Refetch OSM (one live call) and re-conflate against the latest
    Overture/FSQ snapshots already on record (local, no extra call), so
    the refreshed OSM data actually reaches the competitor set."""
    from poi_conflation import conflate_area
    from poi_pipeline import PoiPipelineError, ingest_area

    try:
        result = ingest_area(conn, node)
    except PoiPipelineError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "ok", "detail": {**result, "conflated": conflate_area(conn, node)}}


def _tool_refetch_lor(conn: sqlite3.Connection, node: LocationInput) -> dict:
    from lor_client import (
        LOR_BOUNDARIES_LICENSE,
        LOR_BOUNDARIES_VERSION,
        MSS_POPULATION_LICENSE,
        LorFetchError,
        fetch_lor_boundaries,
        fetch_mss_population,
        population_by_plr_id,
    )
    from lor_pipeline import _ingest_node

    now = datetime.now(timezone.utc)
    try:
        boundaries = fetch_lor_boundaries()
        population_geojson = fetch_mss_population()
    except LorFetchError as exc:
        return {"status": "error", "error": str(exc)}

    LOR_RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    boundaries_path = LOR_RAW_DIR / f"followup_{node.area_id}_boundaries_{stamp}.geojson"
    boundaries_path.write_text(json.dumps(boundaries, ensure_ascii=False))
    population_path = LOR_RAW_DIR / f"followup_{node.area_id}_population_{stamp}.geojson"
    population_path.write_text(json.dumps(population_geojson, ensure_ascii=False))

    pop_index = population_by_plr_id(population_geojson)

    boundaries_snapshot_id = conn.execute(
        """
        INSERT INTO source_snapshots
            (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "berlin_lor_boundaries",
            LOR_BOUNDARIES_VERSION,
            now.isoformat(),
            LOR_BOUNDARIES_LICENSE,
            str(boundaries_path.relative_to(PROJECT_ROOT)),
            f"Day 4 agent follow-up refetch, single area ({node.area_id})",
        ),
    ).lastrowid
    population_snapshot_id = conn.execute(
        """
        INSERT INTO source_snapshots
            (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "berlin_mss_population",
            "2024-12-31",
            now.isoformat(),
            MSS_POPULATION_LICENSE,
            str(population_path.relative_to(PROJECT_ROOT)),
            f"Day 4 agent follow-up refetch, single area ({node.area_id})",
        ),
    ).lastrowid

    result = _ingest_node(conn, node, boundaries, pop_index, population_snapshot_id)
    conn.execute(
        "UPDATE areas SET lor_code = COALESCE(lor_code, ?) WHERE area_id = ?",
        (result.get("plr_id"), node.area_id),
    )
    conn.commit()
    return {
        "status": "ok",
        "new_snapshot_ids": [boundaries_snapshot_id, population_snapshot_id],
        "detail": result,
    }


def _tool_refetch_ihk(conn: sqlite3.Connection, node: LocationInput) -> dict:
    from ihk_pipeline import IhkPipelineError, ingest_area

    try:
        result = ingest_area(conn, node)
        return {"status": "ok", "detail": result}
    except IhkPipelineError as exc:
        return {"status": "error", "error": str(exc)}


_TOOL_FUNCS = {
    TOOL_REFETCH_OSM: _tool_refetch_osm,
    TOOL_REFETCH_LOR: _tool_refetch_lor,
    TOOL_REFETCH_IHK: _tool_refetch_ihk,
}


def _tool_flag_field_research(conn: sqlite3.Connection, node: LocationInput, gap: GapTrigger,
                              question: str | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    question = question or _field_research_question(gap)
    conn.execute(
        """
        INSERT INTO development_events
            (area_id, event_type, title, description, event_date, source_snapshot_id, source_url, recorded_at)
        VALUES (?, 'evidence_gap', ?, ?, NULL, NULL, NULL, ?)
        """,
        (node.area_id, f"Evidence gap: {gap.gap_type.value}", question, now),
    )
    conn.commit()
    return {"status": "flagged", "question": question}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_followups(conn: sqlite3.Connection, node: LocationInput, budget: CallBudget, now: datetime | None = None) -> list[dict]:
    """Day 4 entry point, kept for its callers: one area, rule policy,
    no weak-evidence gaps. The loop itself now lives in
    agent_policy.run_agent(), shared by every policy. A live tool
    already called once for this area in this run is reused, not
    charged again; a source that failed is not retried."""
    from agent_policy import RulePolicy, run_agent

    return run_agent(conn, [node], budget, RulePolicy(), now=now, include_weak_evidence=False)["log"]


def _make_policy(name: str):
    from agent_policy import ImpactRulePolicy, RulePolicy

    if name == "rule":
        return RulePolicy()
    if name == "impact_rule":
        return ImpactRulePolicy()
    if name == "llm":
        from agent_llm_policy import LlmPolicy

        return LlmPolicy()
    raise ValueError(f"unknown policy {name!r}")


def run(db_path: Path = DB_PATH, budget: CallBudget | None = None, policy: str = "impact_rule",
        include_weak_evidence: bool = False) -> list[dict]:
    """Default policy is the impact rule (comparison/policy_findings.md);
    policy="rule" reproduces Day 4 exactly. The tool log keeps its Day 4
    format (report.py reads it); run metadata -- policy, guard
    rejections, unhandled gaps, LLM transcript and cost -- goes to
    agent_log/runs/, out of report.py's way."""
    from agent_policy import run_agent

    budget = budget or CallBudget()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        out = run_agent(conn, list(CANDIDATE_NODES), budget, _make_policy(policy),
                        include_weak_evidence=include_weak_evidence)
    finally:
        conn.close()
    full_log = out["log"]
    for e in full_log:
        suffix = f" (stop: {e['stop_reason']})" if e["stop_reason"] else ""
        print(f"[{e['area_id']}] {e['gap_type']} -> {e['tool']}{suffix}")
    if not out["run"]["gaps"]:
        print("No information gaps detected -- nothing for the Agent to follow up on.")
    for r in out["run"]["guard_rejections"]:
        print(f"  guard rejected step {r['step']}: {r['proposed']['tool']} on {r['proposed']['gap_id']} ({r['reason']})")
    print(f"Finished: {out['run']['finish_reason']}" + (f"; fallback: {out['run']['policy_fallback']}" if out["run"]["policy_fallback"] else ""))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    AGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = AGENT_LOG_DIR / f"day4_followup_{stamp}.json"
    log_path.write_text(json.dumps(full_log, ensure_ascii=False, indent=2))
    runs_dir = AGENT_LOG_DIR / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"agent_run_{policy}_{stamp}.json").write_text(
        json.dumps({**out["run"], "tool_log": log_path.name}, ensure_ascii=False, indent=2, default=str)
    )
    print(
        f"Tool log written to {log_path.relative_to(PROJECT_ROOT)} "
        f"({budget.used_total}/{budget.max_calls_total} live calls used)"
    )
    return full_log


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Bounded follow-up agent")
    ap.add_argument("--policy", choices=["rule", "impact_rule", "llm"], default="impact_rule")
    ap.add_argument("--weak-evidence", action="store_true", help="also follow up WEAK_EVIDENCE gaps")
    args = ap.parse_args()
    run(policy=args.policy, include_weak_evidence=args.weak_evidence)
