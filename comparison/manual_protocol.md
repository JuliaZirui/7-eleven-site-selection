# Manual Baseline Protocol — Brandenburger Tor

2026-09-28 · Day 7 · Input for the manual vs. tool-assisted comparison (project_plan_v3.md, MVP item "A single comparison of manual vs. tool-assisted workflow — time spent, quality, cost")

## Reduced scope (decided 2026-10-01)

Only **steps 0, 1 and 4** are run by hand (setup, competitor inventory, nearest competitor — step 4 is read off the step 1 distances, no extra search). Total cap: 35 min. Steps 2, 3 and 5–8 are skipped: the speed gap is not in question, and the competitor inventory is the step that tests quality (possible OSM under-mapping at this node). The write-up states this as a reduced, non-blind comparison. Still fill in "After the pass".

## Why this node

Brandenburger Tor is the node the tool trusts least (evidence quality 75.5, "verify first"): OSM lists very few competitors relative to IHK registrations, so the tool flags possible under-mapping. A manual pass is the most informative test here — it checks where the tool is fast *and* where a human might catch something the tool cannot.

## Rules

1. **Do not open** `reports/`, `db/`, `site_candidates.md` or the code during the pass. Work from this file and the browser only.
2. **Allowed tools**: Google Maps (incl. "Measure distance"), any public website, a spreadsheet or calculator, notes. **Not allowed**: Python, the project database, Claude or any other LLM.
3. **Timer**: record start and end clock time for every step. Pauses (phone, coffee) are not counted — note them.
4. **Timebox**: each step has a cap. When you hit it, stop, write down what you have, and mark `status = capped`. If you decide a step is not feasible by hand, mark `status = gave_up` and write why. Both outcomes are results, not failures.
5. **Same questions as the tool**: anchor 52.51654, 13.38152 · radius 400m · weekday windows 7–9 and 16–19 · store hypothesis as in `store_hypothesis.md` (you can reread that one file).
6. Write answers as you go — do not reconstruct from memory at the end.

## Known bias (to state in the write-up)

You designed the tool and have seen its output, so this is not a blind analyst. Record anything you "already knew" in the notes column. The comparison will state this limitation explicitly.

## Steps

| # | Step | What to produce | Cap |
| --- | --- | --- | --- |
| 0 | Setup | Maps open at the anchor, 400m circle or reference distance marked | 5 min |
| 1 | Competitor inventory | Every fresh / ready-to-eat competitor within 400m: name, type (Späti, supermarket, bakery, café, fast food, fuel shop, other), approximate distance from anchor | 25 min |
| 2 | Opening hours | Weekday hours for each competitor in step 1, with source (Maps / own website / unknown) | 20 min |
| 3 | Time-window gap | Share of hours-known competitors closed 7–9 and closed 16–19 on a weekday | 5 min |
| 4 | Nearest competitor | Distance in metres from the anchor to the closest competitor | 5 min |
| 5 | Catchment residents | Estimated residents within 400m, with source and method | 20 min |
| 6 | Office-worker proxy | Any usable proxy for office commuters within 400m (e.g. count of larger employers), with source | 20 min |
| 7 | Conclusion | 5–10 lines: priority call for this node, 2–3 differentiation hypotheses, evidence gaps | 15 min |
| 8 | Sources | List every source with URL and access date | 5 min |

Total cap: 2 h. Expected: 60–120 min.

## Recording sheet

### Timing

| # | Start | End | Minutes (net) | Status (done / capped / gave_up) | Notes |
| --- | --- | --- | --- | --- | --- |
| 0 | 14:50 | 14:52 | 2 | done | |
| 1 | 14:52 | 15:20 | 28 | done | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | 15:20 | 15:22 | 2 | done | |
| 5 | | | | | |
| 6 | | | | | |
| 7 | | | | | |
| 8 | | | | | |

### Step 1–2: competitors

| Name | Type | Distance (m) | Weekday hours | Hours source | Open 7–9? | Open 16–19? |
| --- | --- | --- | --- | --- | --- | --- |
| Ecke 84 Spätkauf & Souvenirs | späti| 350m | 09:00-00:00| google maps | no | yes |
| Kadakals Döner Kebap | fastfood | 33m | 09:00-18:00| google maps | no | yes |
| Panini Bar | fastfood | 20m | | google maps | | |
| Curry Wolf Brandenburger Tor | fastfood | 50m | 10:00-22:30| google maps | no | yes |
| Starbucks | cafe | 74m | 07:30-21:30| google maps | yes | yes |
| Bäcker Wiedemann | bakery | 65m | 07:00-19:00| google maps | yes | yes |
| Dunkin' Donuts | bakery | 60m | 08:00-22:00| google maps | yes | yes |
| Bär Berlin Gyros/Snack | fastfood | 60m | 10:30-23:00| google maps | no | yes |
| Gate Kebap | fastdoof | 350m | 00:00-24:00| google maps | yes | yes |
| Bäckerei & Eiscafe, Breakfast Coffee - Bich Ha | bakery | 400m | 07:00-16:00| google maps | yes | no |
| Starbucks | cafe | 300m | 08:00-20:00| google maps | yes | yes |
| ama Café | cafe | 279m | 08:00-18:00| google maps | yes | yes |
| Adlon To Go Coffee Shop | cafe | 50m | 09:00-16:30| google maps | no | yes |
| Café Liebermann | cafe | 70m | 11:00-18:30| google maps | no | yes |
| Midtown Coffeeshop | cafe | 240m | 08:30-22:00| google maps | yes | yes |
| Káffee Büro | cafe | 300m | 08:00-17:00| google maps | yes | yes |

### Step 3–6: figures

| Metric | Value | Source | Method | Confidence (high / medium / low) |
| --- | --- | --- | --- | --- |
| Competitors within 400m | | | | |
| Direct-format competitors (Späti, supermarket, fuel shop) | | | | |
| Share closed 7–9 (hours-known) | | | | |
| Share closed 16–19 (hours-known) | | | | |
| Nearest competitor (m) | | | | |
| Catchment residents | | | | |
| Office-worker proxy | | | | |

### Step 7: conclusion

(write here)

### Step 8: sources

(write here)

## After the pass

Also note, as rough estimates:

- Your total hours spent building the tool over Days 1–6.
- Any paid cost during the build (Claude subscription or API spend), if known.

These feed the break-even part of the comparison: the tool's per-run cost is near zero, but the build cost is not, and the write-up needs both.
