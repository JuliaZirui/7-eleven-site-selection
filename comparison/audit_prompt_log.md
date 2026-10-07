# Data-Plausibility Audit — Prompt Versions (development node only)

Every prompt version tried on the development node (Warschauer Straße), with its SHA-256 (`audit_llm.prompt_sha256()`: system prompt, new-entity instruction, few-shot examples, tool definitions), its one full run, and why the next version changed. At most 3 versions, one full run each (plan Amendment 3). Changes are based on development results only and describe issue types, never answer-key records (`tests/test_audit_prompt.py`).

| Version | Prompt SHA-256 | Model / effort | Full development run | Cost | Development recall (D1–D4) | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| trial | `bd8a2050e135` | Opus 5.5 / medium | — (4 candidates only) | $0.489 | — | Before Amendment 3. Not used for any result |
| v1 | `fdc651d566cd` | Sonnet 5.5 / medium | `db/raw/audit/runs/warschauer_strasse_20261007T121505Z.json` | $0.462 | 4 / 4 (D5 also flagged) | 26 of 40 candidates flagged |
| v2 | `f75ac69f5933` | Sonnet 5.5 / medium | `db/raw/audit/runs/warschauer_strasse_20261007T122511Z.json` | $0.558 | 3 / 4: D2 missed (D5 flagged) | 13 of 40 flagged |
| v3 | `85168ab831f7` | Sonnet 5.5 / medium | `db/raw/audit/runs/warschauer_strasse_20261007T123153Z.json` | $0.731 | 4 / 4 (D5 flagged) | 13 of 40 flagged. **Frozen** |

## v1 → v2

What the v1 run showed (40 candidates, 32 API calls, 8 searches, 0 fetches):

1. **"Suspicious" used for "cannot confirm".** 12 of 22 `poi_duplicate` pairs were suspicious with reasons like "different names, unclear without a visit": mostly neighbouring stalls in a food court. Two `missing_entity` buildings were suspicious although the reason said the registered headcount was about right (an event arena, a disused building). The v1 definition invited it: "suspicious (likely off by more than 2x, or unclear without a site visit)".
2. **Almost no searching.** 8 searches for 17 web-module candidates; only 1 finding carried a sourced number. The audit's point is looked-up, cited evidence, and value accuracy and source quality are metrics.
3. **Gateway rule not explained.** One venue finding argued the venue should not count because it lies outside the 400 m circle; visitor records include venues up to about 2 km on purpose, weighted by the station's 1/k share.
4. **Type misuse.** Two registrations counting the same workers twice were typed `duplicate`, a type meant for competitor records.

Also seen: one genuine new issue, a competitor record named after a shopping centre and classified as a late-night kiosk (`poi_category`, implausible).

v2 changes, all in the system prompt:

- Verdicts: suspicious needs a specific reason, in the data or a source; not being able to confirm a record means plausible, with the check in `evidence_needed`.
- Search for each candidate before reporting it, where web search is available, unless the data alone settles it.
- `fame_vs_attendance`: venues up to about 2 km are counted on purpose through the 1/k share; judge the figure, not the inclusion.
- `duplicate`: closeness alone is not a reason to flag (food courts, malls, concourses); flag only when the data points to one business. The type is for competitor records only; double-counted workers are `company_wide_headcount`.

## v2 → v3

What the v2 run showed (40 candidates, 32 API calls, 14 searches, 0 fetches):

- Hedging gone: 2 of 22 `poi_duplicate` pairs suspicious (v1: 12); 13 of 40 candidates flagged in total (v1: 26). 4 findings carry a sourced number (v1: 1).
- **D2 missed.** The registrant was identified and searched for, but the search results, filtered by the model's own code, kept no headcount sentence; without evidence the model judged the counted figure "within 2x of a plausible on-site figure" and called it plausible. Under the v2 rule "nothing points either way → plausible" this is what the rule asks for, but for a size class the default is wrong: the class is the company's by construction (`demand_model.LARGE_REGISTRATION_MIN_EMPLOYEES`, the pipeline's own reason for checking these by hand).
- No page was opened with web fetch in either run, so a figure missing from the filtered search results was never looked for on the page itself.

v3 changes:

- Exception to the verdict rule for registrations with a size class of 500 or more: plausible only if a source or the building's floor area supports roughly that many people at this address; otherwise suspicious. Disclosure: the person writing this rule had read the answer key, in which every large registration is overstated; the rule's reason is the pipeline's own design, but it is not blind to that.
- If the search results do not give the figure, open the most relevant page with web fetch before deciding.

## Which version is frozen

Decided before the v3 run: the version with the highest development recall on D1–D4 is frozen; on a tie, the one with fewer flagged candidates (suspicious or implausible) on the development node, as a proxy for fewer false alarms. Its SHA-256 goes into `comparison/audit_prompt_freeze.json` in a commit of its own, before any holdout, control or generalisation run.

## v3 result and the frozen version

v3 run: 40 candidates, 32 API calls, 10 searches, **0 fetches** (the fetch instruction had no effect), 5 findings with a sourced number. D2 caught: the registrant's listing in a company directory gave a smaller size class, and with no source for the counted figure the size-class exception made it suspicious. D4 caught as suspicious but, unlike v2, without a proposed figure. Duplicate hedging stayed down (1 of 22 pairs suspicious).

By the rule above: v1 and v3 tie on recall (4 / 4), v3 has fewer flagged candidates (13 vs 26) → **v3 (`85168ab831f7`) is frozen**, with model `claude-sonnet-5-5` and effort `medium`. Development spend: $1.75 for the three versions, $2.24 in total with the Opus trial.
