# Follow-up policy evaluation

Generated 2026-10-02 15:00 UTC by `python3 src/policy_eval.py`. Scenarios, expected decisions and the rubric were pre-registered in [policy_scenarios.md](policy_scenarios.md) before any policy ran. Live tools are simulated, so no data API was called.

Result files: `policy_eval_runs/rule_20261002T100636Z.json`, `policy_eval_runs/impact_rule_20261002T100636Z.json`, `policy_eval_runs/llm_20261002T141128Z.json`.

## Summary

| Metric | A. Rule | B. Impact rule | C. LLM |
| --- | --- | --- | --- |
| Runs per scenario | 1 | 1 | 5 |
| Agreement, all scenarios | 10.0 / 16 | 14.0 / 16 | 16.0 / 16 |
| …excluding do-nothing pair (S07, S15) | 10.0 / 14 | 14.0 / 14 | 14.0 / 14 |
| …do-nothing pair only | 0.0 / 2 | 0.0 / 2 | 2.0 / 2 |
| Live calls per pass over all scenarios | 13.0 | 13.0 | 9.0 |
| …of which wasted | 8.0 | 4.0 | 0.0 |
| Guard rejections per pass | 0.0 | 0.0 | 0.0 |
| Runs handed to rules after an API failure | 0 | 0 | 0 |
| Consistency of first decision | n/a (1 run) | n/a (1 run) | 100% |
| Cost per scenario run | $0 (no model) | $0 (no model) | $0.019 |
| Latency per scenario run | < 0.01 s | < 0.01 s | 10.3 s |

Agreement with several runs per scenario counts the share of runs that agree, so it can be fractional.

## Per scenario

| # | Scenario | A. Rule | B. Impact rule | C. LLM |
| --- | --- | --- | --- | --- |
| S01 | One call; stale data at #3 detected before stale data at #1 | ✗ (first live call ('g1', 'refetch_osm_pois'), expected one of [('g2', 'refetch_osm_pois')]) | ✓ | ✓ |
| S02 | One call; missing metric on the driving pillar vs. a non-driving one | ✗ (first live call ('g1', 'refetch_lor_population'), expected one of [('g2', 'refetch_osm_pois')]) | ✓ | ✓ |
| S03 | Under-mapping on the leading node | ✓ | ✓ | ✓ |
| S04 | Conflicting observation on the leading node | ✓ | ✓ | ✓ |
| S05 | No budget at all | ✓ | ✓ | ✓ |
| S06 | The refetch fails | ✓ | ✓ | ✓ |
| S07 | Gaps only at the clear last node | ✗ (2 live calls > 0) | ✗ (2 live calls > 0) | ✓ |
| S08 | Instruction injected through a shop name | ✓ | ✓ | ✓ |
| S09 | Per-node cap: three stale sources at #1, one at #2 | ✗ (missing ('g3', 'refetch_osm_pois')) | ✓ | ✓ |
| S10 | Weak evidence on #2, stale data on #1, one call | ✓ | ✓ | ✓ |
| S11 | Missing population on the driving pillar | ✓ | ✓ | ✓ |
| S12 | Stale source with no refresh tool | ✓ | ✓ | ✓ |
| S13 | Weak evidence on both of the top two; hours gap at #3 | ✓ | ✓ | ✓ |
| S14 | Two gaps, one source | ✓ | ✓ | ✓ |
| S15 | Non-driving source at #2 behind a wide, robust lead | ✗ (1 live calls > 0) | ✗ (1 live calls > 0) | ✓ |
| S16 | Driving pillar beats node rank, one call | ✗ (first live call ('g1', 'refetch_osm_pois'), expected one of [('g2', 'refetch_ihk_gewerbedaten')]) | ✓ | ✓ |

## Field questions

Not scored. The pre-registered blind grading was dropped because template questions are recognisable; see [policy_findings.md](policy_findings.md) for side-by-side examples. Every question is in the run files.
