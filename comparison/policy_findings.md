# Follow-up policy: findings and decision

2026-10-02. This closes Phase 4 of [plan_llm_tool_selection.md](../plan_llm_tool_selection.md). Numbers come from [policy_eval.md](policy_eval.md). Scenarios and expected decisions were pre-registered in [policy_scenarios.md](policy_scenarios.md) before any policy ran.

## Question

The follow-up agent decides which data gaps to spend a small budget of live API calls on, and which gaps to hand to field research. Does an LLM make these decisions better than rules?

## Result

| | A. Rule | B. Impact rule | C. LLM (`claude-opus-5-5`, effort medium) |
| --- | --- | --- | --- |
| Agreement, 14 ordering scenarios | 10 / 14 | **14 / 14** | **14 / 14** (all 5 runs) |
| Agreement, 2 "do nothing" scenarios | 0 / 2 | 0 / 2 | **2 / 2** (all 5 runs) |
| Live calls, one pass over all 16 scenarios | 13 | 13 | 9 |
| …spent on gaps that cannot move the #1 vs #2 call | 8 | 4 | 0 |
| Proposals rejected by the guard | 0 | 0 | 0 |
| Runs handed back to rules (API failure) | – | – | 0 of 80 |
| Cost and time per run | $0, < 0.01 s | $0, < 0.01 s | $0.019, 10.3 s |

The full LLM evaluation (80 runs) cost $1.50, against a plan estimate of $10–20.

## What the numbers say

**1. On ordering, B and C tie.** Every ordering scenario that the rule baseline A got wrong (S01, S02, S09, S16) is also solved by B. B is twenty lines of deterministic sorting: gaps on the top two nodes and on the pillar that drives their gap go first. On this part of the task, the LLM adds nothing that a cheaper rule cannot do.

**2. C's measurable edge is declining a call.** In S07 and S15, no live call can change which node gets diligence first, and C spent none. Its reasons were sound. For S15 it noted a 29-point lead, of which 21.8 points come from competition; a ranking that holds in 100% of random weightings; and a gap that only touches demand. A and B cannot decline by design: they have no rule for it. The pre-registration says C must not win on these two scenarios alone, so this counts as a capability B lacks, not as proof that C judges better. A rule could probably cover these two cases, but that is untested (see Next steps).

**3. The clearest qualitative difference is the field question.** Same gaps, run 0:

| Gap | A and B (template, identical) | C |
| --- | --- | --- |
| S10: weak corroboration at Warschauer Straße | "Verify on-site before relying on the competition score -- competitor_corroboration 45% (< 50%): 33/73 competitors seen by >= 2 independent providers." | Names the node and the pillar. Points out that OSM also maps only 61% of IHK registrations. Asks the analyst to walk the radius, count open competitors by type, and check the 40 single-source records. Ends: "Competition is the only pillar separating this node from Brandenburger Tor (5 points), so a true count higher or lower than the map shows could change which node gets diligence first." |
| S12: stale attraction data at Brandenburger Tor (no refresh tool) | "Re-verify before finalizing the comparison -- osm_attractions_gateway snapshot #140 is 40 days old (>= 14)." | Ties the gap to visitor demand. Asks for closures, construction or events, and a 15-minute frontage count at weekday midday and early evening. Explains that the 5-point lead is not robust (62% Monte Carlo share). |

Spot checks found no factual errors: the figures C cites (62% share, 70 vs 70 on demand, 61% mapping rate) match the decision context. Two weaknesses:
- **Length.** The median question is 671 characters, far longer than a checklist item.
- **Scope creep.** One question (S04) adds a second task: "while on site, also count…".

## Caveats

- **Expectations and prompt share an author.** I wrote the expected decisions and the system prompt, and the prompt states the same principle the expectations test: spend calls where they can move the top-two comparison. C's agreement therefore shows that it reads the context and applies the stated principle correctly and consistently. It does not show independent judgement.
- **Consistency is measured on the first decision only.** That measure is 100%, but later steps vary. In S09, how many of the two leftover demand gaps C flagged differed across the 5 runs: none in 3 runs, one in 1, both in 1.
- **C leaves gaps unhandled.** When it finishes early, gaps it judged irrelevant get neither a refetch nor a flag: the #3 node's gaps in S01, S07 and S13, the #2 node's demand gap in S15, and the demand gaps in S09. A and B flag everything, so their field checklist is complete; C's is not.
- **The question grading was not done (deviation from the pre-registration).** The plan called for blind 0–3 grading. Template questions are instantly recognisable, so the grading could not have been blind, and it was dropped. Question quality above is a qualitative judgement with examples, not a score.
- **Constructed scenarios, small world.** There are three nodes. Today's real data contains no budget decision at all (every gap is flag-only), and the live tools were simulated. Only one model and one effort level were tested.

## Decision

The outcome matches the plan's middle row: **"C ≈ B → keep rules, possibly use the LLM only to write field questions."**

1. **The default policy becomes B (`impact_rule`).** It is deterministic, free, and fixes four of A's six failures. The real data is unaffected: it only reorders flags.
2. **C stays available** as `--policy llm`. The guard and the rule fallback stay as they are. The scoring path is unchanged and LLM-free, and the end-to-end reproducibility check still holds.
3. **Proposed next step: a hybrid.** B makes the decisions, and the LLM only rewrites the question for each flag, with a length limit and one task per question. Any gap left open at finish is flagged with the template question, so the checklist stays complete.

## Next steps

- **Do-nothing rule for B.** Skip live calls on gaps that are both off the top two nodes and off the driving pillar, when the ranking is robust. This needs a *new* set of scenarios written before it runs, labelled as post-hoc relative to this evaluation. S07 and S15 cannot be reused to validate it.
- **Hybrid question writer** (decision 3), evaluated on the same 8 graded gaps. It costs about one short call per flag.
- **Citywide run (139 stations).** This is where the budget becomes a real constraint, and where any difference between the policies matters in practice.
