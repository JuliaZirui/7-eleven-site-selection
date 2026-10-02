"""The rule baselines on the pre-registered scenarios.

policy_scenarios.md predicted from the code, before any run, which
scenarios A and B fail. This pins that prediction: a change to the
rule policies that moves a baseline result has to show up here.
"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401

import policy_eval
from policy_scenarios import SCENARIOS

PREDICTED_FAILURES = {
    "rule": {"S01", "S02", "S07", "S09", "S15", "S16"},
    "impact_rule": {"S07", "S15"},
}


class BaselinesMatchPreregistration(unittest.TestCase):
    def test_scenario_ids_are_unique_and_gap_ids_exist(self):
        self.assertEqual(len({s.sid for s in SCENARIOS}), len(SCENARIOS))
        for s in SCENARIOS:
            ids = {f"g{i + 1}" for i in range(len(s.gaps))}
            named = {g for g, _ in s.expect.must + s.expect.must_not + s.expect.first_live} | set(s.relevant) | set(s.graded)
            self.assertLessEqual(named, ids, s.sid)

    def test_rule_baselines_fail_exactly_the_predicted_scenarios(self):
        for policy, predicted in PREDICTED_FAILURES.items():
            failed = {s.sid for s in SCENARIOS if not policy_eval.run_scenario(s, policy)["agree"]}
            self.assertEqual(failed, predicted, policy)

    def test_rule_baselines_never_trip_the_guard(self):
        for policy in PREDICTED_FAILURES:
            for s in SCENARIOS:
                self.assertEqual(policy_eval.run_scenario(s, policy)["guard_rejections"], 0, (policy, s.sid))


if __name__ == "__main__":
    unittest.main()
