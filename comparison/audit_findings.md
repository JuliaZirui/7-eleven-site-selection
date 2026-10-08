# Data-Plausibility Audit — Findings and Decision

2026-10-08 · Experiment of [plan_llm_data_audit.md](../plan_llm_data_audit.md) (Amendments 1–4). Numbers: [audit_eval.md](audit_eval.md) (generated), the evaluation runs' review rows: [audit_eval_queue.csv](audit_eval_queue.csv) (kept as recorded; the live queue `audit_review_queue.csv` holds pipeline runs only), [audit_candidate_recall.md](audit_candidate_recall.md), [audit_prompt_log.md](audit_prompt_log.md); labels: [audit_flag_labels.csv](audit_flag_labels.csv); manual scans: [audit_manual_scans/](audit_manual_scans/).

## Decision

**By the pre-registered rule: Adopt** — holdout recall 4 / 6 (threshold ≥ 4 / 6) and false alarms per node, median of 3 runs, 1 at the holdout and 3 at the control (threshold ≤ 3).

Both criteria sit exactly on their thresholds. One more missed holdout item, or one more false alarm per run at the control, would have given "keep as an optional assistant". Read the result as *just adopted*, with these qualifications attached:

- **Main evidence H1–H3: 2 / 3.** H3 is missed in all 3 runs (below).
- The 4 / 6 includes **H5, contaminated** (its candidate rule was revised after its candidate recall had been seen, Amendment 2), and **H6, in-sample** for the conflation_v2 rules the generator mirrors (Amendment 1). Without H4–H5: 3 / 4.
- **The holdout has six items.** The difference between adopt and optional is one item.

What "adopt" means here is limited by design: the auditor never changes a number. It writes flags to a review queue; scoring reads only the reviewed correction tables.

## What the auditor found

**Holdout (Brandenburger Tor, 3 runs, frozen prompt v4).** Caught in all 3 runs: the company-wide registration at one address (H1; every proposed range overlaps the verified one), the public-sector offices missing from IHK (H2), the office building listed as a café (H5), the duplicate bakery (H6). Missed:

- **H3, fame vs attendance (0 / 3).** The auditor found a source giving about 3 million visits a year and judged the pipeline's 4.8 million to be within the factor-2 tolerance; the answer key's verified figure is 1.85 million. A source disagreement, not a failure to look; it still counts as a miss.
- **H4, category error (1 / 3, contaminated).** Since Amendment 2 the record reaches the worklist only inside a duplicate pair. In all three runs the auditor's reason says the restaurant is miscategorised, but it rates the pair, not the category.

The analyst confirmed 13 further holdout flags as true issues not in the answer key (unregistered public bodies beyond the 22 known buildings, a restaurant listed as a café, a café that no longer exists). 3 flags were unclear, 1 a false alarm.

**Control (Schönhauser Allee).** 18 flagged candidates: 15 true issues, 3 false alarms (2, 3, 3 per run).

**Development (Warschauer Straße), frozen v4.** D1–D4 caught; proposed ranges overlap the verified ones for D1, D2 and D4.

**Consistency:** 4 of 6 holdout items flagged in all 3 runs. Flag counts per node vary by at most 3 between runs.

## Generalisation stations (Kurfürstendamm, Rosenthaler Platz, Deutsche Oper)

The analyst scanned each station by hand (16–20 minutes, Google Maps) and committed the scan before any auditor output for these stations was opened.

**Issues the scan found and the auditor missed.** 46 manual findings. About 19 reached the audit worklist at all; the auditor flagged about 12 of those in at least 2 of 3 runs (including 7 registrations of one quick-commerce company at its head office, flagged as company-wide headcount where the scan said duplicate: the same problem under the type the prompt prescribes). About 27 never reached the worklist. 6 of the 46 (wine shops, a drugstore) are competitors under the current taxonomy on purpose; the analyst holds that **the scope definition itself is wrong** ([scope_note.md](audit_manual_scans/scope_note.md)). They are reported as a challenge to the scope, not as data errors the auditor missed. Without them: about 12 of 40.

**Issues the auditor found and the scan missed.** 58 flagged candidates, all labelled by the analyst: 57 true issues and 1 false alarm. 19 of the true issues correspond to scan findings; **38 were found by the auditor only**. For 7 of the 57 the analyst's note gives a different problem than the flag (a "duplicate" pair whose shops are both closed, or are restaurants); the record is wrong, the auditor's reason is not.

How much to trust those two numbers:

- The **auditor-only 38** rests on labels given *after reading the auditor's reasons*, not blind; 57 of 58 confirmed is unusually high and should be read as "the flagged record has some problem", an upper bound on the auditor's precision.
- The **scan** is one person, 16–20 minutes per station, not blind to the issue types, with no evidence recorded for each finding.

## The largest gap: the candidate generator, not the auditor

Most of what the scan found and the audit missed never reached the auditor. The `poi_category` rule only lists competitors whose name carries a non-food word or that disagree with a same-name OSM object within 15 m; restaurants tagged as cafés, closed shops and shops out of scope pass it. The rules are frozen since Amendment 2; changing them needs a new holdout to evaluate.

## Cost

$14.12 in total at list prices for everything: the Opus trial, development v1–v4, and 15 evaluation runs. A full run of one node with the frozen prompt costs $0.25–1.25 (about $0.023 per candidate), with Claude Sonnet 5.5 at effort medium. The POI modules run without web search (Amendment 3).

## Problems found on the way (fixed, recorded)

- **Dynamic-filtering web search** (`web_search_20260209`): malformed calls rejected by the server, and model-side filtering that dropped the sentence a finding needed. Replaced by the basic tools (Amendment 4).
- **Runs without credentials** wrote empty run records that would have been counted, and records written in the same second overwrote each other. The CLI now checks the API before any work, keeps API-less runs apart, and never overwrites a run file.
- **A spreadsheet round-trip** changed the label sheet's delimiter and one computed column. The computed values were restored; the analyst's associations were kept as notes.

## Recommendation

Follow the rule and wire the audit into the pipeline (Phase 5), as a step whose output is only the review queue and one line per node in the report. Because the decision is marginal, keep two things visible:

1. The report line states what is still unreviewed, and the README states that the audit's recall on issues outside its worklist is low.
2. Improve the candidate generator next, especially competitor category checks and closed shops. That is the measured bottleneck; any new rule needs a fresh holdout.

The competitor-scope question raised by the analyst is a separate decision for the scoring model, outside this experiment.

## Limitations that travel with this result

1. The answer key is in-sample for the person who designed the checks and wrote the prompt (disclosed in the prompt log and the amendments).
2. Six holdout items; the adopt/optional boundary is one item.
3. Flags were labelled by one analyst who could see the auditor's reasons.
4. The generalisation scan is one person, 20 minutes per station, not blind to the issue types.
5. Web search results change; run records keep the URLs and quotes used.
