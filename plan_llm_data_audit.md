# Implementation Plan — LLM Data-Plausibility Audit (v3)

2026-10-07 · Status: not started. Pre-registration: [comparison/audit_answer_key.md](comparison/audit_answer_key.md) (committed together with this plan, before any audit code or prompt exists).

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
| `poi_category` | Competitors whose provider category conflicts with OSM tags or name keywords at the same location, or whose name contains words from a non-food list (Steakhouse, Haus, Hof, Bank …; the list lives in code and is reviewed) | All provider categories, OSM tags within 15 m |
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

## Limitations that travel with any result

1. **The answer key is in-sample for the person.** The check types were designed by someone who already knew the issues. The dev/holdout split and the blocklist test limit how much of that leaks into the prompt; they do not remove it. The generalisation stations are the honest test.
2. **The holdout is small.** Six primary items on one node. A 4/6 vs 3/6 difference is one item; the write-up will say so.
3. **Recall on unknown issues has no ceiling measure** beyond the three-station scan, which is itself one person, 20 minutes, not blind to the issue types.
4. **The model's knowledge may already contain the answers** (Berghain's capacity, the Bundestag's offices are well known). Fine for the tool's purpose; it means the result says little about obscure entities. The generalisation stations include less famous places.
5. **Web search results change.** Run files store the URLs and quotes the model used, so a finding can be checked later even if the page moves.

## Cost estimate

Unknown until Phase 2 measures it. Order of magnitude: 5–20 candidates per node, a few searches each. The per-run USD cap in the guard is set after the first dev run, and the eval reports actual spend.
