# Data-Plausibility Audit — Answer Key and Pre-Registration

2026-10-07 · For [plan_llm_data_audit.md](../plan_llm_data_audit.md). Written before any audit code, candidate rule or prompt exists. Commit hash of this file's first version: `ec70403` (branch `plan/llm-data-audit`).

> **For whoever writes the audit prompt:** do not copy anything from this file into the prompt, few-shot examples or keyword lists. `tests/test_audit_prompt.py` reads the entity names and raw values below and fails if any of them appear there.

## What counts as an issue here

An item is in the key if, in the corrections-off state, a record carries a value that the manual review found to be **off by more than 2×**, or an entity worth ≥ 1,000 people/day is **absent**, or a competitor record is **not a competitor / a duplicate**. Items below 2× are listed as secondary and scored separately.

All items were found by the analyst before this experiment (see [site_candidates.md](../site_candidates.md) v4.1–v5.2 and [manual_vs_tool.md](manual_vs_tool.md)). The POI items (H4–H6) are still uncorrected in the current data.

## Development set — Warschauer Straße (prompt may be tuned on these)

| ID | Record | Type | Raw value (corrections off) | Verified (low–central–high) | Caught if |
| --- | --- | --- | --- | --- | --- |
| D1 | Night venue, de title `Berghain` | fame_vs_attendance | ~5.9 M visitors/yr from pageview conversion | 56,000–115,000–240,000 /yr | Flagged ≥ suspicious on this venue, type fame_vs_attendance |
| D2 | IHK `1126051271504`, 500–999 | company_wide_headcount | 707 | 100–250–500 | Flagged ≥ suspicious on this registration, type company_wide_headcount |
| D3 | IHK `146410147338`, 500–999, registered < 1 year | company_wide_headcount | 707 | 10–50–150 | Same |
| D4 | Employer absent from IHK at Tamara-Danz-Straße 13 | missing_entity | 0 | 2,500–3,400–3,400 | A missing_entity finding within 100 m of that address |
| D5 *(secondary, < 2×)* | IHK `1146940271134` and `1315585271284`, 2,500–4,999 each | company_wide_headcount | 3,535 each | 870–1,040–1,140 and 2,500–2,710–2,980 | Either registration flagged ≥ suspicious |

## Holdout — Brandenburger Tor (frozen prompt, no tuning)

| ID | Record | Type | Raw value | Verified | Caught if |
| --- | --- | --- | --- | --- | --- |
| H1 | IHK `17303106124`, 5,000–7,499 | company_wide_headcount | 6,123 | 300–450–600 | Flagged ≥ suspicious on this registration, type company_wide_headcount |
| H2 | Public-sector offices (parliament, embassies, ministry), 22 buildings in [worker_onsite_unregistered.csv](../db/raw/calibration/worker_onsite_unregistered.csv) | missing_entity | 0 | 5,097–6,629–8,158 people in total | missing_entity findings covering ≥ 3 of those buildings (within 50 m), or one node-level finding naming the parliament office complex |
| H3 | Day attraction `Reichstagsgebäude` | fame_vs_attendance | ~4.8 M visitors/yr from pageview conversion | 1.85 M /yr published | Flagged ≥ suspicious, type fame_vs_attendance |
| H4 | POI `overture:311992c2-4d9a-425d-a613-c22c9838dfa2`, category `cafe` | category_error | counted as café | a steakhouse restaurant | Flagged ≥ suspicious, type category_error |
| H5 | POI `overture:e99aea2b-c4e5-4e25-a80a-0f62d57d87e2`, category `cafe` | category_error | counted as café | an office building | Same |
| H6 | POIs `node/5912146257` and `overture:5120cae2-6961-4b33-89ac-f53f02d99819`, both bakery, ~8 m apart | duplicate | counted twice | one bakery | Flagged as duplicate pair |

Issue types H4–H6 (category error, duplicate) have **no development instance**. Their result tests whether the generic type descriptions are enough on their own.

## Control — Schönhauser Allee

No known issues. Every flag is labelled by the analyst; confirmed ones are reported as new findings, not as false alarms.

## Generalisation stations

Drawn on 2026-10-07 with `random.Random(20261007).sample(pool, 3)`, where `pool` = the alphabetically sorted station names in `db/raw/calibration/demand_anchors_20261002T092010Z.json` minus the three candidates (136 stations):

1. **Kurfürstendamm**
2. **Rosenthaler Platz**
3. **Deutsche Oper**

Protocol: 20-minute manual scan per station **before** reading the auditor's output, then label every flag (see the plan).

## Decision thresholds

Proposed; edit here and commit **before** Phase 3 runs.

| Holdout recall (H1–H6) | False alarms per node (median of 3 runs) | Decision |
| --- | --- | --- |
| ≥ 4 / 6 | ≤ 3 | Adopt as default pre-scoring audit |
| 2–3 / 6, or > 3 false alarms | — | Keep as optional `--audit` |
| < 2 / 6 | — | Drop |

Recall counts an item as caught if it is caught in at least 2 of 3 runs.
