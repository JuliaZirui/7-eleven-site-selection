# Data-Plausibility Audit — Prompt Versions (development node only)

Every prompt version tried on the development node (Warschauer Straße), with its SHA-256 (`audit_llm.prompt_sha256()`: system prompt, new-entity instruction, few-shot examples, tool definitions), its one full run, and why the next version changed. At most 3 versions, one full run each (plan Amendment 3). Changes are based on development results only and describe issue types, never answer-key records (`tests/test_audit_prompt.py`).

| Version | Prompt SHA-256 | Model / effort | Full development run | Cost | Development recall (D1–D4) | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| trial | `bd8a2050e135` | Opus 5.5 / medium | — (4 candidates only) | $0.489 | — | Before Amendment 3. Not used for any result |
| v1 | `fdc651d566cd` | Sonnet 5.5 / medium | `db/raw/audit/runs/warschauer_strasse_20261007T121505Z.json` | $0.462 | 4 / 4 (D5 also flagged) | See below |
| v2 | `f75ac69f5933` | Sonnet 5.5 / medium | pending | | | |

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
