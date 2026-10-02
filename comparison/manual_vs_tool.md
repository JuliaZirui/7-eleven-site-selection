# Manual vs. Tool-Assisted Comparison — Brandenburger Tor

2026-10-01 · Day 7 · MVP item "A single comparison of manual vs. tool-assisted workflow — time spent, quality, cost" ([project_plan_v3.md](../project_plan_v3.md)). Manual input: [manual_protocol.md](manual_protocol.md). Tool input: POI snapshot 27 (`poi_conflated`, OSM + Overture, data as of 2026-09-23) and metrics from analysis run #5.

## Scope and limits

- **Reduced scope.** Only the competitor inventory (step 1) and nearest competitor (step 4) were done by hand. Opening hours were also recorded during step 1. Population, office proxy and the conclusion (steps 5–7) were not; the speed gap there is not in question.
- **Not blind.** The analyst built the tool and had seen its output before the pass.
- **One node, one analyst, one pass.** This is a single data point, not a measurement of either method's error rate.
- **Ground truth is unknown.** Neither list is treated as correct. Where they disagree, the item goes to the field-research checklist.

## Time and cost

| | Manual | Tool |
| --- | --- | --- |
| Setup + inventory + nearest competitor | 32 min net (2 + 28 + 2; step 1 ran 3 min over its 25-min cap) | ~1 s compute (metrics 0.98 s, scoring + report 0.04 s, from the Day 6 log) |
| Data fetch | included above | ~5 min wall clock for all sources and all three nodes (snapshots 9–29, 2026-09-28 14:33–14:38) |
| Marginal cost per run | analyst time | EUR 0.00 (no paid APIs, no LLM tokens in the fixed pipeline) |
| Build cost | none | **_H_ hours over Days 1–6 (to fill in)** plus subscription/API spend _C_ |

Break-even, counting time only: the tool pays back after **H × 60 / 32 ≈ 1.9 × H** node-inventories, or **H × 60 / 90 ≈ 0.7 × H** full node passes using the protocol's expected 60–120 min. Example: a 40-hour build breaks even after ~75 competitor inventories or ~27 full passes. This ignores the tool's other value (reproducibility, the sensitivity analysis, 10,000-weighting robustness), which a manual pass does not produce at all.

## Quality: competitor inventory

| | Manual | Tool |
| --- | --- | --- |
| Competitors within 400m | 16 | 32 (31 unique — see duplicate below) |
| Morning-peak set (bakery, café; tool's open-hours rule) | 8 | 16 |
| Evening-peak set (späti, fast food) | 6 | 12 |
| Direct-format (späti / kiosk) | 1 | 3 |
| Nearest competitor | Panini Bar, 20 m | Panini Bar, 22 m |
| Weekday hours known | 15 / 16 (94%) | 10 / 32 (31%) |

### Match result

**Found by both: 12** — Panini Bar, Kadakals Döner Kebap, Curry Wolf, Adlon To Go Coffee Shop, Dunkin', Bäcker Wiedemann, Starbucks (in-station), ama Café, Gate Kebap, Midtown Coffeeshop, Bäckerei Bich Ha, plus Ecke 84 Spätkauf as a *probable* match to an unnamed OSM kiosk 315 m east (`way/881527237`) — not confirmed.

**Manual only: 4** — Bär Berlin Gyros/Snack (fast food, 60 m), Café Liebermann (café, 70 m), a second Starbucks (300 m), Káffee Büro (café, 300 m). Not in OSM or Overture's ≥ 0.6-confidence set.

**Tool only: 19**, in three groups:

| Group | Items | Reading |
| --- | --- | --- |
| Plausible competitors the manual pass missed | Café Lebensart (56 m, 3 providers, in-station), Linden Grill and Pavilion (in-station kiosks, 79–86 m), Cafe Einstein (306 m, 3 providers), Lindner, Abia, Monument, Burgerkette, K-bap, Curry and Burger, Wurst, Cafe Bar Italia, Segafredo, Espresso Eleganza, Sagar, Hoflieferantenberlin | Several are well corroborated (3 providers) or very close, so the manual pass under-counts too. The single-provider OSM entries may also be stale. |
| Miscategorised by the tool | MAREDO Steakhouse (tagged café), Zollernhof (an office building, tagged café) | Overture category errors; both inflate the morning-peak count. |
| Outside both peak sets | 7tappi Wein & Feinkost (beverages) | Counted in the total only; no effect on scoring. |

### Defects the comparison found in the tool

1. **Duplicate not merged.** Ha's Café (OSM, 335 m) and Bäckerei Bich Ha (Overture, 327 m) are the same bakery, ~8 m apart, but conflation keeps both because the names differ. The morning-peak count is one too high.
2. **Category errors from Overture.** A steakhouse and an office building count as cafés. The morning-peak count is two too high.
3. **Category conflict on Dunkin'.** OSM says fast food, Overture says café, and the analyst says bakery. The tool already flags it (`category_conflict: true`) and takes OSM's category, so Dunkin' counts in the evening set, not the morning one.
4. **Hours coverage.** Google Maps gave hours for 15 of 16 places; the tool has them for 10 of 32. Where both have hours they mostly agree (Kadakals, Curry Wolf, ama, Bich Ha identical). Wiedemann, Starbucks and Dunkin' differ by 30–60 min, but none of those differences changes whether the shop counts as open in a peak window.

Correcting defects 1–2 takes the tool's morning-peak count from 16 to 13. The per-unit-demand competition score would rise slightly, which widens Brandenburger Tor's one-point lead over Warschauer Straße. That lead is still inside the sensitivity range, so the ranking call does not change.

### Distances

The nearest competitor agrees within 2 m. Other in-station places were measured 30–45 m shorter by hand (e.g. Kadakals 33 vs 78 m, Starbucks 74 vs 120 m). This fits measuring to a station entrance rather than the coordinate anchor. One larger gap, Midtown Coffeeshop at 240 vs 369 m, is unexplained; Overture's point may sit at the building's centroid. No competitor changes sides of the 400 m line, except that Bich Ha sits right on it (400 m by hand, 327 m by the tool).

## Conclusion

- **Speed is not close.** For one node, the manual pass took 32 minutes to produce a competitor list and hours. The tool's fixed pipeline takes about a second once the data is fetched, and it repeats the job identically across nodes and runs.
- **Neither inventory is complete.** The tool listed about twice as many places, including several well-corroborated ones the manual pass missed. The manual pass found 4 places no open source had, and it got much better hours coverage from Google Maps.
- **The useful output is the disagreement list.** It sharpens the field checklist: confirm the four Google-only places, the in-station kiosks (Linden Grill, Pavilion), and the unnamed kiosk / Ecke 84 match. The tool's "verify first" evidence label for this node holds up.
- **Tool fixes found:** name-tolerant duplicate merging for POIs from different providers at the same spot; a category blocklist or confidence filter for Overture "café" tags on restaurants and buildings. Both are logged as v2 candidates, not fixed in v1.
