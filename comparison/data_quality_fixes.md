# Data-Quality Fixes from the Manual Benchmark

2026-10-03 · `conflation_v2`, `scoring_v7`, [analysis run #17](../reports/site_report_run17_20261003T153943Z.md). Follows up the defects found in [manual_vs_tool.md](manual_vs_tool.md).

## What was found and how it is handled

The manual pass at Brandenburger Tor found three records in the tool's competitor set that should not be there. Each is now handled in the conflation step ([`src/poi_conflation.py`](../src/poi_conflation.py)). Two are handled by general rules, so they apply to every node and every reference station, not just the one that was checked by hand.

| Defect | Record(s) | Fix | Type |
| --- | --- | --- | --- |
| Duplicate not merged | Ha's Café (OSM) + Bäckerei Bich Ha (Overture), 8 m apart | **Near-duplicate merge:** records from different sources within 15 m, same category, sharing a distinctive name word, are one shop. Food-type words ("burger", "kebap", "spätshop" …) don't count as distinctive. | General rule |
| Category error | MAREDO Steakhouse, tagged café by Overture | **Name/category check:** a non-OSM record classified café or bakery whose name says restaurant or lodging (steakhouse, trattoria, osteria, ristorante, brasserie, hotel, hostel) is dropped. OSM categories are trusted. | General rule |
| Category error | Zollernhof, an office building tagged café by Overture | **Verified correction:** no rule can tell a building name from a café name without more data, so it is listed in [`poi_corrections_verified.csv`](../db/raw/calibration/poi_corrections_verified.csv) with its evidence. It is dropped wherever it appears. | Manual, sourced |

Every removed or merged record is written into the conflated snapshot's stats (`excluded`, `near_duplicates_merged`), so each correction can be audited and undone.

Not changed: **Dunkin'** (OSM fast food, Overture café). The tool already flags it as a category conflict and uses OSM's category; the benchmark found no reason to override that.

## Where the rules fire

| Scope | Near-duplicates merged | Name/category exclusions | Verified corrections |
| --- | --- | --- | --- |
| Brandenburger Tor | 1 (Ha's Café / Bich Ha) | 1 (MAREDO) | 1 (Zollernhof) |
| Warschauer Straße | 0 | 0 | 0 |
| Schönhauser Allee | 0 | 0 | 0 |
| 139 reference stations | 28 pairs | 6 (MAREDO, 2 osterias, 1 trattoria, 1 hotel, 1 hostel) | Zollernhof at 4 stations |

The reference stations were re-run with the same station set, the same OSM cache and the same Overture release (2026-09-23.1). Before any rule changed, the old code reproduced every station's counts exactly, so every difference below comes from the rules alone. 37 of 139 stations changed. The full list of merged pairs is in [`competition_anchors_20261003T153748Z.json`](../db/raw/calibration/competition_anchors_20261003T153748Z.json) (`quality_corrections` per station).

## Impact on the result

Three stages, so the effect of the corrected counts can be told apart from the effect of the recalibrated anchors:

| | Run #16 (before) | Counts fixed, old anchors | Run #17 (counts + anchors fixed) |
| --- | --- | --- | --- |
| BT competitors in 400 m | 32 | 29 | 29 |
| BT morning-peak competitors (7–9) | 16 | 13 | 13 |
| BT weekend-day competitors (10–19) | 27.5 | 24.5 | 24.5 |
| BT evening / weekend-night | 12 / 10.5 | 12 / 10.5 | 12 / 10.5 |
| Business fit: BT / WS / SA | 92.7 / 63.6 / 23.7 | 92.9 / 63.6 / 23.7 | **92.9 / 63.2 / 23.7** |
| Ranked #1 in 10,000 random weightings | BT 100% | BT 100% | BT 100% |
| Evidence quality, BT | 75.4 (verify first) | 73.9 (verify first) | 73.9 (verify first) |
| Competition-structure check, absolute counts | 86.0 / 54.1 / 23.7 | — | 87.3 / 54.0 / 23.7 |

Anchor changes (P90 → P10 of the 139 stations): morning peak per 10,000 people 34.2 / 6.4 → 33.4 / 5.9; evening 35.5 → 35.4; weekend day 91.5 → 90.5; weekend night unchanged. Absolute counts: morning-peak P90 54 → 53.2, weekend-day P90 96.2 → 96.0; the rest unchanged.

**Reading:**

- **The ranking does not change.** The three corrections take Brandenburger Tor's morning-peak count from 16 to 13, exactly as the benchmark predicted. Its competition criteria were already at or near the top of the scale, so the fit moves by +0.2.
- **The recalibration barely moves the anchors.** The rules remove 1–3 competitors at 37 of the 139 stations (102 are unchanged), so the P10/P90 anchors shift by at most 0.8. Warschauer Straße's counts are unchanged; it drops 0.4 only because its ratios are now scored against slightly tighter anchors (e.g. morning-peak P10 6.4 → 5.9).
- **Evidence quality at Brandenburger Tor drops 75.4 → 73.9.** Removing three false records makes the existing under-mapping concern slightly stronger: 29 mapped competitors against 66 IHK Gastronomie registrations (43.9% of the best node's ratio, was 48.5%). Corroboration also falls slightly, from 17/32 to 15/29 competitors seen by two or more providers. All three removed records had two providers each, and the merge makes Ha's Café corroborated (OSM + Meta). Verifying the competitor count stays first on its field checklist.

## Limits

- **Only Brandenburger Tor was checked by hand.** The rules apply everywhere, but the 28 pairs merged at reference stations were not each confirmed on site. Most are plainly the same shop ("Pergamon Döner & Pizza" / "Pergamon Döner Kebap", "Melek Bäckerei" / "Melek Pastanesi"). One is uncertain: "Edeka Focke" / "EDEKA Schrader", 14 m apart, may be one store under a new operator or two neighbouring stores.
- **The verified correction is one-sided.** Zollernhof is dropped wherever it appears, but other nodes' Overture records were not reviewed the same way, so errors a rule can't catch may remain there. The same asymmetry applies to the hand-estimated public-sector workers at Brandenburger Tor.
- **The manual pass also found 4 places that no open source lists.** They stay on the field checklist; adding them would favour the one node that was checked by hand.

## Reproduce (offline)

```bash
cd src
python3 poi_conflation.py
python3 calibrate_anchors.py --stations-from ../db/raw/calibration/competition_anchors_20261002T084101Z.json
python3 calibrate_demand.py
python3 metrics_pipeline.py
python3 report.py
```

`poi_conflation.py` re-conflates the candidates from their stored source snapshots. `calibrate_anchors.py` uses the cached OSM responses and the cached Overture extract ([`overture_ring_2026-09-23.1.json`](../db/raw/calibration/overture_ring_2026-09-23.1.json)). `calibrate_demand.py` reads only its cache. `build_competitor_set(..., quality_rules=False)` reproduces `conflation_v1`.
