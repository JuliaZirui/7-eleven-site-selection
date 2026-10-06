# Website Opening Hours — Coverage Check (Phase 0)

2026-10-04 · Phase 0 of [plan_llm_opening_hours.md](../plan_llm_opening_hours.md): before building LLM extraction of opening hours from shop websites, measure whether the websites are there. No LLM was called. Script: [`src/hours_coverage.py`](../src/hours_coverage.py). Raw result: [`coverage_20261004T073732Z.json`](../db/raw/website_hours/coverage_20261004T073732Z.json).

## What was measured

The input is every competitor in a scored category (bakery, café, fast food, späti/kiosk, supermarket, fuel shop) with no recorded hours in the latest conflated snapshots (#50–52): **136 shops**. For each one:

1. **URL known?** Taken from OSM `website` / `contact:website` / `url`, or Overture `websites`. Overture's field was looked up by place ID in the same release the snapshots came from (2026-09-23.1), so the shop set did not move.
2. **Reachable and allowed?** robots.txt permits the page, and it returns HTTP 200 HTML. An unreachable robots.txt counts as disallowed (RFC 9309).
3. **Hours visible?** Checked on the homepage, plus at most one same-domain "Öffnungszeiten / Kontakt / Standort" link, which is the reach the pipeline would have. "Visible" means schema.org `openingHours` in JSON-LD, or hours-like text in the HTML (a time range next to an hours word).

Each URL was fetched once, with a 1 s delay and a user agent that names the project.

## Result

| | Brandenburger Tor | Warschauer Straße | Schönhauser Allee | Total |
| --- | --- | --- | --- | --- |
| No hours, scored category | 17 | 51 | 68 | **136** |
| URL known | 4 | 21 | 37 | 62 (46%) |
| Reachable + allowed | 2 | 11 | 28 | **41 (30%)** |
| Hours visible in HTML | 1 | 7 | 3 | **11 (8%)** |

- **URLs: 46%, far more than the plan expected.** The plan counted OSM tags only (3 of 152). Overture's `websites` field supplies the URL for 59 of the 62 shops; OSM for 3.
- **Why the other 21 URLs failed:** 10 have an unreachable robots.txt, 2 are disallowed by robots.txt, and 9 return an HTTP error (404 ×3, 403 ×3, 500, 429) or no connection.
- **What the 41 reachable URLs point to** (classified by hand):

  | Kind of page | Count | Examples |
  | --- | --- | --- |
  | The shop's own site | 19 | Curry Fritze, HonigKrone, Kohlenquelle, Godshot |
  | Branch page of a chain | 7 | Denns BioMarkt market page, Starbucks store page, McCafé locator |
  | Chain or group homepage, no branch hours | 12 | starbucks.de ×2, mcdonalds.com, lavazza.com, back-factory.de, veganz.de |
  | Wrong entity or dead page | 3 | ServiceStore DB → a DB press page, a Google sign-in page |

- **Hours visible: 11 shops, about 8 of them usable.** Three of the 11 are chain pages whose hours are not this branch's (starbucks.de ×2, the Ciao Bella locator). Following one link found hours for only 1 more shop. On several of the shops' own sites the HTML holds little besides a script loader, so their hours, if any, are rendered by JavaScript that a simple fetcher does not run.

## Can it change the result at all?

Unknown hours already count as *open*, so new hours can only lower a peak count. The table below takes extremes no real extraction could exceed: every shop in the group is closed in every window.

| Assumption | Brandenburger Tor | Warschauer Straße | Schönhauser Allee | Ranking |
| --- | --- | --- | --- | --- |
| Today (run #17) | 92.9 | 63.2 | 23.7 | BT > WS > SA |
| All 41 reachable shops closed in every window | 93.2 | 68.0 | 27.7 | unchanged |
| All 62 shops with any URL closed | 93.2 | 70.9 | 32.2 | unchanged |
| All 136 shops without hours closed | 93.8 | 86.4 | 49.8 | unchanged |

**Even if every unknown-hours shop turned out closed, the order would not change.** So the most this feature could add is evidence quality, meaning more shops with known hours, not a different answer.

## Decision

**The gate as written passes:** 30% reachable and allowed, above the 25% threshold. **My recommendation is still not to build Phase 1 for this three-node study:**

1. **The gate measured the wrong thing.** A reachable URL is not usable hours. The figure that matters is about 8 of 136 shops (6%) with branch-specific hours readable from the HTML. A pipeline that renders JavaScript would raise this, but it is a different, heavier tool than the one planned.
2. **It cannot change the ranking.** The upper bound above settles that without building anything.
3. **Filling hours only at the candidates would bias the comparison.** The P10/P90 anchors come from 139 reference stations scored under the same "unknown hours count as open" rule. Removing closed shops at three nodes but not at the reference stations would make the candidates look less contested than the city they are measured against. Doing it fairly means extracting hours at all 139 stations, which is thousands of shops.

**When to revisit:** with the citywide run (next step 1 in the README), and only together with the reference stations. That run should measure the same three numbers first; the script works unchanged on any snapshot.

Until then, the 136 shops stay as they are: flagged for field research, with OSM hours preferred where they exist.
