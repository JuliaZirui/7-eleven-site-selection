"""v2 agent layer: one executor, pluggable follow-up policies.

Day 4's run_followups() hard-wired one decision rule: handle gaps in
detection order, one tool per gap type. This module splits that into

  - a deterministic executor (run_agent) that owns the budget, the
    guard, tool execution and the log -- plain code, same for every
    policy;
  - a Policy that only proposes the next step: which gap, which tool,
    which field question, or finish.

Three policies share that interface (plan_llm_tool_selection.md):

  A. RulePolicy        today's behaviour, the offline default
  B. ImpactRulePolicy  same rules, gaps sorted by impact on the ranking
  C. LlmPolicy         Claude decides (src/agent_llm_policy.py)

B exists so that any gain C shows can be checked against "it just
sorted by importance", which 20 lines of code also do.

The guard is code, not the model. Whatever a policy proposes, the
executor only runs it if the gap exists and is still open, the tool is
in allowed_tools(gap), the budget allows a live call, and the same
source hasn't already failed for that node in this run. A rejected
proposal is logged and the gap is handled by RulePolicy instead, so a
bad proposal costs one step, never an extra live call.

Scoring never reads anything a policy writes: the fit and evidence
numbers come from the fixed pipeline before the agent runs.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Union

import agent_followup as af
from agent_followup import (
    TOOL_FLAG_FIELD_RESEARCH,
    CallBudget,
    GapTrigger,
    GapType,
    allowed_tools,
    select_tool,
)
from models import LocationInput

LIVE_TOOLS = af._LIVE_TOOLS

# WEAK_EVIDENCE triggers: an evidence-quality component below this value
# on a node. Both components bear on the competition pillar.
WEAK_EVIDENCE_THRESHOLDS: dict[str, float] = {
    "osm_mapping_consistency": 0.60,
    "competitor_corroboration": 0.50,
}
_COMPONENT_PILLAR = {
    "osm_mapping_consistency": "competition",
    "competitor_corroboration": "competition",
}

# A pillar "drives" the #1-vs-#2 gap if it contributes at least this
# share of it. With two pillars, both drive unless one is marginal.
DRIVER_SHARE = 0.25

_DEMAND_SOURCES = {"berlin_lor_boundaries", "berlin_mss_population", "ihk_gewerbedaten", "osm_attractions_gateway"}
_DEMAND_METRIC_HINTS = ("population", "resident", "worker", "visitor", "attraction", "daily_people")

STOP_REUSED = "reused result: this source was already refetched for this area in this run"
STOP_BUDGET = "call budget exhausted -- flagged for field research instead"
STOP_FAILED_BEFORE = "this source already failed for this area in this run -- flagged for field research instead"


class PolicyError(RuntimeError):
    """A policy could not produce a decision at all (API error, refusal,
    timeout). The executor finishes the run with RulePolicy."""


# --------------------------------------------------------------------------
# Decision context -- deterministic, built before any policy runs.
# --------------------------------------------------------------------------


def summarize_decision(fits: dict[str, float | None], pillars: dict[str, dict], weights: dict[str, float],
                       top_share: dict[str, float], top_rank_robust: bool, evidence: dict[str, dict]) -> dict:
    """The part of the scoring output a follow-up decision depends on.
    Split out from build_decision_context() so evaluation scenarios can
    state a situation directly instead of seeding a full database."""
    from scoring import rank

    ranking = rank(fits)
    total_w = sum(weights.values())
    contributions: dict[str, float] = {}
    top_two_gap = None
    if len(ranking) >= 2 and fits[ranking[0]] is not None and fits[ranking[1]] is not None:
        a, b = ranking[0], ranking[1]
        top_two_gap = fits[a] - fits[b]
        for p, w in weights.items():
            pa, pb = pillars[a].get(p), pillars[b].get(p)
            if pa is not None and pb is not None:
                contributions[p] = w * (pa - pb) / total_w
    driving = sorted(
        p for p, c in contributions.items()
        if top_two_gap and top_two_gap > 0 and c > 0 and c >= DRIVER_SHARE * top_two_gap
    )
    return {
        "ranking": ranking,
        "business_fit": {a: _r(v) for a, v in fits.items()},
        "top_two_gap": _r(top_two_gap),
        "top_two_gap_by_pillar": {p: _r(c) for p, c in contributions.items()},
        "driving_pillars": driving,
        "pillar_scores": {a: {p: _r(v) for p, v in ps.items()} for a, ps in pillars.items()},
        "weights": weights,
        "monte_carlo_top_share": {a: _r(v, 3) for a, v in top_share.items()},
        "top_rank_robust": top_rank_robust,
        "evidence": evidence,
    }


def build_decision_context(conn: sqlite3.Connection, area_ids: list[str], now: datetime | None = None) -> dict:
    """One preliminary scoring pass (no network, ~0.05 s)."""
    from scoring import DEFAULT_WEIGHTS, score_all, sensitivity_analysis

    results = score_all(conn, area_ids, DEFAULT_WEIGHTS, now=now)
    sens = sensitivity_analysis(results, DEFAULT_WEIGHTS)
    evidence = {
        a: {
            "score": _r(r["evidence_quality"]["score"]),
            "tier": r["evidence_quality"]["tier"],
            "components": {
                k: {"value": _r(c["value"], 3), "detail": c["detail"]}
                for k, c in r["evidence_quality"]["components"].items()
            },
        }
        for a, r in results.items()
    }
    return summarize_decision(
        {a: r["business_fit"] for a, r in results.items()},
        {a: r["pillars"] for a, r in results.items()},
        dict(DEFAULT_WEIGHTS),
        sens["monte_carlo"]["top_share"],
        sens["top_rank_robust"],
        evidence,
    )


def detect_weak_evidence(context: dict) -> list[GapTrigger]:
    triggers = []
    for area_id in context["ranking"]:
        for name, threshold in WEAK_EVIDENCE_THRESHOLDS.items():
            comp = context["evidence"].get(area_id, {}).get("components", {}).get(name)
            if comp is None or comp["value"] >= threshold:
                continue
            triggers.append(
                GapTrigger(
                    area_id=area_id,
                    gap_type=GapType.WEAK_EVIDENCE,
                    detail=f"{name} {comp['value']:.0%} (< {threshold:.0%}): {comp['detail']}",
                    extra={"component": name, "value": comp["value"], "threshold": threshold,
                           "pillar": _COMPONENT_PILLAR[name]},
                )
            )
    return triggers


def gap_pillars(gap: GapTrigger) -> frozenset[str]:
    """Which business-fit pillar(s) a gap can move."""
    t = gap.gap_type
    if t == GapType.WEAK_EVIDENCE:
        return frozenset({gap.extra["pillar"]})
    if t in (GapType.MISSING_POI_HOURS, GapType.CONFLICTING_OBSERVATION):
        return frozenset({"competition"})
    if t == GapType.STALE_SNAPSHOT:
        return frozenset({"demand" if gap.extra["source_name"] in _DEMAND_SOURCES else "competition"})
    if t == GapType.MISSING_METRIC:
        name = gap.extra["metric_name"]
        pillars = set()
        if any(h in name for h in _DEMAND_METRIC_HINTS):
            pillars.add("demand")
        if "competitor" in name or "competition" in name or "peak" in name:
            pillars.add("competition")
        return frozenset(pillars or {"competition"})
    raise AssertionError(f"unhandled gap type: {t}")


# --------------------------------------------------------------------------
# State, actions, guard
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    gap_id: str
    trigger: GapTrigger

    @property
    def area_id(self) -> str:
        return self.trigger.area_id


@dataclass(frozen=True)
class Action:
    gap_id: str
    tool: str
    question: Optional[str] = None
    rationale: str = ""
    # Rule policies explain a downgrade (live -> flag) here; it becomes
    # the log entry's stop_reason, exactly as Day 4 wrote it.
    note: Optional[str] = None


@dataclass(frozen=True)
class Finish:
    reason: str


Decision = Union[Action, Finish]


@dataclass
class AgentState:
    gaps: list[Gap]
    context: dict
    budget: CallBudget
    handled: dict[str, dict] = field(default_factory=dict)       # gap_id -> log entry
    history: list[dict] = field(default_factory=list)            # one entry per policy step
    called: dict[tuple[str, str], dict] = field(default_factory=dict)  # (area, tool) -> ok result
    failed: set[tuple[str, str]] = field(default_factory=set)

    def gap(self, gap_id: str) -> Gap | None:
        return next((g for g in self.gaps if g.gap_id == gap_id), None)

    def open_gaps(self) -> list[Gap]:
        return [g for g in self.gaps if g.gap_id not in self.handled]

    def live_calls(self) -> int:
        return self.budget.used_total

    def budget_view(self) -> dict:
        b = self.budget
        return {
            "live_calls_remaining_total": b.max_calls_total - b.used_total,
            "live_calls_remaining_by_area": {
                a: b.max_calls_per_area - b._used_by_area.get(a, 0)
                for a in dict.fromkeys(g.area_id for g in self.gaps)
            },
        }


def guard(action: Action, state: AgentState) -> str | None:
    """Rejection reason, or None if the action may run."""
    gap = state.gap(action.gap_id)
    if gap is None:
        return f"unknown gap_id {action.gap_id!r}"
    if gap.gap_id in state.handled:
        return f"{gap.gap_id} was already handled in this run"
    allowed = allowed_tools(gap.trigger)
    if action.tool not in allowed:
        return f"{action.tool!r} is not allowed for {gap.trigger.gap_type.value} (allowed: {sorted(allowed)})"
    if action.tool in LIVE_TOOLS:
        key = (gap.area_id, action.tool)
        if key in state.called:
            return None  # reuses the earlier result, costs nothing
        if key in state.failed:
            return "this source already failed for this area in this run; it is not retried"
        if not state.budget.can_call(gap.area_id):
            return "call budget exhausted"
    return None


# --------------------------------------------------------------------------
# Rule policies
# --------------------------------------------------------------------------


def rule_action(gap: Gap, state: AgentState) -> Action:
    """Day 4's per-gap rule, downgraded to a flag whenever the guard
    would refuse the live call -- so RulePolicy is never rejected."""
    tool = select_tool(gap.trigger)
    if tool in LIVE_TOOLS:
        key = (gap.area_id, tool)
        if key not in state.called:
            if key in state.failed:
                return Action(gap.gap_id, TOOL_FLAG_FIELD_RESEARCH, note=STOP_FAILED_BEFORE)
            if not state.budget.can_call(gap.area_id):
                return Action(gap.gap_id, TOOL_FLAG_FIELD_RESEARCH, note=STOP_BUDGET)
    return Action(gap.gap_id, tool)


class RulePolicy:
    """A: gaps in detection order, select_tool() per gap."""

    name = "rule"
    needs_context = False

    def order(self, state: AgentState) -> list[Gap]:
        return state.open_gaps()

    def next_action(self, state: AgentState) -> Decision:
        gaps = self.order(state)
        if not gaps:
            return Finish("no open gaps")
        return rule_action(gaps[0], state)


class ImpactRulePolicy(RulePolicy):
    """B: same per-gap rule, but gaps that can move the #1-vs-#2
    comparison go first. Sort key: node is #1 or #2, then the gap
    touches a pillar driving their gap, then node rank, then detection
    order. Written before any LLM result existed (see the plan)."""

    name = "impact_rule"
    needs_context = True

    def order(self, state: AgentState) -> list[Gap]:
        ranking = state.context.get("ranking", [])
        driving = set(state.context.get("driving_pillars", []))
        pos = {g.gap_id: i for i, g in enumerate(state.gaps)}

        def key(g: Gap):
            node_rank = ranking.index(g.area_id) if g.area_id in ranking else len(ranking)
            return (node_rank > 1, not (gap_pillars(g.trigger) & driving), node_rank, pos[g.gap_id])

        return sorted(state.open_gaps(), key=key)


# --------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------

ToolFunc = Callable[[sqlite3.Connection, LocationInput], dict]


def _execute(conn: sqlite3.Connection, state: AgentState, gap: Gap, action: Action, node: LocationInput,
             tool_funcs: dict[str, ToolFunc], decided_by: str) -> dict:
    tool, stop_reason = action.tool, action.note
    if tool in LIVE_TOOLS:
        key = (gap.area_id, tool)
        if key in state.called:
            result, stop_reason = state.called[key], STOP_REUSED
        else:
            state.budget.record_call(gap.area_id)
            result = tool_funcs[tool](conn, node)
            if result["status"] == "error":
                state.failed.add(key)
                stop_reason = f"live tool call failed ({result['error']}) -- flagged for field research"
                result = {**result, "fallback": af._tool_flag_field_research(conn, node, gap.trigger)}
                tool = f"{tool}+{TOOL_FLAG_FIELD_RESEARCH}"
            else:
                state.called[key] = result
    else:
        result = af._tool_flag_field_research(conn, node, gap.trigger, question=action.question)

    entry = {
        # Day 4 fields, unchanged -- report.py reads these.
        "area_id": gap.area_id,
        "gap_type": gap.trigger.gap_type.value,
        "detail": gap.trigger.detail,
        "tool": tool,
        "stop_reason": stop_reason,
        "result": result,
        # v2 fields
        "gap_id": gap.gap_id,
        "decided_by": decided_by,
        "rationale": action.rationale or None,
    }
    state.handled[gap.gap_id] = entry
    return entry


def run_agent(
    conn: sqlite3.Connection,
    nodes: list[LocationInput],
    budget: CallBudget,
    policy,
    *,
    now: datetime | None = None,
    context: dict | None = None,
    gaps: list[GapTrigger] | None = None,
    include_weak_evidence: bool = True,
    tool_funcs: dict[str, ToolFunc] | None = None,
    max_steps: int | None = None,
) -> dict:
    """Run one follow-up pass over `nodes`. Gaps are detected once, up
    front, so every policy decides over the same list. Returns
    {"log": [...], "run": {...}}; "log" keeps Day 4's format."""
    now = now or datetime.now(timezone.utc)
    nodes_by_id = {n.area_id: n for n in nodes}
    tool_funcs = tool_funcs or af._TOOL_FUNCS

    if context is None:
        needs = include_weak_evidence or getattr(policy, "needs_context", False)
        context = build_decision_context(conn, list(nodes_by_id), now) if needs else {}
    if gaps is None:
        gaps = [t for n in nodes for t in af.detect_gaps(conn, n.area_id, now)]
        if include_weak_evidence:
            gaps += detect_weak_evidence(context)
    state = AgentState([Gap(f"g{i + 1}", t) for i, t in enumerate(gaps)], context, budget)
    max_steps = max_steps if max_steps is not None else len(state.gaps) + 2

    log: list[dict] = []
    rejections: list[dict] = []
    active, fallback_reason, finish_reason = policy, None, None
    steps = 0
    while True:
        if not state.open_gaps():
            finish_reason = "no open gaps"
            break
        if steps >= max_steps:
            finish_reason = f"max_steps reached ({max_steps})"
            break
        steps += 1
        try:
            decision = active.next_action(state)
        except PolicyError as exc:
            fallback_reason = f"{active.name} failed at step {steps}: {exc}"
            active = RulePolicy()
            decision = active.next_action(state)

        if isinstance(decision, Finish):
            finish_reason = f"{active.name}: {decision.reason}"
            state.history.append({"step": steps, "decision": "finish", "reason": decision.reason})
            break

        step = {"step": steps, "decided_by": active.name, "proposed": _action_dict(decision)}
        rejection = guard(decision, state)
        if rejection is None:
            gap = state.gap(decision.gap_id)
            entry = _execute(conn, state, gap, decision, nodes_by_id[gap.area_id], tool_funcs, active.name)
            log.append(entry)
            step["outcome"] = "executed"
            step["entry"] = entry
        else:
            rejections.append({"step": steps, "proposed": _action_dict(decision), "reason": rejection})
            step["outcome"] = "rejected"
            step["rejection"] = rejection
            gap = state.gap(decision.gap_id)
            if gap is not None and gap.gap_id not in state.handled:
                fb = rule_action(gap, state)
                entry = _execute(conn, state, gap, fb, nodes_by_id[gap.area_id], tool_funcs, "rule_fallback")
                log.append(entry)
                step["rule_fallback"] = entry
        state.history.append(step)

    unhandled = [
        {"gap_id": g.gap_id, "area_id": g.area_id, "gap_type": g.trigger.gap_type.value, "detail": g.trigger.detail}
        for g in state.open_gaps()
    ]
    record = getattr(policy, "run_record", None)
    return {
        "log": log,
        "run": {
            "policy": policy.name,
            "finished_with": active.name,
            "finish_reason": finish_reason,
            "policy_fallback": fallback_reason,
            "steps": steps,
            "max_steps": max_steps,
            "live_calls": state.live_calls(),
            "budget": {"max_calls_total": budget.max_calls_total, "max_calls_per_area": budget.max_calls_per_area},
            "gaps": [{"gap_id": g.gap_id, "area_id": g.area_id, "gap_type": g.trigger.gap_type.value,
                      "detail": g.trigger.detail} for g in state.gaps],
            "guard_rejections": rejections,
            "unhandled_gaps": unhandled,
            "context": context,
            "policy_record": record() if callable(record) else None,
        },
    }


def _action_dict(a: Action) -> dict:
    return {"gap_id": a.gap_id, "tool": a.tool, "question": a.question, "rationale": a.rationale or None}


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)
