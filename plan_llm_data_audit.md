# Implementation Plan — LLM Data-Plausibility Audit (v3)

2026-10-07 · Status: Phase 1 done ([comparison/audit_candidate_recall.md](comparison/audit_candidate_recall.md)); candidate rules frozen (Amendment 2); Phase 2 next. Pre-registration: [comparison/audit_answer_key.md](comparison/audit_answer_key.md) (committed together with this plan, before any audit code or prompt exists).

## Goal

Replace the manual "does this number make sense?" pass with an LLM auditor that reads the pipeline's inputs and intermediate figures and flags the ones that contradict what is known about the real world. Then measure, on issues that were already found by hand, how many it finds, how many false alarms it raises, and what it costs.

The auditor **never changes a number**. It proposes rows for the existing correction tables; a person accepts or rejects each one; scoring keeps reading only the reviewed tables. Scoring stays deterministic and reproducible.

Like [plan_llm_tool_selection.md](plan_llm_tool_selection.md), this is an experiment. "It finds too little to replace the manual pass" is an acceptable outcome and will be reported as such.

## Why this step, and why an LLM

Between v4 and v5.2 the largest changes to the result came from plausibility checks done by hand, not from code or weights (see the version history in [site_candidates.md](site_candidates.md)). They fall into a few types:

| Type | What goes wrong | Why code alone does not catch it |
| --- | --- | --- |
| Fame ≠ attendance | A venue's Wikipedia fame, converted to visitors, exceeds what its capacity or published attendance allows | Needs the venue's capacity or published count, which is in the world, not in the data |
| Company-wide headcount at one address | An IHK size class belongs to the whole company but is filed at one Berlin address | Needs to know who the registration is, and how many people actually work there |
| Missing entity | A large employer or venue is absent from the source (registered elsewhere, or outside IHK's scope, e.g. the public sector) | Absence leaves no record to check; needs an expectation of what should be there |
| Category error | A provider tags a restaurant or an office building as a café | Needs to know what the named place is |
| Duplicate across providers | The same shop under two different names a few metres apart | Name matching fails by design; needs judgement on whether two names are one place |

All five need world knowledge plus the ability to look things up and cite them. That is the part an LLM with web search can do, and the part the current pipeline delegates to the analyst. The corrections the analyst made live in three CSV files, so the result of each manual check is recorded with its basis and sources. Those files are the answer key for this experiment.

## Design

### 1. Corrections-off switch (deterministic)

Add `corrections: bool = True` to the functions that load the three correction tables and the published-attendance override:

- `demand_model.load_onsite_verifications()` → `worker_onsite_verified.csv`
- `demand_model.load_unregistered_employers()` → `worker_onsite_unregistered.csv`
- `demand_model.load_known_attendance()` → `venue_attendance_verified.csv` **and** the published counts from the visitor fit

With `corrections=False` the pipeline reproduces the raw, pre-review state: registrations at their size-class midpoint, no hand-added employers, every visitor figure from the fame conversion. Expose it as `--raw` on the metrics run, writing to a **copy** of the database (as `run_day6.py` already does), never to `db/site_selection.db`.

Test: with `corrections=False`, the answer-key records show their raw values (e.g. a 5,000–7,499 registration counts 6,123).

### 2. Candidate generator (deterministic, `src/audit_candidates.py`)

Code decides **what** gets audited, so the LLM's cost is bounded and every run sees the same worklist. Modules, one per issue type:

| Module | Candidates | Context attached |
| --- | --- | --- |
| `concentration` | Any single record contributing ≥ 25% of a node's worker or visitor segment, or ≥ 1,000 people/day | The record, the segment total, the conversion used |
| `large_registration` | Every IHK registration with size-class floor ≥ 500 | Size class, NACE code and text, registration age, plus **OSM names within 40 m** (building `name`, `office`, `company`, `operator`, address tags). IHK data are anonymised, so this is the identification hint the analyst used by hand |
| `missing_entity` | (a) OSM buildings in the circle tagged office / government / embassy / company, or with floor area ≥ 5,000 m², with no IHK registration within 40 m; (b) entities the LLM itself lists as "expected major employers or venues near this station" that code cannot match to any record within 100 m | Floor area (footprint × levels), tags, nearest station |
| `poi_category` | Competitors whose provider category conflicts with OSM tags or name keywords at the same location, or whose name contains words from a non-food list (derived from OSM tag values only, see Amendment 2; the list lives in code and is reviewed) | All provider categories, OSM tags within 15 m |
| `poi_duplicate` | Pairs of competitors in the same category within 15 m that conflation kept apart | Both records, distance, providers |

Every candidate gets a stable `candidate_id` (module + record ref).

**Measured separately: candidate recall.** If the generator never lists an answer-key item, the LLM cannot find it. Phase 1 reports how many answer-key items reach the worklist before any LLM runs.

### 3. LLM auditor (`src/audit_llm.py`)

Reuses the client pattern of [`src/agent_llm_policy.py`](src/agent_llm_policy.py) (same model constant, prompt caching, injected fake client in tests, `LlmPolicyError`-style failure handling). One call per node and module, with the API's server-side web search tool enabled (check the current tool version in the API docs before implementing).

Output = one `report_finding` tool call per candidate, validated against a JSON schema:

```text
candidate_id | verdict: plausible | suspicious | implausible
issue_type: fame_vs_attendance | company_wide_headcount | missing_entity | category_error | duplicate | other
reason (≤ 400 chars)
proposed: {low, central, high, unit} | null
sources: [{url, quote}]          # quote = the sentence that states the number
evidence_needed (≤ 200 chars)    # what a person would check to settle it
```

For `missing_entity (b)` the model may also return `new_entity` findings with name, coordinates and proposed headcount; code rejects any whose coordinates fall outside the 400 m circle.

### 4. Guard (plain code, between the model and anything stored)

- `candidate_id` must exist in the worklist; otherwise the finding is dropped and logged.
- **No source, no number.** A `proposed` value without at least one source carrying a non-empty quote is stripped, and the verdict is capped at `suspicious`. The finding stays, as a question for a person.
- A source URL must have been returned by the web search tool in the same call (the guard compares against the search results in the response), so the model cannot cite a page it did not retrieve.
- Record names and tags are data. The prompt says so, and the existing injection scenario (S08 in [policy_scenarios.md](comparison/policy_scenarios.md)) gets an audit counterpart.
- Budget: max searches per call and max USD per run, both constants; an exhausted budget ends the run with the remaining candidates marked `not_audited`.

### 5. Review queue, not automatic correction

- Findings → `db/raw/audit/<stamp>.json` (full record) and `comparison/audit_review_queue.csv` (one row per proposal, columns matching the target correction table + `review_status` = pending / accepted / rejected + `reviewer_note`).
- `src/audit_apply.py` appends **accepted** rows to the matching correction CSV with `added_by=audit_llm` and the reviewer's name. Scoring never reads the queue.
- The report's §6 gains one line per node: "AI audit: N flags, M accepted, K pending", so a reader can see what is still unchecked.

### 6. Prompt hygiene (the leakage problem)

The person and the agent writing the prompt can read [site_candidates.md](site_candidates.md), which describes every answer-key issue by name. Two mechanical guards, because good intentions are not checkable:

- **Blocklist test.** `tests/test_audit_prompt.py` fails if the system prompt, any few-shot example or the non-food word list contains an entity name or a number from the answer key (the test reads the names from the key file). The prompt describes issue *types* only.
- **Frozen prompt for the holdout.** The prompt's SHA-256 is written to the run record. Holdout and control runs must use a hash that was committed before they ran; any later edit means a new holdout is needed.

## Evaluation (pre-registered in [comparison/audit_answer_key.md](comparison/audit_answer_key.md))

| Set | Node(s) | Use | Iteration allowed? |
| --- | --- | --- | --- |
| Development | Warschauer Straße | Write and tune the prompt and the candidate rules | Yes, every version logged |
| Holdout | Brandenburger Tor | Measure recall once, with the frozen prompt | No |
| Control | Schönhauser Allee | No known issues; measures false alarms | No |
| Generalisation | Kurfürstendamm, Rosenthaler Platz, Deutsche Oper (drawn with seed 20261007 from the 136 reference stations, before any audit code existed) | Estimates what the answer key cannot: issues nobody has found yet | No |

All runs on the corrections-off copy. Holdout, control and generalisation: 3 runs each, same frozen prompt.

**Metrics**

- **Candidate recall**: answer-key items that reach the worklist (code only).
- **Recall**: answer-key items flagged on the right record, verdict ≥ `suspicious`, `issue_type` correct. Reported per set, never pooled across dev and holdout.
- **Value accuracy** (secondary): proposed range overlaps the verified range.
- **Precision**: every flag labelled by the analyst as true issue / false alarm / unclear. Reported as false alarms per node.
- **Source quality**: share of proposed numbers whose quote, opened by hand, states that number (spot-check of up to 20).
- **Consistency**: share of answer-key items flagged in all 3 runs.
- **Cost and time** per node.

**Generalisation protocol.** For each of the three reference stations, before looking at the auditor's output, the analyst spends at most 20 minutes on a fixed manual scan (the five types above, same sources as the original checks) and writes down the findings. Then the auditor's flags are labelled. Issues found by the scan but not by the auditor are misses; issues found by the auditor and confirmed but missed by the scan are counted too. This is the only estimate of recall on issues nobody knew in advance.

**Decision rule** (thresholds proposed; the analyst confirms or edits them in the key file before Phase 3):

| Holdout recall | False alarms per node | Decision |
| --- | --- | --- |
| ≥ 4 of 6 primary items | ≤ 3 | Adopt: the audit runs before scoring by default; flags go to the review queue and the report |
| 2–3 of 6, or more false alarms | any | Keep as an optional assistant (`--audit`), report what it misses |
| < 2 of 6 | any | Drop; write up why |

## Phases

| Phase | Work | Output |
| --- | --- | --- |
| 0 | This plan + answer key + station draw, committed before code | Commit hash recorded in the key file |
| 1 | Corrections-off switch, candidate generator, tests (no LLM) | Candidate recall on all sets |
| 2 | Auditor, guard, review queue, mocked-client tests, blocklist test; tune on Warschauer Straße only | Logged prompt versions, dev recall |
| 3 | Freeze prompt; run holdout, control, generalisation × 3 | Run files under `comparison/audit_runs/` |
| 4 | The analyst labels flags and does the generalisation scans | `comparison/audit_eval.md`, `comparison/audit_findings.md` with the decision |
| 5 | Only if adopted: wire into the pipeline, report line, README | — |

## Amendment 1 (2026-10-07, before any audit code)

The plan above was written against `main` (run #15). The unmerged branch `conflation-v2-data-quality-fixes` (run #17, `scoring_v7`, `conflation_v2`) already fixes the three POI defects from the manual benchmark, with two general rules and a fourth correction table. Changes to this plan:

- **Order.** Merge `conflation-v2-data-quality-fixes` into `main` first; branch the audit implementation from that `main`.
- **Corrections-off switch also covers POI fixes:** `poi_conflation.cluster_records(..., quality_rules=False)` (near-duplicate merge and name/category check off) and skipping `load_verified_corrections()` (`poi_corrections_verified.csv`). `audit_apply.py` writes accepted POI proposals to that table.
- **H4–H6 are now in-sample twice:** the general rules were written from exactly these records. Their recall result says whether the LLM matches rules that were tuned on the answer; it is reported separately from H1–H3, and the decision rule's "≥ 4 / 6" is unchanged.

## Limitations that travel with any result

1. **The answer key is in-sample for the person.** The check types were designed by someone who already knew the issues. The dev/holdout split and the blocklist test limit how much of that leaks into the prompt; they do not remove it. The generalisation stations are the honest test.
2. **The holdout is small.** Six primary items on one node. A 4/6 vs 3/6 difference is one item; the write-up will say so.
3. **Recall on unknown issues has no ceiling measure** beyond the three-station scan, which is itself one person, 20 minutes, not blind to the issue types.
4. **The model's knowledge may already contain the answers** (Berghain's capacity, the Bundestag's offices are well known). Fine for the tool's purpose; it means the result says little about obscure entities. The generalisation stations include less famous places.
5. **Web search results change.** Run files store the URLs and quotes the model used, so a finding can be checked later even if the page moves.

## Cost estimate

Unknown until Phase 2 measures it. Order of magnitude: 5–20 candidates per node, a few searches each. The per-run USD cap in the guard is set after the first dev run, and the eval reports actual spend.

## Amendment 2 (2026-10-07, after Phase 1, before any prompt exists)

**What was seen.** During Phase 1 development on 2026-10-07, the holdout's candidate recall was computed and read (generator `audit_candidates_v2`): H1–H6 all reached the worklist, H2 with 9 of 22 buildings within 50 m, H4 through the non-food word "steakhouse", H5 through a same-name OSM building within 15 m. The `poi_category` row of §2 listed example words ("Steakhouse, Haus, Hof, Bank") that come from answer items H4/H5, and the v2 word list was built from them.

**Changes.**

- The examples are deleted from the `poi_category` row above.
- The non-food word list is rebuilt from an independent source only: the common German and English words for the OSM tag values `amenity=restaurant/bar/pub/biergarten`, `tourism=hotel/hostel` and `office=*` (with its values lawyer, notary, insurance, estate_agent, tax_advisor, architect, government, diplomatic, coworking, association). The derivation is written next to the list in `src/audit_candidates.py`. Neither the answer key nor [site_candidates.md](site_candidates.md) was consulted for it. Result (`audit_candidates_v3`, commit `80ae22b`): H4 no longer reaches the worklist as a `poi_category` candidate, only inside a `poi_duplicate` pair; H5 is unchanged.
- `tests/test_audit_prompt.py` now also blocks every word of ≥ 4 letters in the answer entities' names (whole-word match), across the system prompt, the few-shot examples and the non-food word list.

**Reporting.** H4 and H5 are marked **contaminated**: their candidate recall and their LLM recall are reported apart from the other items. The decision thresholds are unchanged ("≥ 4 / 6" on H1–H6), but the findings must state that H1–H3 are the holdout's main evidence (H4–H6 are in-sample for the conflation_v2 rules per Amendment 1, and H4/H5 are additionally contaminated here).

**Freeze.** From this commit the candidate rules are frozen at

```text
src/audit_candidates.py  SHA-256 1fdf42751930c09d4a744e14c5890d6b8f433c75041fecb5343548e30775eae9
```

Later changes to that file are allowed only to fix bugs. Each fix is logged here as a further amendment (what was wrong, the diff's effect on every worklist, the new SHA-256) before any LLM run uses the fixed version.


## Amendment 3 (2026-10-07, before the first full development run and before any Phase 3 run)

**What had run.** One trial on the development node only: 4 candidates (2 `concentration`, 2 `large_registration`), `claude-opus-5-5`, prompt `bd8a2050e135`, $0.489 at list prices (14 searches, 3 fetches; record `db/raw/audit/runs/warschauer_strasse_20261007T115724Z.json`). Roughly $0.12 per candidate, which put the full evaluation at $75–85. The analyst set a budget of $50 and decided the following changes.

**Changes.**

1. **Model:** `claude-sonnet-5-5` (list price $2 / $10 per MTok, cache reads $0.20) instead of the `agent_llm_policy.py` model constant (§3). Effort `medium`. The Opus trial is not used for any result; whether Sonnet is good enough is shown by the development runs and the holdout itself.
2. **No web search for the POI modules.** `poi_category` and `poi_duplicate` candidates (issue types `category_error`, `duplicate`) are judged from the names, tags, categories, providers and distances in the worklist only; web search and fetch are not offered in those conversations. Consequence: H4–H6 test whether the auditor recognises the issue from the data alone, not after a lookup. Their findings carry no proposed number.
3. **Development iterations:** at most **3 prompt versions**, each run **once** on the full development worklist. Each version is logged with its prompt SHA-256. The code refuses a fourth version and a second full run of the same version. Partial runs (`--limit`) remain possible for debugging and count towards the budget.
4. **Batching:** at most 3 candidates per conversation, at most 10 searches and 4 fetches per conversation (the trial had 2 candidates and 7 searches; 6 candidates sharing 8 searches would have cut the searches per candidate to about a third).
5. **Budget:** $50 in total for Phase 2 and Phase 3 at list prices, the Opus trial included. The code sums the cost of every run record and refuses a run whose cap does not fit into what is left.

**Unchanged:** the node sets, the answer key, 3 runs each for the holdout, the control and the generalisation stations, the metrics and the decision thresholds.

## Amendment 4 (2026-10-07, after v3 was frozen, before any holdout, control or generalisation run)

**What was found.** v3 (`85168ab831f7`) was frozen by the rule in [comparison/audit_prompt_log.md](comparison/audit_prompt_log.md). Reading the development transcripts afterwards showed a tool-interface defect, not a wording problem: with the dynamic-filtering search tool (`web_search_20260209`), the model sometimes wrapped the query in an extra `params` object and the server rejected the call (`invalid_tool_input`: 2 of 10 searches in v1, 4 of 14 in v3; the model did not retry, so D4 in v3 got no sourced figure), and the same tool lets the model filter results with its own code, which in v2 dropped the sentence with the headcount D2 needed. No `web_fetch` call was made in any development run; that is not a defect in itself when search results already carry a quotable sentence.

**Changes.**

1. The auditor uses the basic server tools `web_search_20250305` and `web_fetch_20250910` (no dynamic filtering): results reach the model unfiltered and there is one way to call each tool. The prompt text is unchanged; the tool definitions are part of the prompt hash, so the hash changes and the v3 freeze no longer applies to the code.
2. **One extra development version, v4**, only for this tool change (the cap of Amendment 3 becomes 4, for this reason alone). v4 is run once on the full development worklist, after a 1-candidate smoke run that checks the request format (partial runs do not count as versions).
3. **Decision rule, fixed before the v4 run:** v4 replaces v3 as the frozen version if its development recall on D1–D4 is 4 / 4 and it has fewer server-tool errors than v3 (4). Otherwise v3 stays frozen and the code goes back to the v3 tool definitions.
4. Every run record counts server-tool errors (error results of web search and fetch), and the command prints them, so this defect is visible without reading transcripts.

**Unchanged:** sets, answer key, runs per set, metrics, thresholds, budget ($50).
