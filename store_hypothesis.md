# Store Hypothesis

2026-09-23 · Input source for `analysis_runs.store_hypothesis`

## Disclaimer

This is a self-constructed hypothetical client case. **It does not represent an actual mandate or market-entry plan from 7-Eleven.** The first version only delivers a transit-node shortlist; it does not answer "should 7-Eleven enter Germany" or forecast profitability.

## Store Assumptions (case input, not 7-Eleven's actual operating model)

| Dimension | Assumption |
| --- | --- |
| Products & services | Fresh food (sandwiches, onigiri, prepared meals), tobacco/alcohol, coffee, daily emergency items (tissues, batteries, charging cables) |
| Target customers | Office commuters, local young residents, and tourists — focused on a small number of high-traffic transit nodes |
| Planned operating hours | All-day trading, weekday and weekend. **Working hypothesis, to verify on site:** trade concentrates in a morning commute peak (~7–9am) and an evening commute peak (~4–7pm). No public source measures footfall at these nodes by time window, so the peaks are not scored; they are tested by counting footfall once at 7–9 and once at 16–19 on a weekday (field checklist) |
| Store format | 50–100㎡, embedded in or immediately adjacent to a high-traffic transit node, not a gas-station-attached convenience store |

## Why the dual peak is a hypothesis, not a scoring window

Until `scoring_v7`, competition was scored separately in each commute peak (7–9, 16–19) and in two weekend windows (10–19, 22–02). That treated the dual peak as known: a window's weight in the score assumed the store's trade really falls there. Nothing in the public data shows it does. DB InfraGO publishes weekday passenger bands per station, not passengers by hour, and no source covers the U-Bahn or the walk-in flow from offices, homes and sights.

Since `scoring_v8`, competition is scored over the whole day: competitors of the merged morning and evening basket (bakery, café, supermarket, späti, fast food, fuel-station shop) open on a weekday or on a weekend day, per 10,000 of that day type's people. The anchors are recalibrated on the same 139 reference stations. The peak and weekend windows are kept as unscored diagnostics: their competitor counts are listed in the report and drive the differentiation hypotheses, e.g. "16–19 thinly served". Two weekday footfall counts, at 7–9 and at 16–19, decide whether the dual peak holds at a node.

## Benchmark Cases: Successful European Convenience Chains

| Brand / Market | Store size | Fresh-food category | Notes |
| --- | --- | --- | --- |
| Żabka (Poland) | Predominantly neighborhood-sized | Żabka Cafe hot food, coffee, private-label sandwiches (Tomcio Paluch), fresh juice | Europe's largest convenience chain, 10,000+ stores; evening retail model closest to Berlin's local Späti, also has a cashier-less Nano format [source](https://en.wikipedia.org/wiki/%C5%BBabka) |
| REWE To Go (Germany) | Up to ~300㎡, site-dependent | Modular product lines, self-checkout, premium coffee machines | **Direct German domestic benchmark** — serves the same station/pedestrian-street/airport scenarios; worth deep-diving | [source](https://convenience.org/Topics/Global/NACS-European-Convenience-Briefing/The-Pacemaker-Is-Gearing-Up) |
| 7-Eleven (Nordics, Reitan Group franchise) | New "7-Eleven Market" concept, between a traditional convenience store and a small supermarket | Fresh grocery section + hot-food counter + self-checkout | Same brand has operated in Norway/Sweden/Denmark for years; German trade media has reported Reitan Group considering bringing 7-Eleven into the German market — the most direct precedent, worth deep-diving as an evidence gap [source 1](https://www.nacsmagazine.com/Issues/January-2022/new-norway) [source 2](https://www.lebensmittelzeitung.net/handel/nachrichten/Seven-Eleven-Kommt-nach-Deutschland-40435) |

Of the three, **REWE To Go** and **7-Eleven Nordics (Reitan)** are the two threads most worth deep-diving during the field research phase: the former is a domestically validated German model, and the latter is the same brand's existing European operating experience, with some indication that Reitan has evaluated the German market — so far only confirmed via trade-media reporting, with no confirmed follow-through, so it goes on the evidence-gap list.
