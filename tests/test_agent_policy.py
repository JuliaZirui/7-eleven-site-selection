"""v2 agent layer: executor, guard and the two rule policies.

Gaps and decision context are stated directly (no scoring pass), live
tools are fakes -- these tests are about who decides what, not about
the data. The DB is the usual throwaway schema copy, because flagging
writes development_events rows.
"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (sets sys.path for src/)
from helpers import PipelineTestCase

import agent_followup as af
import agent_policy as ap
from agent_followup import CallBudget, GapTrigger, GapType
from models import CANDIDATE_NODES

BT, WS, SA = (n.area_id for n in CANDIDATE_NODES)
OSM, LOR, IHK, FLAG = af.TOOL_REFETCH_OSM, af.TOOL_REFETCH_LOR, af.TOOL_REFETCH_IHK, af.TOOL_FLAG_FIELD_RESEARCH


def stale(area: str, source: str = "osm_overpass") -> GapTrigger:
    return GapTrigger(area, GapType.STALE_SNAPSHOT, f"{source} snapshot is 40 days old", {"snapshot_id": 1, "source_name": source})


def conflict(area: str) -> GapTrigger:
    return GapTrigger(area, GapType.CONFLICTING_OBSERVATION, "Cafe X reported inconsistently", {"external_ref": "node/1", "snapshot_ids": [1, 2]})


def context(driving=("competition",)) -> dict:
    return ap.summarize_decision(
        fits={BT: 80.0, WS: 75.0, SA: 20.0},
        pillars={BT: {"demand": 70.0, "competition": 90.0}, WS: {"demand": 70.0, "competition": 80.0},
                 SA: {"demand": 30.0, "competition": 10.0}},
        weights={"demand": 0.5, "competition": 0.5},
        top_share={BT: 0.7, WS: 0.3, SA: 0.0},
        top_rank_robust=False,
        evidence={},
    )


class FakeTools:
    def __init__(self, fail: set[str] = frozenset()):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def funcs(self) -> dict:
        def make(tool):
            def f(conn, node):
                self.calls.append((tool, node.area_id))
                if tool in self.fail:
                    return {"status": "error", "error": "simulated outage"}
                return {"status": "ok", "detail": {"simulated": True}}
            return f
        return {t: make(t) for t in ap.LIVE_TOOLS}


class Scripted:
    """A policy that proposes a fixed sequence, whatever the state."""

    name = "scripted"
    needs_context = False

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def next_action(self, state):
        return self.decisions.pop(0) if self.decisions else ap.Finish("script exhausted")


class AgentPolicyTestCase(PipelineTestCase):
    nodes = CANDIDATE_NODES

    def run_agent(self, policy, gaps, budget=(4, 2), tools=None, ctx=None, **kw):
        tools = tools or FakeTools()
        out = ap.run_agent(self.conn, list(CANDIDATE_NODES), CallBudget(*budget), policy,
                           context=ctx if ctx is not None else context(), gaps=gaps,
                           tool_funcs=tools.funcs(), **kw)
        return out, tools


class DecisionContext(unittest.TestCase):
    def test_driving_pillars_from_top_two_gap(self):
        ctx = context()
        self.assertEqual(ctx["ranking"], [BT, WS, SA])
        self.assertAlmostEqual(ctx["top_two_gap"], 5.0)
        self.assertEqual(ctx["top_two_gap_by_pillar"], {"demand": 0.0, "competition": 5.0})
        self.assertEqual(ctx["driving_pillars"], ["competition"])

    def test_weak_evidence_only_below_threshold(self):
        ctx = context()
        ctx["evidence"] = {
            BT: {"components": {"osm_mapping_consistency": {"value": 0.48, "detail": "32 per 66"},
                                "competitor_corroboration": {"value": 0.53, "detail": "17/32"}}},
            WS: {"components": {"osm_mapping_consistency": {"value": 0.61, "detail": "73 per 119"}}},
        }
        gaps = ap.detect_weak_evidence(ctx)
        self.assertEqual([(g.area_id, g.extra["component"]) for g in gaps], [(BT, "osm_mapping_consistency")])
        self.assertEqual(af.allowed_tools(gaps[0]), frozenset({FLAG}))
        self.assertIn("competition", af._field_research_question(gaps[0]))

    def test_gap_pillars(self):
        self.assertEqual(ap.gap_pillars(stale(BT, "berlin_mss_population")), {"demand"})
        self.assertEqual(ap.gap_pillars(stale(BT, "poi_conflated")), {"competition"})
        density = GapTrigger(BT, GapType.MISSING_METRIC, "x", {"metric_name": "competitor_density_per_1000_residents"})
        self.assertEqual(ap.gap_pillars(density), {"demand", "competition"})


class Guard(AgentPolicyTestCase):
    def test_rejects_unknown_gap_disallowed_tool_and_handled_gap(self):
        out, tools = self.run_agent(Scripted([
            ap.Action("g9", OSM),                 # unknown gap
            ap.Action("g1", OSM),                 # conflict: flag only
            ap.Action("g2", FLAG),
            ap.Action("g2", FLAG),                # already handled
        ]), [conflict(BT), stale(WS), stale(SA)])
        reasons = [r["reason"] for r in out["run"]["guard_rejections"]]
        self.assertIn("unknown gap_id", reasons[0])
        self.assertIn("not allowed for conflicting_observation", reasons[1])
        self.assertIn("already handled", reasons[2])
        self.assertEqual(tools.calls, [])  # g3 is left to the script, which finishes
        # The rejected conflict proposal was handled by the rule instead.
        g1 = next(e for e in out["log"] if e["gap_id"] == "g1")
        self.assertEqual((g1["tool"], g1["decided_by"]), (FLAG, "rule_fallback"))

    def test_budget_is_enforced_whatever_the_policy_proposes(self):
        gaps = [stale(BT), stale(BT, "ihk_gewerbedaten"), stale(WS), stale(SA)]
        out, tools = self.run_agent(Scripted([ap.Action(f"g{i}", t) for i, t in
                                              [(1, OSM), (2, IHK), (3, OSM), (4, OSM)]]), gaps, budget=(1, 1))
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(out["run"]["live_calls"], 1)
        self.assertEqual(len(out["run"]["guard_rejections"]), 3)
        self.assertTrue(all(e["tool"] == FLAG for e in out["log"][1:]))

    def test_failed_source_is_not_retried(self):
        gaps = [stale(BT), stale(BT, "poi_conflated")]
        out, tools = self.run_agent(Scripted([ap.Action("g1", OSM), ap.Action("g2", OSM)]), gaps,
                                    tools=FakeTools(fail={OSM}))
        self.assertEqual(tools.calls, [(OSM, BT)])
        self.assertIn("not retried", out["run"]["guard_rejections"][0]["reason"])
        self.assertEqual(out["log"][1]["stop_reason"], ap.STOP_FAILED_BEFORE)

    def test_second_gap_on_same_source_reuses_the_call(self):
        out, tools = self.run_agent(ap.RulePolicy(), [stale(BT), stale(BT, "poi_conflated")])
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(out["log"][1]["stop_reason"], ap.STOP_REUSED)
        self.assertEqual(out["run"]["live_calls"], 1)

    def test_run_ends_at_max_steps_and_reports_unhandled_gaps(self):
        stubborn = Scripted([ap.Action("g9", OSM)] * 50)
        out, _ = self.run_agent(stubborn, [stale(BT), stale(WS)])
        self.assertEqual(out["run"]["steps"], 4)  # gaps + 2
        self.assertIn("max_steps", out["run"]["finish_reason"])
        self.assertEqual([g["gap_id"] for g in out["run"]["unhandled_gaps"]], ["g1", "g2"])

    def test_finish_leaves_remaining_gaps_unhandled(self):
        out, tools = self.run_agent(Scripted([ap.Action("g1", FLAG), ap.Finish("cannot change the ranking")]),
                                    [stale(SA), stale(SA, "berlin_mss_population")])
        self.assertEqual(tools.calls, [])
        self.assertEqual(out["run"]["finish_reason"], "scripted: cannot change the ranking")
        self.assertEqual([g["gap_id"] for g in out["run"]["unhandled_gaps"]], ["g2"])

    def test_policy_error_hands_the_rest_of_the_run_to_rules(self):
        class Broken(Scripted):
            def next_action(self, state):
                if not self.decisions:
                    raise ap.PolicyError("API timeout")
                return super().next_action(state)

        out, _ = self.run_agent(Broken([ap.Action("g1", FLAG)]), [stale(BT), stale(WS)])
        self.assertIn("API timeout", out["run"]["policy_fallback"])
        self.assertEqual(out["run"]["finished_with"], "rule")
        self.assertEqual([e["decided_by"] for e in out["log"]], ["scripted", "rule"])
        self.assertEqual(out["run"]["unhandled_gaps"], [])


class RulePolicies(AgentPolicyTestCase):
    def test_rule_policy_spends_budget_in_detection_order(self):
        out, tools = self.run_agent(ap.RulePolicy(), [stale(SA), stale(BT)], budget=(1, 1))
        self.assertEqual(tools.calls, [(OSM, SA)])
        self.assertEqual(out["log"][1]["stop_reason"], ap.STOP_BUDGET)
        self.assertEqual(out["run"]["guard_rejections"], [])

    def test_impact_policy_spends_budget_on_the_top_two_first(self):
        out, tools = self.run_agent(ap.ImpactRulePolicy(), [stale(SA), stale(BT)], budget=(1, 1))
        self.assertEqual(tools.calls, [(OSM, BT)])
        self.assertEqual(out["run"]["guard_rejections"], [])

    def test_impact_policy_prefers_the_driving_pillar(self):
        gaps = [stale(BT, "berlin_mss_population"), stale(WS), stale(BT)]  # demand at #1, competition at #2 and #1
        out, tools = self.run_agent(ap.ImpactRulePolicy(), gaps, budget=(4, 2))
        self.assertEqual([e["gap_id"] for e in out["log"]], ["g3", "g2", "g1"])


if __name__ == "__main__":
    unittest.main()
