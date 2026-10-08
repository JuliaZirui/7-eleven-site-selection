# Store Hypothesis

2026-09-23 · Input source for `analysis_runs.store_hypothesis`

## Disclaimer

This is a self-constructed hypothetical client case. **It does not represent an actual mandate or market-entry plan from 7-Eleven.** The first version only delivers a transit-node shortlist; it does not answer "should 7-Eleven enter Germany" or forecast profitability.

## Store Assumptions (case input, not 7-Eleven's actual operating model)

| Dimension | Assumption |
| --- | --- |
| Products & services | Fresh food (sandwiches, onigiri, prepared meals), tobacco/alcohol, coffee, daily emergency items (tissues, batteries, charging cables) |
| Target customers | Everyone within walking distance of a high-traffic transit node: residents, people working nearby and visitors. No segment is preset as the core customer; the mix comes from the data at each node |
| Planned operating hours | All-day trading, weekday and weekend. No time-of-day peak is assumed: no public source measures footfall at these nodes by time window, and the field checklist counts footfall in the morning, at midday and in the evening |
| Store format | 50–100㎡, embedded in or immediately adjacent to a high-traffic transit node, not a gas-station-attached convenience store |

## Why competition is scored over the whole day

The first versions of this project positioned the store around a morning (7–9) and an evening (16–19) commute peak, and until `scoring_v7` competition was scored separately in those two windows and in two weekend windows (10–19, 22–02). That treated the store's trading hours as known. Nothing in the public data shows when trade falls at these nodes: DB InfraGO publishes weekday passenger bands per station, not passengers by hour, and no source covers the U-Bahn or the walk-in flow from offices, homes and sights. The project then dropped the commuter positioning: the question became how strong the competition is at each node and how much business potential the public data show for an all-day convenience store.

Since `scoring_v8`, competition is scored over the whole day: competitors in the convenience basket (bakery, café, supermarket, späti, fast food, fuel-station shop) open on a weekday or on a weekend day, per 10,000 of that day type's people. The anchors are recalibrated on the same 139 reference stations. The four time windows are kept as unscored diagnostics: their competitor counts are listed in the report and drive differentiation hypotheses such as "weekday 16–19 thinly served".

## Benchmark Cases: Successful European Convenience Chains

| Brand / Market | Store size | Fresh-food category | Notes |
| --- | --- | --- | --- |
| Żabka (Poland) | Predominantly neighborhood-sized | Żabka Cafe hot food, coffee, private-label sandwiches (Tomcio Paluch), fresh juice | Europe's largest convenience chain, 10,000+ stores; evening retail model closest to Berlin's local Späti, also has a cashier-less Nano format [source](https://en.wikipedia.org/wiki/%C5%BBabka) |
| REWE To Go (Germany) | Up to ~300㎡, site-dependent | Modular product lines, self-checkout, premium coffee machines | **Direct German domestic benchmark** — serves the same station/pedestrian-street/airport scenarios; worth deep-diving | [source](https://convenience.org/Topics/Global/NACS-European-Convenience-Briefing/The-Pacemaker-Is-Gearing-Up) |
| 7-Eleven (Nordics, Reitan Group franchise) | New "7-Eleven Market" concept, between a traditional convenience store and a small supermarket | Fresh grocery section + hot-food counter + self-checkout | Same brand has operated in Norway/Sweden/Denmark for years; German trade media has reported Reitan Group considering bringing 7-Eleven into the German market — the most direct precedent, worth deep-diving as an evidence gap [source 1](https://www.nacsmagazine.com/Issues/January-2022/new-norway) [source 2](https://www.lebensmittelzeitung.net/handel/nachrichten/Seven-Eleven-Kommt-nach-Deutschland-40435) |

Of the three, **REWE To Go** and **7-Eleven Nordics (Reitan)** are the two threads most worth deep-diving during the field research phase: the former is a domestically validated German model, and the latter is the same brand's existing European operating experience, with some indication that Reitan has evaluated the German market — so far only confirmed via trade-media reporting, with no confirmed follow-through, so it goes on the evidence-gap list.
