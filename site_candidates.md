# Site Candidates: Transit Nodes

2026-09-23 · Candidate transit-node shortlist and business analysis scope. Store-model assumptions live separately in [store_hypothesis.md](store_hypothesis.md).

> **How to read this file.** After the scope sections it is a dated working log (Day 2 → v5.2). Later sections supersede earlier ones and earlier text is not rewritten. For example, Day 5's three-pillar scoring was replaced in v3, v1's competitor counts in v2, and VBB GTFS, rejected on Day 4, was adopted in v2 for station lookups. Current figures: the last section and the [latest report](reports/site_report_run17_20261003T153943Z.md). The follow-up agent's v2 is the last section.

## Core Business Question

Assume 7-Eleven plans to pilot a Berlin store emphasizing fresh food, beverages, and commuter convenience, anchored to a small number of high-traffic transit nodes. Among the three candidate transit nodes, which are worth prioritizing for on-the-ground site diligence? What do the public data support, and what still needs verification?

## Candidate Transit Nodes (final)

All three nodes share the same DB official station category (Preisklasse 4) (source: *Stationspreisliste 2026*, DB InfraGO AG, as of 08.09.2026), so their transit magnitude is comparable — the customer-profile mix is the only remaining variable. This is the result after verifying the data and replacing the originally shortlisted Zoologischer Garten (Preisklasse 2 — not comparable in magnitude to the other two, and its customer base is itself a three-way mix of office/commercial/tourist). See "Data Verification Log" below for the verification process.

| Node | Transit connections | Preisklasse | Customer profile | Rationale |
| --- | --- | --- | --- | --- |
| Brandenburger Tor | S1/S2/S25/S26 + U5 | 4 | Tourist (city-center landmark) | Berlin's most iconic landmark station, highest tourist density citywide; no regional/long-distance trains stop here, so its Preisklasse sits at the same tier as the other two — a clean "pure tourist" sample |
| Warschauer Straße | S3/S5/S7/S9 + U1 | 4 | Office + tourist mix (east) | Adjacent to the RAW-Gelände nightlife cluster, while also drawing on the Mediaspree office district (Universal Music, Zalando, and other companies cluster there) — the only "office + tourist" mixed-customer sample among the three |
| Schönhauser Allee | S8/S41/S42/S85 + U2 | 4 | Local young-resident daily consumption (Prenzlauer Berg) | Same commercial strip as Eberswalder Straße but a higher interchange tier; the only clean "community daily consumption" control point among the three |

## Coordinate Anchors

| Node | Latitude, Longitude | Search radius |
| --- | --- | --- |
| Brandenburger Tor | 52.51653594770922, 13.381516508773798 | 400m |
| Warschauer Straße | 52.50533737805442, 13.449070864605977 | 400m |
| Schönhauser Allee | 52.549317383699474, 13.413628569194154 | 400m |

Radius rationale: no 7-Eleven-specific (Nordic or Japan HQ) catchment-radius figure is publicly disclosed — see "Evidence Gap: 7-Eleven Catchment Radius" below. 400m is adopted as the convenience-retail industry's general convention for an urban, walking-oriented primary trade area (≈0.25 mile), applied uniformly across all three nodes to preserve cross-area comparability.

## Business Analysis Scope

- **Demand**: convenience-consumption signals from residents, commuters, and tourists
- **Competition**: Späti, supermarkets, gas-station convenience stores, and other formats offering fresh/ready-to-eat food
- **Differentiation**: compare products/services, operating hours, and spatial distribution — not just a headcount of same-category stores
- **Time**: dual-peak model — morning and evening commute peaks as the core evaluation windows, midday off-peak as a baseline, avoiding the preset assumption that evening is always optimal
- **Output**: area prioritization, differentiation hypotheses, evidence gaps, field-research checklist

Low competition does not automatically mean market opportunity; a high store count can also signal both demand clustering and competitive pressure. Unverified items — rent, actual foot traffic, available storefronts, operating restrictions, and supply chain — must be explicitly listed in the report.

## To-Do (must land within Day 1)

Coordinate-anchor conversion is done and the search radius is set (400m, uniform across all three nodes) — see "Coordinate Anchors" above and "Evidence Gap: 7-Eleven Catchment Radius" below.

`LocationInput.time_window` rework is done — implemented in [`src/models.py`](src/models.py) as `TimeWindow`/`TimeWindowLabel` plus a `DEFAULT_TIME_WINDOWS` tuple (morning peak 7–9, evening peak 16–19, midday off-peak 10–15), replacing the old single time-window tuple. `CANDIDATE_NODES` in the same file encodes the three nodes' coordinates and the 400m radius, ready for Day 2's OSM integration.

## Evidence Gap: 7-Eleven Catchment Radius

**Question**: does 7-Eleven (Nordics/Reitan or Japan HQ) publish a standard trade-area / catchment radius for site selection?

**Search conducted**: English and Norwegian-language search across 6 queries — Reitan/7-Eleven Nordic site-selection criteria, walking-distance/commuter radius, Reitan investor materials, 7-Eleven Japan's "dominant area strategy" and store spacing, and one article fetch for a claimed 400m figure.

**Findings**:

- **Reitan (Nordics)**: public statements are qualitative, not numeric — the company states it prioritizes locations with sufficient "customer base" (*kundegrunnlag*) such as airports, hospitals, and transit hubs, and is closing ~70 underperforming kiosks starting 2026 due to a sustained decline in customer base and profitability. No disclosed radius or distance threshold.
- **7-Eleven Japan HQ**: known for the "dominant area strategy" — saturating a region with 50+ stores before expanding to the next region — but this is a *regional store-density* strategy, not a *per-store catchment radius*. One article mentioned "three 7-Elevens within 400m" in Tokyo, but this is an anecdotal observation, not an official design metric, and is not usable as a sourced figure.
- **Closest available benchmark**: general convenience-retail industry convention (from retail-analytics literature, not company-specific) — urban, walking-oriented convenience stores commonly use ≈0.25 mile (~400m) as the innermost/primary trade-area ring, extending to 0.5–1 mile for the secondary ring.

**Conclusion**: no 7-Eleven-specific catchment radius exists in the public domain — this remains an unresolved evidence gap, to revisit if primary-source access (e.g., a Reitan site-selection brief) becomes available during field research. The project adopts **400m** as its working radius, sourced to the general industry convention above, not to a 7-Eleven-specific standard. This should be stated explicitly wherever the radius is cited in the report, to avoid it being read as "benchmarked to 7-Eleven's own methodology."

~~3. Verify foot-traffic data sources~~ — resolved via DB's official Preisklasse grading (see "Data Verification Log" below); no longer needs separate verification of raw passenger-count figures.

## Data Verification Log

**Background**: the foot-traffic figures for the original three candidate nodes (Zoologischer Garten / Warschauer Straße / Schönhauser Allee) came from Wikipedia citations with inconsistent methodology and vintage (Zoo's figure from 2006–2008, Warschauer Straße from 2012, Schönhauser Allee from a 1996 book), and Zoo's figure was "total ridership" while Warschauer Straße's was "transfer passengers" — not directly comparable. At the time, the claim that the three nodes were "comparable in transit magnitude" had no basis.

**Verification method**: switched to Preisklasse (station category, 1–7, a nationally standardized DB InfraGO metric with consistent methodology) from the official *Stationspreisliste 2026* (as of 08.09.2026) as a standardized proxy for transit magnitude.

**Result**:

| Station | Preisklasse |
| --- | --- |
| Zoologischer Garten | 2 |
| Warschauer Straße | 4 |
| Schönhauser Allee | 4 |
| Brandenburger Tor | 4 |
| Berlin-Adlershof (alternative, not adopted) | 4 |
| Berlin Jungfernheide (alternative, not adopted) | 4 |

Zoo is two tiers higher than the other two, so its magnitude isn't comparable — it was replaced with Brandenburger Tor (also Preisklasse 4). All three final candidate nodes now share the same transit magnitude, differing only in customer-profile mix.

**Source**: [Stationspreisliste 2026](https://www.dbinfrago.com/resource/blob/13518698/1cd204bc2c7a98b2490822ee6fc200ad/Stationspreisliste-2026-data.pdf), DB InfraGO AG, Geschäftsbereich Personenbahnhöfe, as of 08.09.2026

**A methodological caveat worth keeping**: Preisklasse measures "how important this station is to DB's long-distance/regional rail operations," not raw foot traffic or fame. For example, Brandenburger Tor is Berlin's most famous landmark with extremely high tourist density, but because no ICE/IC/regional trains stop there (only S-Bahn + U5), it's graded the same as an ordinary intra-city interchange station (Preisklasse 4). If the report cites Preisklasse to describe "transit-hub tier," this caveat needs to travel with it, or it will be misread as a foot-traffic comparison.

## Day 2 Data Sources & Evidence Gaps

Pipelines: [`src/poi_pipeline.py`](src/poi_pipeline.py) (OSM competitor POIs) and [`src/lor_pipeline.py`](src/lor_pipeline.py) (LOR population). Taxonomy in [`src/poi_taxonomy.py`](src/poi_taxonomy.py), HTTP clients in [`src/osm_client.py`](src/osm_client.py) / [`src/lor_client.py`](src/lor_client.py).

### OSM Overpass: endpoint substitution

`overpass-api.de` (the standard public endpoint, including its `lz4.`/`z.` mirrors) rejects this project's environment with a bare HTTP 406 at the Apache layer — confirmed via a real TLS handshake against the genuine `overpass-api.de` certificate, so this is a server-side IP block (a known behavior toward cloud/datacenter egress ranges), not a local network issue. `overpass.osm.ch` is reachable but only serves a Swiss extract (0 results for all three Berlin nodes; verified against a Zurich control query that returned 83 results). The pipeline uses `overpass.atownsend.org.uk`, a full-planet, live-updated public instance that works from this environment, with the official endpoint kept as a documented fallback for environments without the IP-block issue. If this project moves to a different execution environment, re-verify which endpoint is reachable before trusting the fallback order.

> **Superseded (2026-09-28):** `overpass.atownsend.org.uk` is *not* a complete planet instance; it held 0–1 buildings per 400m circle and caused v1's under-counted competition. See "v2: Multi-Source Competitors and Competition Recalibration" below.
>
> **Corrected (2026-10-01):** the `overpass-api.de` 406 was not an IP block. The main instance rejects httpx's default `User-Agent`; the same query returns 406 with `python-httpx/…` and 200 with an identifying agent. Every Overpass call now sends `osm_client.HTTP_HEADERS`, and `overpass-api.de` is first in the endpoint list again, with `maps.mail.ru` as fallback. A live re-query of Brandenburger Tor on the main instance returned exactly the same 26 elements as the 2026-09-28 mail.ru snapshot.

### LOR population: source substitution

The Amt für Statistik Berlin-Brandenburg's static open-data CSVs (e.g. `EWR_L21_202512E_Matrix.csv`, the population-by-LOR-Planungsraum export linked from daten.berlin.de) now 404 into that site's new "MATS" data-portal SPA shell for every path under `/opendata/` — the site was migrated and the old static file links were not preserved as redirects. The Wayback Machine's snapshots of the same CSV URLs are already post-migration too (same broken shell), so there is no live or archived copy of the raw EWR register export at LOR granularity. The new MATS portal (`/datenportal` → Bevölkerung → Bevölkerungsstand) only exposes population by district/municipality (Kreis/Gemeinde) at the time of writing — too coarse to distinguish the three candidate nodes, which is the entire reason for using LOR in the first place.

**Substitute used**: the Berlin Senate's "Monitoring Soziale Stadtentwicklung" (MSS) 2025 WFS layer (`mss_2025:mss2025_indexind_542`, via `gdi.berlin.de`), whose context indicators include `ew` (Einwohnerzahl) and `zeit` (as-of period) per Planungsraum, data as of 31.12.2024. This is an official SenStadt publication and a reasonable v1 stand-in, but it's one step removed from the primary EWR register (a monitoring report's population base figure, not the register export itself) — logged here, and in `source_snapshots.notes` for the `berlin_mss_population` snapshot, rather than silently treated as equivalent. If the MATS portal later exposes LOR-level EWR figures directly, switch to that.

LOR boundaries: `lor_2021:a_lor_plr_2021` via the same `gdi.berlin.de` WFS (542 Planungsräume, Stand 01.01.2021) — this is the current boundary vintage and matches the MSS 2025 layer's Planungsraum geography, so `plr_id` codes join cleanly across both sources without a boundary-version mismatch.

### Day 2 results

| Node | LOR Planungsraum | Population (31.12.2024) | Competitor POIs (400m) |
| --- | --- | --- | --- |
| Brandenburger Tor | 01100206 · Unter den Linden | 1,876 | 2 (cafe, deli) |
| Warschauer Straße | 02400625 · Wriezener Bahnhof | 1,328 | 8 (mostly fast food + 1 späti-kiosk + 1 supermarket) |
| Schönhauser Allee | 03601141 · Arnimplatz | 7,057 | 12 (bakeries, cafes, 2 späti-kiosk, 1 supermarket, 1 fast food) |

The resident-population gap (Schönhauser Allee ~4-5x the other two) and the competitor mix both track the intended customer-profile split from the node table above — a low-population, low-competitor tourist node (Brandenburger Tor), a low-population but fast-food-heavy office/nightlife node (Warschauer Straße), and a high-population, bakery/café-heavy residential node (Schönhauser Allee). This is a directional read from raw counts, not yet the scored comparison — spatial/opening-hours computation is Day 3.

## Day 3: Fixed Computation Layer

[`src/metrics_pipeline.py`](src/metrics_pipeline.py) reads the observations Day 1-2 already wrote (no new API calls) and computes 27 `area_metrics` rows per area: competitor counts (total + per category), competitor density (per km², per 1,000 residents), nearest/mean competitor distance from the anchor, resident population, opening-hours data completeness, and opening-hours coverage for each of the three [`DEFAULT_TIME_WINDOWS`](src/models.py). `area_metrics` is append-only (each run is dated by `computed_at`), consistent with the schema's "snapshots are appended, never overwritten" rule applied to derived data, not just raw sources.

### Opening-hours parsing

[`src/opening_hours.py`](src/opening_hours.py) parses the subset of OSM's `opening_hours` syntax that actually appears in the Day 2 data (verified against every distinct raw value: day ranges/lists, `PH`, `off`, `24/7`, and overnight spans). A string outside that subset is treated as unparseable (hours "unknown"), never silently mis-read — same rule as the rest of the pipeline. Overnight spans (closing time ≤ opening time, e.g. `09:00-01:00`) are split across the two affected days rather than dropped; confirmed correct against Schönhauser Allee's Star Mini Market (`Mo-Th 09:00-01:00, Fr,Sa 09:00-04:00, Su 13:00-01:00`).

Two methodological choices, both fixed and explainable rather than fuzzy:

- **Representative days**: opening-hours coverage is evaluated on Wednesday (weekday) and Saturday (weekend), not averaged across all 7 days. Wednesday avoids Monday/Friday edge effects; Saturday — not Sunday — represents the weekend baseline because Germany's *Ladenschlussgesetz* keeps most non-food/non-kiosk shops closed on Sundays, which would understate weekend competition rather than describe a typical one.
- **`PH` (public holiday) tokens** are recognized in day selectors but dropped when building the weekly schedule — this project has no holiday calendar, so a `PH`-scoped rule can't be mapped to a specific weekday. Logged here as a bounded, known gap.

### Day 3 results

| Node | Competitors (400m) | Density /km² | Density /1,000 residents | Nearest competitor | Hours known/unknown |
| --- | --- | --- | --- | --- | --- |
| Brandenburger Tor | 2 | 4.0 | 1.07 | 56m | 2 / 0 |
| Warschauer Straße | 8 | 15.9 | 6.02 | 20m | 5 / 3 |
| Schönhauser Allee | 12 | 23.9 | 1.70 | 51m | 11 / 1 |

Competitors open during each window (weekday **We** / weekend **Sa**, out of the node's total):

| Node | Morning peak (7-9) | Evening peak (16-19) | Midday off-peak (10-15) |
| --- | --- | --- | --- |
| Brandenburger Tor | 1/2 · 0/2 | 2/2 · 1/2 | 2/2 · 1/2 |
| Warschauer Straße | 3/8 · 3/8 | 5/8 · 5/8 | 5/8 · 5/8 |
| Schönhauser Allee | 7/12 · 5/12 | 10/12 · 9/12 | 10/12 · 11/12 |

Warschauer Straße stands out on two dimensions at once: it has the shortest nearest-competitor distance (20m — KEBmeUP, a fast-food stand with no OSM opening hours; *corrected on Day 5: an earlier version of this sentence misattributed the 20m POI to the 24/7 REWE To Go kiosk, which is actually 142m from the anchor*) and the highest competitor density per resident (6.0/1,000 — more than 3x Schönhauser Allee's), despite having a third of Schönhauser Allee's raw competitor count. Schönhauser Allee's competitors are also the most consistently open across all three windows (58-92% coverage), reflecting its residential/daily-consumption profile, while Brandenburger Tor's two competitors both go quiet on weekend mornings — consistent with a tourist-flow pattern that doesn't peak until later in the day. These are directional reads to carry into Day 5's scoring, not yet a ranked recommendation.

## Day 4: Agent Follow-Up Layer

[`src/agent_followup.py`](src/agent_followup.py) adds the bounded, agent-driven layer described in project_plan_v3.md's "Division of Responsibilities": it reads what Day 1-3 already wrote (no new API calls at detection time), flags information gaps, and — only for gaps a re-fetch can plausibly fix — spends a rationed live-API call to try. Per the plan, the trigger is never a raw count ("fewer than 8 POIs"); it's one of three things:

- **Missing** — an `area_metrics` row already flagged `is_missing=1`, or a competitor POI with no recorded `opening_hours_raw`
- **Stale** — a source snapshot backing the area is older than 14 days
- **Conflicting** — the same OSM `external_ref` reported with a different category or opening hours across two snapshots (only possible once an area has been re-fetched)

Tool selection is a fixed rule table, not an open-ended decision, matching "the Agent demo shows tool selection... not chain-of-thought logs":

| Gap | Tool |
| --- | --- |
| Missing population-derived metric | `refetch_lor_population` (re-runs the Day 2 LOR/MSS ingestion for that one area) |
| Missing competitor/spatial metric | `refetch_osm_pois` (re-runs the Day 2 Overpass ingestion for that one area) |
| Missing POI opening hours, backing snapshot fresh | `flag_for_field_research` — a same-day OSM re-query would return identical data, so a live call is skipped in favor of a field checklist item |
| Missing POI opening hours, backing snapshot stale | `refetch_osm_pois` |
| Stale snapshot | `refetch_osm_pois`, `refetch_lor_population`, or `refetch_ihk_gewerbedaten`, matched to the stale source |
| Conflicting observation | `flag_for_field_research` — no automated tool resolves a discrepancy; explaining it is Agent work |

`flag_for_field_research` makes no network call — it writes a `development_events` row (`event_type='evidence_gap'`) with a proposed field-research question, which is both the MVP's "stop condition" (nothing left that automation can do) and a direct contribution to the report's field-research checklist.

**Call budget**: a `CallBudget` caps live-tool calls at 4 total / 2 per area per run (only the refetch tools are rationed; flagging is unlimited, since it costs no external call). A gap whose tool is already exhausted, or whose tool was already invoked once this run for the same area, is flagged instead of re-called or double-charged — verified with a synthetic scratch-DB scenario (forced missing metric, forced 30-day-stale snapshots, forced conflicting observation, `max_calls_total=1`): the first eligible gap consumed the one call, every subsequent live-tool-eligible gap fell back to `flag_for_field_research` with `stop_reason="call budget exhausted"`, and a second gap mapping to an already-used tool reused its result instead of re-calling. `_tool_refetch_osm` was also verified directly against the live Overpass endpoint (new snapshot written successfully) outside of this scratch run.

*Superseded in part (2026-10-02): the loop below is now one of three pluggable policies, and the default is an impact-ordered variant. See "Agent v2" at the end of this file.*

**Day 4 result on the real database**: with all Day 2 snapshots fetched the same day (nothing stale yet) and no missing `area_metrics` rows, the only real gaps were the 4 competitor POIs with no OSM `opening_hours` tag (3 at Warschauer Straße — KEBmeUP, mundis, Fatih Servet; 1 at Schönhauser Allee — Mate Mate Spätshop). All four were flagged straight to field research (fresh snapshot, so no live call attempted) and are now in `development_events`, ready to feed Day 5/7's field-research checklist. The tool log is also written to `db/raw/agent_log/day4_followup_<timestamp>.json` for traceability, mirroring the raw-file-reference pattern used by the Day 2 pipelines.

## Day 4 (extra): IHK Gewerbedaten Ingestion

project_plan_v3.md lists "IHK or VBB data ingestion" as Day 4 work to add if time allows. Both were checked against the actual live data before choosing:

- **VBB** publishes GTFS schedule data (routes/stops/trips), not ridership or foot-traffic counts. Ingesting it would mostly re-derive a transit-magnitude signal already settled in Day 1 via DB's Preisklasse grading (see "Data Verification Log" above), for a ~76MB feed covering the entire Berlin-Brandenburg network — a lot of ingestion weight for a metric this project already has a defensible proxy for.
- **IHK Berlin's "Gewerbedaten"** (`gdi.berlin.de/services/wfs/gewerbedaten`, license *Datenlizenz Deutschland – Zero – Version 2.0*, no attribution required) publishes ~367,500 anonymized, geocoded business-registration points citywide — branch (`branch_top_level_desc`/`ihk_branch_desc`, NACE-coded), employee-size class, business age, legal form, and LOR Planungsraum — refreshed monthly. This adds a genuinely new signal: registered-business density and composition within each node's own 400m radius, which can directly test the customer-profile hypotheses in this file's node table (e.g. "office + tourist mix" for Warschauer Straße) against real evidence instead of only the qualitative rationale used to build the shortlist. IHK was the clearer win and is what got built.

**Implementation**: [`src/ihk_client.py`](src/ihk_client.py) + [`src/ihk_pipeline.py`](src/ihk_pipeline.py), mirroring the Day 2 OSM pipeline's per-area shape. One wrinkle: this WFS's own `DWITHIN` spatial filter returned zero matches at every radius and coordinate-order combination tested, while a plain `BBOX` filter against the same layer worked immediately (most likely because the layer's geometry is stored in EPSG:25833 and this GeoServer instance doesn't reproject a degree/meter `DWITHIN` distance against it). The pipeline works around this the same way `poi_pipeline.py` already handles Overpass-vs-WFS asymmetry: fetch a padded bounding box (confirmed, via a `resultType=hits` check, that the citywide 367,575-feature dataset is never near any WFS result cap), then cut to the exact 400m circle with `geo.haversine_m` before writing to `observations` (`entity_type='business'`, `category`=`branch_top_level_desc`, full record in `payload_json`). `data_as_of_date` is set to the fetch date, same treatment as OSM: the WFS abstract states the register is refreshed monthly, but the daten.berlin.de portal's own "Aktualisiert" date is stale page metadata, not the live service.

`src/agent_followup.py` was updated for the new source: `refetch_ihk_gewerbedaten` joined `refetch_osm_pois`/`refetch_lor_population` as a third live follow-up tool, and the `STALE_SNAPSHOT` gap's tool-selection now looks up the stale `source_name` in an explicit `_SOURCE_TO_TOOL` table (falling back to `flag_for_field_research` for any source with no refresh tool) instead of the previous two-way `osm_overpass`-or-else branch, which would have misrouted an `ihk_gewerbedaten` staleness gap to the LOR tool. Re-verified against a synthetic scratch DB with all snapshots forced 30 days stale: all three sources now route to their correct tool, and `_tool_refetch_ihk` was confirmed directly against the live WFS.

**Result on the real database** — registered businesses within each node's 400m radius, top branches by count:

| Node | Businesses (400m) | Top branches |
| --- | --- | --- |
| Brandenburger Tor | 553 | Grundstücks-/Wohnungswesen (67), Gastronomie (66), Finanzdienstleistungen (62), Unternehmensberatung (54), Einzelhandel (40) |
| Warschauer Straße | 741 | Gastronomie (119), Einzelhandel (96), IT-Dienstleistungen (66), Finanzdienstleistungen (60), Unternehmensberatung (38) |
| Schönhauser Allee | 1,872 | Einzelhandel (327), Gastronomie (149), Finanzdienstleistungen (121), IT-Dienstleistungen (107), persönliche Dienstleistungen (106) |

This corroborates two of the three customer-profile hypotheses reasonably directly — Schönhauser Allee's registered-business mix is retail-dominated by a wide margin (consistent with "local daily consumption"), and Warschauer Straße's is gastronomy/IT/consulting-heavy (consistent with the "office + nightlife" mix, IT/consulting tracking the Mediaspree employers, gastronomy tracking RAW-Gelände). Brandenburger Tor is the interesting miscalibration: its top registered branches are real estate, gastronomy, finance, and consulting — not a "pure tourist" mix at all. This is a real, sourced finding worth carrying into Day 5's scoring and differentiation write-up, not just a null result: Pariser Platz's immediate surroundings are heavily embassies/banks/corporate offices, and *registered business address* is a different thing from *foot traffic composition* — a landmark can be tourist-dominated in footfall while still being office/finance-dominated in who's actually registered to an address there. That distinction should travel with the finding wherever it's cited, the same way the Preisklasse caveat does above.

## Day 5: Scoring Layer, Sensitivity, Report

[`src/scoring.py`](src/scoring.py) computes two scores per node and never adds them together: **business fit** (how well public data match the store hypothesis) and **evidence quality** (how far that fit number can be trusted). [`src/report.py`](src/report.py) renders [`templates/report_template.md`](templates/report_template.md), re-verifies every cited figure against `area_metrics` by `metric_id`, and writes an `analysis_runs` row (store hypothesis parsed from [store_hypothesis.md](store_hypothesis.md), area ids, snapshot ids, weights, `scoring_v1`, the Day 4 tool log, report path). First run: `analysis_runs #1` (24 Sep). Only the latest run's report is published: [`reports/site_report_run17_20261003T153943Z.md`](reports/site_report_run17_20261003T153943Z.md).

### New scoring inputs (added to the Day 3 metrics layer)

Two gaps surfaced while building the scoring, both fixed in [`src/metrics_pipeline.py`](src/metrics_pipeline.py) rather than patched inside the scoring code:

- **Resident population mismatched the competitor catchment.** Day 3's `resident_population` is the *anchor's* Planungsraum only, but that Planungsraum covers just 59% (Brandenburger Tor), 43% (Warschauer Straße) and 25% (Schönhauser Allee) of each 400m circle. New `catchment_population_estimate` intersects the circle with every Planungsraum it touches and apportions population by area (re-reading the Day 2 raw files, no API call; assumes uniform density within a Planungsraum). Result: 1,254 / 3,277 / 10,667 residents vs. the Day 3 figures of 1,876 / 1,328 / 7,057 — Warschauer Straße's catchment is ~2.5x what Day 3 implied, and the Day 3 `competitor_density_per_1000_residents` should be read with that in mind.
- **IHK data were in `observations` but not in `area_metrics`.** New `businesses_10plus_employees_count` is the office-commuter proxy (63 / 122 / 53). It counts size classes instead of a raw business count because 50–65% of registrations in each radius are "0 Beschäftigte" sole traders, which would make a raw count track residential density. It never sums employees, since a registered seat is not an on-site headcount.

`area_metrics` is append-only, so the re-run added a second full set of rows. Scoring reads the latest row per (metric, period).

### Scoring design

| Pillar (baseline weight) | Criteria (equal weight within pillar) | Anchored scale (0 → 100) |
| --- | --- | --- |
| Demand (40%) | Businesses with 10+ employees; catchment residents | 0 → 150; 0 → 12,000 |
| Competition (35%) | Direct-format competitors (späti/supermarket/fuel shop); nearest competitor; all fresh-food competitors per km² | 5 → 0; 0 → 150m; 30 → 0 |
| Time-window gap (25%) | Share of *hours-known* competitors closed 7–9 / 16–19 on a weekday | 0 → 1 |

- **Anchored scales, not min-max.** With three nodes, min-max always gives one node 0 and one 100 on every criterion. Min-max is still run once as a method check (it gives the same order).
- **Window gaps use hours-known competitors as the denominator.** Day 3's `competitors_open_share` divides by all competitors, which silently counts an unknown-hours POI as closed.
- **Missing inputs are dropped from their pillar and flagged, never scored 0.** Verified with a forced-missing test on a scratch DB. Side effect worth knowing: dropping a weak criterion can *raise* fit, which is why input completeness is part of evidence quality.
- **Tourist footfall is unscored.** No open node-level source exists, and it is listed as an unscored dimension in the report rather than left to read as zero.

**Evidence quality** is the equal-weight mean of freshness, hours completeness, hours sample adequacy (≥5 hours-known competitors), input completeness, and **OSM mapping consistency**: OSM competitors per IHK-registered Gastronomie business, relative to the best node. That last component exists because Brandenburger Tor's biggest advantage, an almost empty competitive field (2 OSM POIs), sits next to 66 registered Gastronomie businesses in the same 400m (3.0% vs. 8.1% at Schönhauser Allee). This is the project's rule that "low competition does not automatically mean market opportunity", applied as a cross-source check. The two sources are compared, never summed.

### Day 5 results

| Node | Business fit | Evidence quality | #1 in 10,000 random weightings | Priority call |
| --- | --- | --- | --- | --- |
| Brandenburger Tor | 42.9 | 75.5 (verify first) | 57% | Prioritize — verify evidence first |
| Schönhauser Allee | 41.6 | 98.3 (adequate) | 43% | Prioritize for field diligence |
| Warschauer Straße | 40.8 | 89.2 (adequate) | 0% | Lower priority (control node) |

**No robust single #1.** The fit spread across all three nodes is 2.1 points. Brandenburger Tor wins on competition (pillar 75 vs. 32), Schönhauser Allee on demand (62 vs. 26), and the order flips under demand ×1.5 or competition ×0.5, and under the `demand_led` scenario. That trade-off is the actual finding. The report presents both contenders with the weight that decides between them, not a single winner, and Brandenburger Tor's lead rests on the evidence component flagged above. The robust result is the other end: **Warschauer Straße ranks first in none of the 10,000 weightings.** It is middling on demand (strong workforce, thin residents), carries the closest competition (20m nearest, a 24/7 REWE To Go at 142m), and has the most unknown opening hours.

## Day 6: Fixed Test Cases

`python3 tests/run_day6.py` runs all four fixed test-case categories from project_plan_v3.md, plus a supplementary suite for the Day 5 layer, then an end-to-end reproducibility check. It writes a time/cost/quality log to `reports/day6_test_log_<stamp>.md` and `.json`; the latest published log is [`reports/day6_test_log_20261004T081832Z.md`](reports/day6_test_log_20261004T081832Z.md). The tests use stdlib `unittest` (no new dependency). Every test builds its own throwaway SQLite database from `db/schema.sql` and redirects every raw-file/report path into a temp dir. A network guard makes any HTTP call a test didn't explicitly mock raise an error, so the real `db/site_selection.db` is never written (checksum identical before and after) and no test can pass by quietly hitting a live API.

| Category | File | What it pins down |
| --- | --- | --- |
| Missing data | [`tests/test_missing_data.py`](tests/test_missing_data.py) | Zero competitors → count 0 but distances *missing*; missing/zero population never divides or defaults to 0; schema rejects NULL-as-present and 0-as-missing; unknown hours tracked separately; Agent routes missing gaps to the right tool; scoring drops a missing criterion, renormalizes, and lowers evidence quality |
| Duplicate records | [`tests/test_duplicate_records.py`](tests/test_duplicate_records.py) | Same shop mapped twice within 25m collapses; same name 67m apart or unnamed pairs don't; the DB dedup index backstops an exact duplicate; a refetched area is never double-counted; IHK businesses never count as competitors |
| API failure | [`tests/test_api_failure.py`](tests/test_api_failure.py) | Overpass retries are bounded (2 endpoints × 3 attempts) and fall through mirrors; a failed OSM/IHK fetch writes no snapshot and no rows, and the run continues for other areas; LOR/IHK clients reject error statuses and malformed payloads; a failed Agent tool falls back to a field-research flag and still costs its call; an exhausted budget makes no call; a report whose citations don't verify writes neither a file nor an `analysis_runs` row |
| Overnight-spanning hours | [`tests/test_overnight_hours.py`](tests/test_overnight_hours.py) | Tails land on the next day, including Sunday → Monday; the real Star Mini Market string; a night kiosk counts in the next morning's peak; a 00:00 close doesn't leak; wrapping day ranges; `;` overrides vs `,` adds; unsupported syntax → unknown |
| Day 5 (supplementary) | [`tests/test_day5_scoring.py`](tests/test_day5_scoring.py) | Catchment population apportioned correctly on a synthetic two-Planungsraum split (and *missing*, not lower, if a Planungsraum has no figure); scale clamping/direction; a dominant node is robust and a trade-off is not; Monte Carlo is seeded; every template placeholder is supplied |

### Result

68/68 tests pass in 0.52s. 28 mocked API calls were exercised, with 0 real API calls, 0 LLM tokens and EUR 0.00. The end-to-end check re-runs metrics → scoring → report on a *copy* of the real database with network blocked. It reproduces analysis run #1 exactly (every fit and evidence score, the ranking, the Monte Carlo shares; 33 citations re-verified). The offline pipeline takes ~1.2s in total, almost all of it the catchment-population geometry.

### Defects the tests found (and fixes)

The first run failed 17 of 67. Eight were test-harness mistakes. The other nine exposed three real defects, and a fourth surfaced once those were fixed:

1. **Refetches double-counted.** Snapshots are append-only, but every consumer read *all* of an area's snapshots. After a Day 4 Agent refetch, 3 shops + a re-fetch of 4 would be counted as 7. This affected competitor counts, population (which read an arbitrary row), IHK business mix, the report's POI list, the OSM/IHK consistency check, and the Agent's own gap detection. A refetch also would not have cleared a "stale" gap, because the superseded snapshot was still checked, which would have made the Agent refetch on every run. Fix: `metrics_pipeline.latest_snapshot_id()` / `latest_area_snapshots()`, used by every reader; only conflict detection still reads across snapshots, deliberately. It hadn't affected real results because no real refetch has happened yet. **Known remaining limit:** `source_snapshots` has no `area_id`, so a refetch that returns zero rows leaves no trace, and the older snapshot is still read as current. This is logged as a schema follow-up and not patched around.
2. **The parser accepted partially-read hours.** Anything with ≥60% of its text matched was accepted. So `Mo-Fr 07:00-12:00,13:00-18:00` (a split shift) silently became "closes at noon", and a month-scoped rule became year-round. Fix: any text outside recognized rules and separators → unknown.
3. **`;` rules added hours instead of replacing them.** In OSM syntax, `Mo-Fr 08:00-18:00; Fr 08:00-12:00` means Friday closes at 12:00, but the parser kept 18:00. Fix: `;` replaces the named days' own hours, `,` adds, and an overnight tail stays with the rule of the day it started on.
4. **`None` leaked into the report** for a snapshot with no raw file (the schema allows this). The report's own output guard caught it and refused to write. Fix: the renderer states "no raw file on record" explicitly.

Fixes 2–3 were checked against all 17 distinct real `opening_hours` strings, and none parses differently. The end-to-end reproducibility check confirms that none of the four fixes changes a real Day 5 result.

## v2: Multi-Source Competitors and Competition Recalibration (2026-09-28/29)

### Why v1 under-counted competitors

The Day 2 endpoint substitution (above) chose `overpass.atownsend.org.uk`. Its database turned out to be badly incomplete: 0–1 buildings inside each 400m circle, where `maps.mail.ru` holds 167–2,350. The same competitor query returned 2 vs. 22 elements at Brandenburger Tor and 12 vs. 92 at Schönhauser Allee. v1's thin competition was a mirror artifact, not an OSM or market feature. `osm_client.py` now puts mail.ru first and rejects any response whose building count in the circle is under 25 (`MIN_BUILDINGS_CANARY`), so an incomplete mirror fails like an outage instead of passing as "few competitors".

### What changed in the POI pipeline

- **Taxonomy**: adds beverages (Getränkemarkt) and chemist (Drogerie, not Apotheke); newsagent counts as späti/kiosk. Tobacco/food/confectionery counters count only **inside a station**, meaning within 40m of a station node, platform or station building (Berlin S/U stations have no station polygons in OSM).
- **Sources**: Overture Maps Places (confidence ≥ 0.6, closed places dropped) and Foursquare OS Places (gated on Hugging Face; needs `HF_TOKEN`, not yet ingested) are written as separate `poi_source` snapshots.
- **Conflation** (`poi_conflation.py`): the sources are matched shop-to-shop (name match within 60m, at most one OSM record per cluster) into one `poi_conflated` snapshot, the only `poi` set the metrics read. Each competitor keeps its provenance, and `osm_coverage_share` measures OSM's own under-mapping (65–69% at the three nodes).

| Node | v1 competitors | v2 competitors | in station | seen by ≥ 2 providers |
| --- | --- | --- | --- | --- |
| Brandenburger Tor | 2 | 32 | 10 | 17 |
| Warschauer Straße | 8 | 73 | 31 | 33 |
| Schönhauser Allee | 12 | 149 | 46 | 92 |

### Competition anchors: measured, not judged (scoring_v2)

On the v2 competitor set, every node saturated v1's competition anchors (5 direct-format competitors, 30/km², 150m), so the pillar no longer discriminated. `src/calibrate_anchors.py` ran the identical pipeline around the S/U-Bahn stations inside the Ringbahn. Each competition criterion now maps the reference distribution's P90 (most competition) to 0 and its P10 to 100.

| Criterion | P10 | P50 | P90 | v2 anchors (worst → best) |
| --- | --- | --- | --- | --- |
| Direct-format competitors (400m) | 5.0 | 12.0 | 27.4 | 27 → 5 |
| All competitors per km² | 29.8 | 97.5 | 232.4 | 230 → 30 |
| Nearest direct-format competitor | 11.7 m | 51.3 m | 152.2 m | 12 → 150 m |

The last row **replaces** v1's "nearest competitor of any kind". That distance is 8–85m (P10–P90) at every inner-city station, so it measured density rather than intercept risk. No direct-format competitor within 400m is recorded as 400m (a known, right-censored value, not a missing one).

Calibration sample: 127 of 139 station nodes (12 excluded after public-mirror failures, not imputed); OSM 2026-09-28/29, Overture 2026-09-23.1. Per-station values: `db/raw/calibration/competition_anchors_20260928T150337Z.json`. An interim n=83 sample (`..._20260929_interim_n83.json`, used for analysis_runs #2) gave anchors within 5 units of these.

### Result (analysis_runs #3)

| Node | Business fit v1 → v2 | Competition pillar v2 | Evidence quality |
| --- | --- | --- | --- |
| Brandenburger Tor | 27.2 → **42.5** | 77.1 | 75.9 (verify first) |
| Warschauer Straße | 25.2 → **37.5** | 31.8 | 77.5 (verify first) |
| Schönhauser Allee | 25.3 → **36.0** | 12.1 | 89.7 (adequate) |

The #1 rank is **not robust** (Brandenburger Tor tops 56% of weight draws, Schönhauser Allee 41%). The choice is a demand-vs-competition trade-off: Schönhauser Allee has the catchment, Brandenburger Tor an almost empty direct-format field. The report presents both, not a single winner. Evidence quality fell because Overture-only competitors carry no opening hours (hours known for 26–48% of competitors), so the time-window pillar rests on a smaller share than before.

## v2: Visitor Demand Signal — Attraction Draw (2026-09-29)

Demand side is split into three customer groups: commuters (IHK business mix), local residents (LOR / catchment population), and visitors. Station ridership was considered as the visitor proxy and rejected: the timetable-based estimate ([`src/transit_supply.py`](src/transit_supply.py)) applies one occupancy profile to all nodes, so its weekend figures are the weekday figures times a near-constant (Sa/weekday 0.75–0.80, Su/weekday 0.51–0.55 at all three nodes) and carry no visitor information; measured weekend ridership would still be dominated by residents (e.g. Schönhauser Allee Arcaden). Restaurant/café counts and lodging were tested and dropped: dining counts track residents (Schönhauser Allee highest in absolute terms), and lodging room counts are too incomplete in OSM to compare.

**Method** ([`src/attraction_pipeline.py`](src/attraction_pipeline.py), [`src/attraction_client.py`](src/attraction_client.py), `attraction_gateway_v1`): every wikidata-tagged attraction/venue within 2km (Overpass, full geometry) is kept for a node if the node's station is one of its main S/U gateways — within 250m of the nearest station to the site's nearest edge, or within 400m of the edge (so both ends of long sites like East Side Gallery count), and at most 1.5km away. Weight is shared 1/k among the k gateway stations (VBB GTFS stations). Each attraction is weighted by 12-month de+en Wikipedia pageviews. Day attractions and night/evening venues (`nightclub`, `music_venue`, `events_venue`, `concert_hall`, `theatre`, `stadium`, `sports_centre`) are separate metrics: `day_attraction_draw_index`, `night_venue_draw_index`, plus `*_gateway_count`.

| Node | Day draw index | Day gateways | Night draw index | Night gateways |
| --- | --- | --- | --- | --- |
| Brandenburger Tor | **639,439** | 14 | 9,884 | 1 |
| Warschauer Straße | 79,572 | 8 | **308,074** | 4 |
| Schönhauser Allee | 4,856 | 6 | 14,343 | 1 |

Pageview window 2025-09-01..2026-09-01, snapshots 30–32. Caveats that travel with the numbers: pageviews measure fame, not footfall (Berghain alone is 62% of Warschauer Straße's night index); straight-line distance ignores the Spree, so Uber Arena / Oberbaumbrücke are shared with Schlesisches Tor; 15 kept sites whose OSM wikidata item has no de/en article (e.g. Astra Kulturhaus, Ort der Information — the tag points at a building/monument item) are stored with `pageviews` = null and excluded from the index, not counted as 0.

**Wired into scoring (scoring_v3, analysis_runs #4)**: `day_visitor_draw` and `night_visitor_draw` join the demand pillar, each normalized on log10(1+x) from 1,000 (→0) to 1,000,000 (→100) share-weighted pageviews — judgment anchors like the other demand anchors, not calibrated across the 127-station set. `Criterion.weight` makes the demand pillar a weighted mean: commuters 1 : residents 1 : day 0.5 : night 0.5, so visitors carry one third of demand. Demand pillar moved BT 26.2 → 38.6, WS 54.3 → 60.6, SA 62.1 → 51.6; business fit BT 47.4, WS 40.0, SA 31.8 (Brandenburger Tor tops 65% of random weightings — still not robust; halving the competition weight puts Warschauer Straße first).

## v3: Peak Competition per Unit Demand, Missing-Data and Freshness Fixes (2026-10-01, scoring_v4)

### Changes

- **Time-window "white space" pillar removed.** It scored the *share of hours-known competitors closed* in a window, which went up with every irrelevant closed shop and ignored how many were open. At Schönhauser Allee 34 competitors were open 7–9, yet it scored the best morning gap. In its place, two **commute-peak competitor sets** by format (`poi_taxonomy.MORNING_PEAK_CATEGORIES` / `EVENING_PEAK_CATEGORIES`): morning = bakery, café, supermarket, fuel-station shop; evening = späti/kiosk, fast food, supermarket, fuel-station shop. Chemists, beverage stores and delis are in neither. A competitor is dropped from a peak only if its OSM hours show it closed for the whole window on a weekday (unknown hours count as open). New metrics: `morning_peak_competitor_count`, `evening_peak_competitor_count` (+ `*_known_closed_excluded`).
- **Competition pillar = competitors per unit demand.** Commuters, residents and visitors have no common raw unit, so the demand-pillar score (0–100) is the denominator: `adjusted = count × 50 / demand score`, i.e. the count a node of reference demand 50 would face. The adjusted count is scored on P90 → 0 / P10 → 100 of the raw peak counts at the 139 inner-Berlin reference stations (`db/raw/calibration/competition_anchors_20261001T075524Z.json`; morning 54 → 8, evening 57 → 7). Assumption: reference stations sit near demand 50 (their demand was not measured). Direct-format count, competitor density and nearest-direct-competitor distance are no longer scored. Pillars: demand 50% / competition per unit demand 50%.
- **No POI snapshot ≠ zero competitors.** An area with no POI snapshot used to get `competitor_count_* = 0`, which scored as an empty field (a simulated failed fetch at Schönhauser Allee took it from last to #1 in 98% of weightings). Every competitor-derived metric is now written missing (`metrics_pipeline.NO_POI_SNAPSHOT`); the competition pillar goes missing with it.
- **Freshness on data age, not fetch age.** The old rule (fetch < 14 days) dropped every node a tier two weeks after a fetch with nothing changed, would have failed `run_day6.py`'s reproducibility check from 2026-10-07, and counted a 2024 population figure as fresh. Now each source's `data_as_of_date` is checked against a per-source limit (`scoring.FRESHNESS_MAX_DATA_AGE_DAYS`: 90 days live/monthly, 365 attractions, 1,095 biennial MSS; LOR boundaries are versioned and not aged). `report.run()` takes `run_at`, and the reproducibility check re-scores at the baseline run's timestamp.
- **Evidence quality.** The two opening-hours components only served the removed pillar; replaced by *competitor corroboration* (share seen by ≥ 2 independent providers), since competition now rests on counts. A component whose input is missing scores 0 instead of being dropped.
- **Overpass User-Agent** — see the correction under "OSM Overpass: endpoint substitution". Calibration re-run with it: all 139 stations, none excluded (12 had failed before).

### Result (analysis_runs #5)

| Node | Morning peak (count → per demand → score) | Evening peak | Demand | Competition | Business fit | Evidence | #1 in random weightings |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Brandenburger Tor | 16 → 20.7 → 72 | 12 → 15.5 → 83 | 38.6 | 77.6 | **58.1** | 75.4 (verify first) | 52% |
| Warschauer Straße | 25 → 20.6 → 73 | 48 → 39.6 → 35 | 60.6 | 53.7 | **57.1** | 76.6 (verify first) | 48% |
| Schönhauser Allee | 65 → 62.9 → 0 | 74 → 71.6 → 0 | 51.6 | 0.0 | **25.8** | 90.4 (adequate) | 0% |

Brandenburger Tor and Warschauer Straße are one fit point apart and swap places under demand ×1.5 or competition ×0.5; the min-max method check also puts Warschauer Straße first. Per unit demand the two face the same morning competition; they differ in the evening, where Warschauer Straße's späti / fast-food density is more than twice Brandenburger Tor's. Schönhauser Allee is the robust result: saturated in both peaks for its demand, #1 in none of the 10,000 weightings.

## v4: Demand in People per Day (2026-10-01, scoring_v5)

### Why

scoring_v1–v4 scored commuters (10+-employee businesses), residents and visitors (Wikipedia pageviews, log scale) on three separate 0–100 scales and averaged them 1 : 1 : 1/3 (day) + 1/3 (night). The segment mix was therefore the same judgment call at every node, and the log scale squeezed Brandenburger Tor's 8× visitor lead over Warschauer Straße into 94 vs. 63 points.

### Method ([`src/demand_model.py`](src/demand_model.py))

Demand = **residents + workers + visitors, all in people per day**, summed. A missing segment makes the total missing (never summed as 0).

- **Residents**: `catchment_population_estimate` (unchanged).
- **Workers**: `worker_headcount_estimate` — every IHK registration in the circle at its size class's geometric midpoint (10–19 → 13.8, 5,000–7,499 → 6,123); unknown sizes counted separately (`worker_size_unknown_count`), not imputed. A registered seat, not a verified on-site headcount.
- **Visitors**: `visitor_daily_estimate` — for every gateway attraction, annual visitors = k × 12-month de+en pageviews, × the station's 1/k gateway share, / 365. k = **10.25 visitors per pageview**, the geometric mean over 37 Berlin attractions with published annual counts ([`calibrate_visitors.py`](src/calibrate_visitors.py), input `db/raw/calibration/visitor_counts_published.csv`, source: visitBerlin's most-visited ranking). A free log-log fit was rejected: the published list stops at ~126k visitors, so it is truncated on the outcome (free slope 0.49, R² 0.23), which would credit minor parks with hundreds of thousands of visitors. Interquartile range of k: 4.6–22.6; tourist landmarks run ~4 (Reichstag, Fernsehturm, Olympiastadion), zoos and family museums ~40–50.
- **Competition** is now competitors per 10,000 people/day in each commute peak.
- **Anchors measured on the reference stations** ([`calibrate_demand.py`](src/calibrate_demand.py) → `db/raw/calibration/demand_anchors_20261001T142342Z.json`, all 139 stations complete): people/day P10 7,030 → 0, P90 28,447 → 100; morning competitors per 10k P90 34.2 → 0, P10 6.3 → 100; evening P90 35.1 → 0, P10 5.5 → 100. The reference stations' visitors used one ring-wide Overpass query, the candidates' exact gateway rule (verified to reproduce the candidates' stored gateway records) and Wikidata sitelinks via one SPARQL query (identical to the candidates' API sitelinks, 49/49).
- **Fix found on the way:** `attraction_pipeline.rail_stations()` kept only GTFS names containing "(Berlin)", which dropped "S+U Berlin Hauptbahnhof" as a gateway. Fixed; no effect on the three candidates (re-checked), only on reference stations near Hauptbahnhof.
- **New report check:** re-scoring at the visitor-conversion IQR (k = 4.6 / 22.6).

At the median reference station people/day split 56% residents / 35% workers / 3% visitors — visitors matter at landmark stations, not in general, which is what a fixed one-third weight could not express.

### Result (analysis_runs #6)

| Node | Residents | Workers | Visitors | People/day | Demand | Morning per 10k | Evening per 10k | Competition | Fit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Brandenburger Tor | 1,254 | 9,268 | 18,231 (63%) | 28,753 | 100 | 5.6 → 100 | 4.2 → 100 | 100 | **100.0** |
| Warschauer Straße | 3,277 | 15,295 | 10,884 | 29,456 | 100 | 8.5 → 91 | 16.3 → 63 | 77.3 | **88.6** |
| Schönhauser Allee | 10,667 | 3,232 | 539 | 14,438 | 35 | 45.0 → 0 | 51.3 → 0 | 0 | **17.3** |

Brandenburger Tor now ranks first under every weighting, perturbation and 100% of random weightings, the min-max check agrees, and the order holds across the visitor-conversion IQR (at k = 4.6: 74.2 vs. 71.4 — close). Both leaders sit above the P90 of demand, so the demand scale clamps and the gap between them comes from competition per person.

Caveats that decide how far to trust this:

- **Single large registrations dominate the worker counts.** One financial-services registration in the 5,000–7,499 class is 6,123 of Brandenburger Tor's 9,268 workers; two retail registrations in the 2,500–4,999 class are 7,070 of Warschauer Straße's 15,295. Without registrations of ≥ 1,000 employees: Brandenburger Tor ≈ 22,600 and Warschauer Straße ≈ 22,400 people/day — still level with each other, both lower.
- **Visitors are fame converted to people.** Brandenburger Tor's 63% visitor share rests on landmarks that sell no tickets and so are outside the fit; Berghain (≈ 5,400 of Warschauer Straße's 10,884 visitors/day, even after its 1/3 gateway share) is converted like a museum and is likely over-stated.
- **Brandenburger Tor's competition score still carries the under-mapping flag** (32 mapped competitors vs. 66 IHK Gastronomie registrations). Its "100" on competition per person is the figure most worth an on-site count.

## v4.1: Verified On-Site Headcounts for Large Registrations (2026-10-01, analysis_runs #7)

IHK Gewerbedaten files a company's **whole** size class at its registered Berlin address, so a head-office seat or branch can carry a firm's entire headcount. Every registration of 500+ employees in the three candidate circles (5 in total; IHK data are anonymised, so each was identified from its coordinates via OSM) was checked against public sources. Table with sources: `db/raw/calibration/worker_onsite_verified.csv`; logic: `demand_model.workers_onsite()`.

| Node | Registration | Identified as | As registered | On-site (low–high) | Basis |
| --- | --- | --- | --- | --- | --- |
| Brandenburger Tor | Cooperative bank, 5,000–7,499 | DZ BANK AG, Pariser Platz 3 | 6,123 | **450** (300–600) | Class = DZ BANK AG company-wide (~5,560 employees). Building: 13,500 m² usable incl. 2,230 m² flats + conference hall → ~300–600 workplaces. No published Berlin headcount. |
| Warschauer Straße | Non-food retail, 2,500–4,999 (×2) | Zalando campus HQ + Stream Tower | 7,070 | **7,000** (5,000–7,070) | Zalando: campus "beherbergt rund 7.000 Mitarbeit*innen" in several buildings, both inside the circle. Registration confirmed. |
| Warschauer Straße | IT services, 500–999 | TikTok Germany GmbH, Stralauer Allee 2 | 707 | **250** (100–500) | ver.di: "über 250" Berlin staff; ~165 cuts announced July 2025. |
| Warschauer Straße | Delivery, 500–999, < 1 year old | Unidentified, WeWork Warschauer Platz 11–13 | 707 | **50** (10–150) | Couriers work citywide; office staff at a shared coworking building is an assumption. |

Effect on workers: Brandenburger Tor 9,268 → **3,595**; Warschauer Straße 15,295 → **14,111**; Schönhauser Allee 3,232 (no 500+ registrations). Registrations of 200–499 employees (11 across the three circles) are not verified and stay at their midpoint. Any future 500+ registration without a verified row is counted at its midpoint **and** listed in the report's evidence gaps (`worker_large_unverified_count`).

The reference stations' workers stay as registered (verifying ~139 stations' large registrations by hand is out of scope), so the demand scale's P90 likely sits high and the candidates are scored conservatively against it; the comparison *between* the three candidates uses one method.

### Result

| Node | Residents | Workers (verified) | Visitors | People/day | Demand | Competition | Fit | #1 in random weightings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Warschauer Straße | 3,277 | 14,111 | 10,884 | 28,272 | 98.9 | 75.5 | **87.2** | 51% |
| Brandenburger Tor | 1,254 | 3,595 | 18,231 (79%) | 23,080 | 74.8 | 98.3 | **86.6** | 49% |
| Schönhauser Allee | 10,667 | 3,232 | 539 | 14,438 | 34.6 | 0.0 | **17.3** | 0% |

The v4 "Brandenburger Tor first under every weighting" result rested on DZ BANK's company-wide headcount. With on-site figures the two leaders are 0.6 points apart and the order flips within the worker range (TikTok/Zalando low end), within the visitor-conversion range, and under demand ×0.5 / competition ×1.5. What is robust: both are far ahead of Schönhauser Allee. Brandenburger Tor wins on competition per person (the least-contested peaks in the reference set), Warschauer Straße on absolute demand (Zalando's ~7,000 staff). Deciding between them needs measured footfall, not more desk research: visitors are now 79% of Brandenburger Tor's estimated demand.

## v4.2: Workers Assigned to Their Nearest Station (2026-10-01, analysis_runs #8)

**Question checked:** is Zalando as close to Ostbahnhof as to Warschauer Straße, so that much of its workforce would commute through Ostbahnhof instead? Walking routes (OSM foot routing, `routing.openstreetmap.de`):

| Site | Warschauer Str. | Ostbahnhof | Schlesisches Tor | Brandenburger Tor | Unter den Linden |
| --- | --- | --- | --- | --- | --- |
| Zalando HQ | **691 m** | 1,310 m | 769 m (over the Oberbaumbrücke) | | |
| Zalando Stream Tower | **505 m** | 1,355 m | 903 m | | |
| TikTok | **569 m** | 1,710 m | 632 m | | |
| DZ BANK | | | | **267 m** | 874 m |

Ostbahnhof is about twice the walk and its S-Bahn lines (S3/5/7/9) also stop at Warschauer Straße, so Zalando's rail commuters have no reason to use it; U1 riders reach Schlesisches Tor too, but Warschauer Straße is the shorter walk from all three Warschauer employers. **The Ostbahnhof premise does not hold.** The general point behind it does: a worker inside a node's 400m circle whose nearest station is another one mostly commutes through that other station.

**Adjustment:** workers now count only if their IHK registration's nearest S/U station (straight line, VBB GTFS parent stations) is the node's own (`metrics_pipeline._station_assigner`, `demand_model.workers_onsite(uses_this_station=...)`); the walking checks above are recorded per large employer in `worker_onsite_verified.csv` (`station_check`) and agree with the straight-line pick. Residents are not station-assigned (top-up shopping is a walk from home, not a station trip); visitors were already shared among gateway stations. Reference stations stay whole-circle and unverified, so the demand scale stays conservative for the candidates.

| Node | Workers, whole circle | Workers, nearest station = node | Assigned away |
| --- | --- | --- | --- |
| Brandenburger Tor | 3,595 | **2,502 (70%)** | Unter den Linden 776, Anton-Wilhelm-Amo-Str. 151, Friedrichstraße 134, Bundestag 31 |
| Warschauer Straße | 14,111 | **13,951 (99%)** | Schlesisches Tor 160 |
| Schönhauser Allee | 3,232 | 3,232 (100%) | — |

The overlap problem is Brandenburger Tor's, not Warschauer Straße's: its circle reaches the dense U-Bahn stops along Unter den Linden.

### Result

| Node | Residents | Workers | Visitors | People/day | Demand | Competition | Fit | #1 in random weightings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Warschauer Straße | 3,277 | 13,951 | 10,884 | 28,112 | 98.2 | 75.2 | **86.7** | 56% |
| Brandenburger Tor | 1,254 | 2,502 | 18,231 (83%) | 21,987 | 69.7 | 97.7 | **83.7** | 44% |
| Schönhauser Allee | 10,667 | 3,232 | 539 | 14,438 | 34.6 | 0.0 | **17.3** | 0% |

Warschauer Straße leads by 3.0 points (0.6 before the station assignment). Still not robust: Brandenburger Tor is first under demand ×0.5 / competition ×1.5, at the low end of the Zalando/TikTok range, and at the high end of the visitor conversion. Brandenburger Tor's demand is now 83% estimated visitors, which makes an on-site footfall count there the single most decision-relevant next step.

## v4.3: Zalando's Campus Split by Building, Amazon Added (2026-10-01, analysis_runs #9)

**Question checked:** Zalando SE BHQ-X sits between Ostbahnhof and Warschauer Straße, ~650 m from the station — are its office workers in our count?

**Finding:** BHQ-X ("Zalando SE Headquarters", Valeska-Gert-Straße 5; 49,474 m² GFA with Valeska-Gert-Str. 4 per UBM) is **outside** the 400m circle (550 m straight-line from the anchor; nearest stations Warschauer Str. 545 m and Ostbahnhof 582 m — nearly equidistant, as suspected). Its workers were never counted directly — but v4.1 had attributed Zalando's whole ~7,000-person campus to the two IHK registrations *inside* the circle (Tamara-Danz-Straße 1/3 and Stream Tower), so the circle was over-counted by the people actually sitting in BHQ-X, M_Eins (Mühlenstraße, 13,000 m²) and Arena Boulevard (3,500 m²).

**Fix:** the campus headcount (7,000; 7,700 at the Berlin location as high) is split by gross floor area (OSM footprint × levels): Tamara-Danz 1 9,278 m² + Tamara-Danz 3 12,582 m² + Stream Tower 57,161 m² = 79,021 m² in the circle out of ~147,500 m² → Tamara-Danz 1/3 **1,040** (870–1,140), Stream Tower **2,710** (2,500–2,980; low = its ~2,500-workplace design capacity). Zalando in circle: 7,000 → **3,750**.

**Found on the way — Amazon:** the EDGE East Side Tower (Tamara-Danz-Straße 13, 82 m from the anchor, 45 m from the station) houses Amazon's Berlin staff — "bis zu 3.400 Mitarbeiter" on 28 floors (R&D, Alexa, Fire TV, AWS), move-in during 2025. The IHK data hold only small registrations at that address (Amazon files its headcount elsewhere). Added via a new table of verified employers absent from IHK, `db/raw/calibration/worker_onsite_unregistered.csv` (3,400; low 2,500), under the same nearest-station rule.

| Warschauer Straße workers | v4.2 | v4.3 |
| --- | --- | --- |
| Zalando (in circle) | 7,000 | 3,750 |
| Amazon (EDGE tower) | — | 3,400 |
| Total, nearest station = node | 13,951 | **14,101** |

Net effect almost nil; result unchanged in substance: Warschauer Straße 87.2, Brandenburger Tor 83.7, Schönhauser Allee 17.3, not robust (Brandenburger Tor first under competition ×1.5, at the low end of the worker range and at the high end of the visitor conversion).

**Structural gap this exposes:** IHK Gewerbedaten covers commercial businesses only. Employers registered elsewhere (Amazon) and the public sector — parliament, ministries, embassies — are absent unless added by hand. That matters most for Brandenburger Tor (government district, embassies on Pariser Platz). Not yet corrected; see the open item below.

## Full re-run (2026-10-02, analysis_runs #10–11)

Every source refetched and every layer recomputed in order: LOR/MSS (#33–34) → IHK (#35–37) → OSM (now from overpass-api.de) + Overture → conflation (#38–46) → attraction gateways (#47–49) → metrics → agent follow-up → metrics → report → `tests/run_day6.py`. 0 step failures; Foursquare skipped (no `HF_TOKEN`). Calibration references (competition anchors, demand anchors, visitor fit) were reused, not refetched: same data vintages, and re-running them is ~150 Overpass calls against currently overloaded public mirrors.

Fixes made for the re-run: the attraction pipeline's pageview window is pinned to `demand_model.PAGEVIEW_WINDOW` (it used a rolling "last 12 months", which on 2026-10-02 would have drifted a month away from the visitor fit and reference calibration); the report's evidence gaps show only agent flags raised on each area's current competitor snapshot (the v1-era list of 4 shops no longer appears); agent field questions name at most 10 shops; the IHK public-sector gap is stated in the report's §6.

Checks: all five verified IHK registrations matched on the fresh IHK snapshot (0 unverified large registrations); competitor sets unchanged (32 / 73 / 149); attraction indices within 0.01%. Results identical to #9: Warschauer Straße 87.2, Brandenburger Tor 83.7, Schönhauser Allee 17.3, not robust. Day 6: 119/119, end-to-end reproducible. Report: `analysis_runs #11` (not published; see the latest run).

## v5: Weekday vs. Weekend/Holiday (2026-10-02, scoring_v6, analysis_runs #12–13)

**Why:** demand and competition differ by day type. Commuters are mostly absent at the weekend; Berghain opens only Friday midnight to Monday morning (as do most clubs); Berlin's Sunday trading law closes most shops — while station shops may trade without time limit, which a station-embedded 7-Eleven could use.

**Method** (all rules in [`src/day_types.py`](src/day_types.py), shared by metrics, reference calibration and scoring):

- Day types: weekday = Wednesday; weekend/holiday = Saturday and Sunday averaged (Berlin holidays trade like Sundays). Business fit weights them by the calendar, 5 : 2, in both pillars; the report's day-type check also shows equal weights and each day type alone.
- Residents: both day types in full.
- Workers at the weekend: weekday on-site headcount × the sector's share working weekends at least occasionally (Destatis Mikrozensus 2023: hospitality 70%, arts/entertainment 55%, trade 47%, health/social 46%, all employees 27%; office sectors 10%, assumption) × 0.5 (assumed share of weekend days such a worker is on site). Verified large employers carry their own share (Zalando, Amazon, DZ BANK offices 0.10; TikTok 0.27).
- Visitors: each venue's annual visitors spread over its open days — OSM opening hours where parseable, otherwise every day, except clubs (Friday/Saturday nights only). No weekend uplift for day attractions (no source).
- Competition windows: weekday 7–9 and 16–19 (unchanged); weekend daytime 10–19 (bakery, café, supermarket, späti, fast food, fuel shop) and weekend night 22–02 (späti, fast food, fuel shop), each per 10,000 people of its own day type. Unknown-hours competitors on Sunday follow the Berliner Ladenöffnungsgesetz: supermarkets, spätis, beverage and chemist shops closed unless inside a station; bakeries 7–16.
- Reference calibration re-run for all six criteria on the 139 stations (`competition_anchors_20261002T084101Z.json`, `demand_anchors_20261002T084126Z.json`; IHK refetched with NACE divisions). Reference medians: 13,500 people per weekday vs. 9,200 per weekend day.

| Node | Weekday people (res + work + vis) | Weekend people | Weekday 7–9 / 16–19 per 10k | Weekend 10–19 / 22–02 per 10k | Fit |
| --- | --- | --- | --- | --- | --- |
| Brandenburger Tor | 1,254 + 2,502 + 18,300 = 22,055 | 1,254 + 388 + 18,300 = 19,941 | 7.3 / 5.4 | 13.8 / 5.3 | **89.0** |
| Warschauer Straße | 3,277 + 14,101 + 5,493 = 22,871 | 3,277 + 1,171 + 24,466 = 28,913 | 10.9 / 21.0 | 20.9 / 12.8 | **76.2** |
| Schönhauser Allee | 10,667 + 3,232 + 541 = 14,439 | 10,667 + 498 + 541 = 11,706 | 45.0 / 51.2 | 96.5 / 32.9 | **23.2** |

**Reading:** moving Berghain (≈5,400 of Warschauer Straße's average daily visitors) to the weekend removes most of Warschauer Straße's weekday demand edge; Brandenburger Tor's visitors arrive every day. Warschauer Straße is weekend-led (28,900 vs. 22,900 people); on the weekend both leaders exceed the reference P90 (13,900), so the capped demand criterion scores both 100 and the comparison there rests on competition, where Brandenburger Tor is less contested in every window. Brandenburger Tor is first under every pillar weighting and every day-type weighting (91% of random weightings), but **not at the low end of the visitor conversion** (4.6 visitors/pageview: Warschauer Straße 63.6 vs. 55.7) — the order depends on how many visitors fame actually produces, which a footfall count at both stations would settle. Schönhauser Allee: weekday-led, saturated in all four windows.

Open items unchanged: public-sector employers absent from IHK (most material at Brandenburger Tor); Brandenburger Tor's under-mapping flag; unknown-hours fast food counted open at night (weekend-night counts are upper bounds).

## v5.1: Public-Sector Employers at Brandenburger Tor (2026-10-02, analysis_runs #14)

IHK Gewerbedaten holds commercial businesses only, so parliament, embassies and ministries counted 0. OSM lists 22 public-sector office buildings inside Brandenburger Tor's circle whose nearest station is Brandenburger Tor (the Reichstag, Marie-Elisabeth-Lüders-Haus, Wilhelmstraße 54 and the Press Office are nearer other stations and stay out; the Reichstagspräsidentenpalais club, the Russian Embassy's pool and cultural house, a school, the Max-Liebermann-Haus and small associations are excluded as non-office or minor). Added to `db/raw/calibration/worker_onsite_unregistered.csv`, one row per building with coordinates, method and sources:

- **Jakob-Kaiser-Haus** from published figures: 1,745 offices (Bundestag, "Jakob-Kaiser-Haus in Zahlen"); ~10,000 people work for the Bundestag in >6,000 offices (~1.6 per office; MPs' staff split time with constituencies) → 1.0 / **1.3** / 1.6 people per office = 1,745 / **2,269** / 2,792.
- **Every other building** by floor area (OSM footprint × levels; 5 levels assumed where OSM has none) at the Jakob-Kaiser-Haus density measured the same way: 141,100 m² OSM floor area / 2,269 people = **62 m² per person** (range 50–81).

| Group | People (central, range) |
| --- | --- |
| Bundestag: Jakob-Kaiser-Haus 2,269, Otto-Wels-Haus 772, administration and office buildings 1,332 | ~4,370 |
| Embassies + EU representation: Russia 449, France 371, Poland 261, US 235, UK 212, unnamed (likely Hungary) 96, Europäisches Haus 93 | ~1,720 |
| Bundesministerium für Gesundheit (Berlin office) | 539 |
| **Total** | **6,629 (5,097–8,158)** |

Weekend share 0.10 (office-sector assumption). Brandenburger Tor workers: 2,502 → **9,131** per weekday (7,449–10,810), 388 → 719 per weekend day.

### Result

| Node | People per weekday | People per weekend day | Demand | Competition | Fit | #1 in random weightings |
| --- | --- | --- | --- | --- | --- | --- |
| Brandenburger Tor | 1,254 + 9,131 + 18,300 = 28,684 | 20,273 | 100 | 99.3 | **99.6** | 100% |
| Warschauer Straße | 22,871 | 28,913 | 82.6 | 69.9 | **76.2** | 0% |
| Schönhauser Allee | 14,439 | 11,706 | 46.5 | 0.0 | **23.2** | 0% |

Brandenburger Tor is now first under every pillar weighting, every day-type weighting, the full visitor-conversion range (low: 72.9 vs. 63.6) and the full worker range. **Asymmetry check:** Warschauer Straße's and Schönhauser Allee's public employers have not been inventoried (Overpass unavailable at the time), but the gap is not closable that way — even +40,000 unrecorded workers at Warschauer Straße would give 96.1 vs. 99.6, because Brandenburger Tor's lead now rests mostly on competition per person, not on demand.

**What still decides how far to trust this:** Brandenburger Tor's competition advantage carries the under-mapping flag (32 mapped competitors vs. 66 IHK Gastronomie registrations), and 64% of its weekday demand is estimated visitors (fame converted to people). An on-site competitor walk and a footfall count at Brandenburger Tor are the checks most likely to move the result.

## v5.2: Known Attendance Beats Fame — Berghain Corrected (2026-10-02, analysis_runs #15)

**Question checked:** is ~20,000 Berghain visitors per weekend day plausible? No. The fame conversion gave Berghain 577,600 pageviews × 10.25 ≈ **5.9 million visitors a year** (1.97 million after Warschauer Straße's 1/3 gateway share, ≈19,000 per weekend day). Published facts: capacity **1,500** (official; Wikipedia), open ~34 hours per weekend (Fri 22:00–Sat 10:00, Sat 23:59–Mon 10:00), average stay ~11 hours (FAZEmag, 24.06.2024) → at most ~4,600 admissions per weekend, ~240,000 a year; FAZEmag's explicitly hypothetical extrapolation (2,000–4,500 people queue per weekend, two in three turned away) → ~56,000 admitted a year. No official attendance exists. Taken: **115,000 a year** (geometric mean of the two bounds, ~2,200 admissions per weekend), range 56,000–240,000 — `db/raw/calibration/venue_attendance_verified.csv`.

**General fix:** fame overstates visits wherever it outruns capacity, so any attraction with a known annual figure now uses it instead of the pageview conversion (`demand_model.load_known_attendance` / `visitors_split`): the 37 published counts the visitor fit was trained on plus the venue table. This also corrects the Reichstag (published 1.85 million vs. 4.8 million from fame) and Uber Arena (1.9 million vs. 1.7 million). The visitor-conversion check now varies only the converted part (`visitor_*_from_pageviews`). Reference calibration re-run with the same rule (`demand_anchors_20261002T092010Z.json`).

| Node | Visitors per weekday | Visitors per weekend day | People weekday / weekend | Fit |
| --- | --- | --- | --- | --- |
| Brandenburger Tor | 18,300 → **14,172** (Reichstag published) | 18,300 → 14,172 | 24,556 / 16,145 | **92.7** |
| Warschauer Straße | 5,493 → 5,791 | 24,466 → **6,160** (Berghain) | 23,169 / 10,607 | **63.6** |
| Schönhauser Allee | 541 | 541 | 14,439 / 11,706 | **23.7** |

Warschauer Straße is no longer weekend-led: without Berghain's fame-inflated crowd, its weekend demand falls below its weekday demand, as at a typical station. Brandenburger Tor is first under every weighting, every day-type weighting (weekend only: 96.4 vs. 44.1), the visitor-conversion range (low: 71.7 vs. 54.9) and the worker range; 100% of random weightings. Remaining fame-converted venues without a known figure (other clubs, open-air landmarks such as the Brandenburger Tor itself) keep the conversion and its caveats.

## Agent v2: Pluggable Follow-Up Policies (2026-10-02)

The Day 4 loop was split into a deterministic executor ([`src/agent_policy.py`](src/agent_policy.py)) and a decision policy. The executor's guard is plain code: the gap must exist and still be open, the tool must be the rule's tool or a flag, the budget must allow a live call, and a failed source is not retried. Three policies were compared on 16 pre-registered scenarios:
- the Day 4 rule (detection order);
- an impact-ordered rule (top two nodes and the pillar driving their gap first);
- Claude ([`src/agent_llm_policy.py`](src/agent_llm_policy.py)).

The impact rule and the LLM tie on ordering, at 14/14 each. The LLM's edge is declining calls that cannot change the decision, and writing more specific field questions. The impact rule is now the default.

Plan: [plan_llm_tool_selection.md](plan_llm_tool_selection.md). Pre-registration: [comparison/policy_scenarios.md](comparison/policy_scenarios.md). Results: [comparison/policy_eval.md](comparison/policy_eval.md). Decision and caveats: [comparison/policy_findings.md](comparison/policy_findings.md). On the real data, nothing changes: every current gap is flag-only.

## v6: Benchmark Defects Fixed, Competition-Structure Check (2026-10-03, scoring_v7, analysis_runs #16–17)

- **Competition-structure check (run #16).** The competition criteria divide competitor counts by people per day, so demand enters business fit twice. The report now re-scores with absolute competitor counts in the 400m circle, anchored on the same 139 stations' raw counts. The order and the 100% random-weighting share are unchanged; Schönhauser Allee's counts sit above the city P90 in every window under either measure.
- **conflation_v2 (run #17).** The three wrong records the manual benchmark found at Brandenburger Tor are now handled: a near-duplicate merge (Ha's Café / Bäckerei Bich Ha), a name/category check (MAREDO Steakhouse tagged café), and a sourced manual correction (Zollernhof, an office building). The two rules also run on the reference stations, which were recalibrated on the same stations and the same Overture release (now cached, so the recalibration is offline). Details and before/after: [comparison/data_quality_fixes.md](comparison/data_quality_fixes.md).

| Node | Fit (run #16 → #17) | Evidence quality | Ranked #1 |
| --- | --- | --- | --- |
| Brandenburger Tor | 92.7 → **92.9** | 75.4 → 73.9 (verify first) | 100% |
| Warschauer Straße | 63.6 → **63.2** | 76.6 (verify first) | 0% |
| Schönhauser Allee | 23.7 → **23.7** | 90.4 (adequate) | 0% |
