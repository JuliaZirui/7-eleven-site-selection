"""Measure demand (people per day) at the competition-reference stations.

scoring_v5 scores demand as residents + workers + visitors per day, and
competition as peak competitors per 10,000 of those people. Both scales
need a reference distribution, so this script runs the candidates' own
conversion (demand_model.py) around the same 139 inner-Berlin S/U
stations calibrate_anchors.py measured competitors at:

  - residents: catchment_population() on the LOR/MSS raw files already
    on record (no API call);
  - workers:   IHK Gewerbedaten per station (one WFS call each, cached as
    the list of size classes only);
  - visitors:  one Overpass query for every wikidata-tagged attraction in
    the Ringbahn box + 2km, the candidates' gateway rule against the VBB
    GTFS stations, de + en pageviews over demand_model.PAGEVIEW_WINDOW,
    and the latest visitor fit.

Every network stage is cached under db/raw/calibration/demand_cache/ so a
failed run resumes. Writes demand_anchors_<stamp>.json with per-station
values and P10/P90 of daily people and of competitors per 10,000 people.
Never touches site_selection.db.

    python3 calibrate_demand.py
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import attraction_client
from attraction_client import OVERPASS_ATTRACTION_FILTERS, fetch_pageviews
from attraction_pipeline import MAX_GATEWAY_DISTANCE_M, _vertices, attraction_kind, gateways, rail_stations
from calibrate_anchors import RING_BBOX
from demand_model import (
    CALIBRATION_DIR,
    PAGEVIEW_WINDOW,
    latest_visitor_fit,
    load_known_attendance,
    visitors_per_day,
    visitors_split,
    workers_onsite,
)
from geo import haversine_m
from ihk_client import IhkFetchError, fetch_gewerbedaten_bbox
from lor_client import population_by_plr_id
from day_types import COMPETITION_WINDOWS
from metrics_pipeline import MAX_UNPOPULATED_SHARE, catchment_population
from osm_client import HTTP_HEADERS, OVERPASS_URLS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
CACHE_DIR = CALIBRATION_DIR / "demand_cache"
RADIUS_M = 400
# A reference station's GTFS parent station must lie this close to it.
GTFS_MATCH_M = 300
# Attractions are searched this far beyond the Ringbahn box, matching the
# candidates' 2km attraction search radius.
BOX_MARGIN_DEG = (0.02, 0.03)  # lat, lon ~ 2.2km / 2.0km at Berlin
# Pageview calls: the per-article endpoint tolerates a faster pace than
# the candidates' polite 1.2s, and 429s are retried by fetch_pageviews.
PAGEVIEW_PAUSE_S = 0.3
PEOPLE_UNIT = 10_000
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


def _cache(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def reference_stations() -> tuple[list[dict], Path]:
    path = sorted(CALIBRATION_DIR.glob("competition_anchors_*.json"))[-1]
    return json.loads(path.read_text())["stations"], path


def lor_inputs() -> tuple[dict, dict]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        refs = {
            name: conn.execute(
                "SELECT raw_file_ref FROM source_snapshots WHERE source_name = ? AND raw_file_ref IS NOT NULL "
                "ORDER BY fetched_at DESC, snapshot_id DESC LIMIT 1", (name,)).fetchone()[0]
            for name in ("berlin_lor_boundaries", "berlin_mss_population")
        }
    finally:
        conn.close()
    boundaries = json.loads((PROJECT_ROOT / refs["berlin_lor_boundaries"]).read_text())
    pop_index = population_by_plr_id(json.loads((PROJECT_ROOT / refs["berlin_mss_population"]).read_text()))
    return boundaries, pop_index


def station_workers(station: dict) -> dict:
    """Workers in the circle, weekday and weekend, from IHK size classes
    and NACE divisions (cached per station as [range, division] pairs).
    Reference stations are *not* verified or station-assigned like the
    candidates -- see the module docstring of scoring.py's anchors."""
    path = _cache(f"ihk2_{station['station']}_{station['lat']:.5f}_{station['lon']:.5f}.json".replace("/", "-"))
    if path.exists():
        regs = json.loads(path.read_text())
    else:
        raw = fetch_gewerbedaten_bbox(station["lat"], station["lon"], RADIUS_M)
        regs = [
            [f["properties"].get("employees_range"), f["properties"].get("branch_top_level_id")]
            for f in raw.get("features", [])
            if f.get("geometry")
            and haversine_m(station["lat"], station["lon"], f["geometry"]["coordinates"][1],
                            f["geometry"]["coordinates"][0]) <= RADIUS_M
        ]
        path.write_text(json.dumps(regs, ensure_ascii=False))
        time.sleep(0.5)
    w = workers_onsite([{"employees_range": r, "branch_top_level_id": b} for r, b in regs], {})
    return {"weekday": w["central"], "weekend": w["weekend_central"], "unknown_size": w["unknown_size"]}


def ring_attractions() -> list[dict]:
    path = _cache("attractions_overpass.json")
    if path.exists():
        return json.loads(path.read_text())["elements"]
    s, w, n, e = RING_BBOX
    bbox = f"({s - BOX_MARGIN_DEG[0]},{w - BOX_MARGIN_DEG[1]},{n + BOX_MARGIN_DEG[0]},{e + BOX_MARGIN_DEG[1]})"
    clauses = "\n  ".join(f'nwr["wikidata"]{f}{bbox};' for f in OVERPASS_ATTRACTION_FILTERS)
    query = f"[out:json][timeout:300];\n(\n  {clauses}\n);\nout geom;"
    errors = []
    for url in OVERPASS_URLS[:2]:
        for attempt in range(3):
            try:
                r = httpx.post(url, data={"data": query}, headers=HTTP_HEADERS, timeout=330)
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {exc}")
                continue
            if r.status_code == 200:
                path.write_text(r.text)
                return r.json()["elements"]
            errors.append(f"{url}: HTTP {r.status_code}")
            time.sleep(30)
    raise RuntimeError("ring attraction query failed:\n" + "\n".join(errors))


def gateway_records(elements: list[dict], stations: dict) -> dict[str, dict[str, dict]]:
    """GTFS station -> {qid: {pageview-less record}} under the candidates'
    rule: one record per wikidata id per station, keeping the closest
    mapping, counted only within MAX_GATEWAY_DISTANCE_M."""
    out: dict[str, dict[str, dict]] = {}
    for element in elements:
        tags = element.get("tags", {})
        qid, name, kind = tags.get("wikidata"), tags.get("name"), attraction_kind(tags)
        if not qid or not name or kind is None or tags.get("memorial") == "stolperstein":
            continue
        points = _vertices(element)
        if not points:
            continue
        gw = gateways(points, stations)
        for station, distance in gw.items():
            if distance > MAX_GATEWAY_DISTANCE_M:
                continue
            best = out.setdefault(station, {})
            if qid in best and best[qid]["distance_m"] <= distance:
                continue
            best[qid] = {"qid": qid, "name": name, "kind": kind, "distance_m": distance, "share": 1 / len(gw),
                         "opening_hours_raw": tags.get("opening_hours")}
    return out


def sitelinks_sparql(qids: list[str]) -> dict[str, dict[str, str]]:
    """{qid: {"dewiki": title, "enwiki": title}} in one query. Same data as
    attraction_client.fetch_sitelinks (Wikidata sitelinks), but one SPARQL
    request instead of 50-id API batches, which the API throttles hard
    for ~900 items. Cached."""
    path = _cache("sitelinks.json")
    if path.exists():
        return json.loads(path.read_text())
    sites = {"https://de.wikipedia.org/": "dewiki", "https://en.wikipedia.org/": "enwiki"}
    query = (
        "SELECT ?item ?site ?title WHERE { VALUES ?item { " + " ".join(f"wd:{q}" for q in qids) + " } "
        "?article schema:about ?item; schema:isPartOf ?site; schema:name ?title. "
        "VALUES ?site { <https://de.wikipedia.org/> <https://en.wikipedia.org/> } }"
    )
    for attempt in range(5):
        r = httpx.post(WIKIDATA_SPARQL, data={"query": query, "format": "json"},
                       headers={**attraction_client.WIKIMEDIA_UA, "Accept": "application/sparql-results+json"},
                       timeout=120)
        if r.status_code == 200:
            break
        time.sleep(int(r.headers.get("retry-after") or 30) + 1)
    r.raise_for_status()
    links: dict[str, dict[str, str]] = {}
    for b in r.json()["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        links.setdefault(qid, {})[sites[b["site"]["value"]]] = b["title"]["value"]
    path.write_text(json.dumps(links, ensure_ascii=False))
    return links


def pageviews_for(qids: list[str]) -> dict[str, int | None]:
    path = _cache("pageviews.json")
    done: dict[str, int | None] = json.loads(path.read_text()) if path.exists() else {}
    todo = [q for q in qids if q not in done]
    if todo:
        links = sitelinks_sparql(todo)
        attraction_client.PAGEVIEW_PAUSE_S = PAGEVIEW_PAUSE_S
        start, end = PAGEVIEW_WINDOW
        for i, q in enumerate(todo, 1):
            titles = links.get(q, {})
            done[q] = sum(fetch_pageviews(site, t, start, end) for site, t in titles.items()) if titles else None
            if i % 50 == 0:
                path.write_text(json.dumps(done))
                print(f"    pageviews {i}/{len(todo)}", flush=True)
        path.write_text(json.dumps(done))
    return done


def percentiles(values: list[float]) -> dict[str, float]:
    qs = statistics.quantiles(sorted(values), n=20, method="inclusive")
    return {"p10": qs[1], "p25": qs[4], "p50": qs[9], "p75": qs[14], "p90": qs[17]}


def run() -> dict:
    now = datetime.now(timezone.utc)
    stations, anchors_path = reference_stations()
    fit, fit_path = latest_visitor_fit()
    boundaries, pop_index = lor_inputs()
    gtfs = rail_stations()

    print("Visitors: ring-wide attraction query ...", flush=True)
    by_station = gateway_records(ring_attractions(), gtfs)
    qids = sorted({q for recs in by_station.values() for q in recs})
    print(f"  {len(qids)} gateway attractions across {len(by_station)} GTFS stations; fetching pageviews", flush=True)
    pv = pageviews_for(qids)
    sitelinks = sitelinks_sparql(qids)
    known = load_known_attendance(fit)

    rows, failed = [], []
    for i, st in enumerate(stations, 1):
        row = {k: st[k] for k in ("station", "lat", "lon")}
        row.update({f"{w.key}_competitor_count": st.get(f"{w.key}_competitor_count") for w in COMPETITION_WINDOWS})

        c = catchment_population(st["lat"], st["lon"], RADIUS_M, boundaries, pop_index)
        row["residents"] = None if c["unpopulated_share"] > MAX_UNPOPULATED_SHARE else c["estimate"]

        try:
            w = station_workers(st)
            row["workers"], row["workers_weekend"], row["worker_size_unknown"] = w["weekday"], w["weekend"], w["unknown_size"]
        except IhkFetchError as exc:
            row["workers"] = row["workers_weekend"] = None
            failed.append({"station": st["station"], "stage": "ihk", "error": str(exc)})

        dists = {name: min(haversine_m(st["lat"], st["lon"], a, b) for a, b in locs) for name, locs in gtfs.items()}
        gtfs_name = min(dists, key=dists.get)
        if dists[gtfs_name] > GTFS_MATCH_M:
            row["visitors"] = row["visitors_weekday"] = row["visitors_weekend"] = None
            failed.append({"station": st["station"], "stage": "gtfs_match", "error": f"nearest {gtfs_name} {dists[gtfs_name]:.0f}m"})
        else:
            records = [{**r, "pageviews": pv.get(q), "wikipedia_titles": sitelinks.get(q, {})}
                       for q, r in by_station.get(gtfs_name, {}).items()]
            row["gtfs_station"] = gtfs_name
            kn, conv, row["attractions_without_article"] = visitors_split(records, fit, None, known)
            row["visitors"] = kn + conv
            for day_type in ("weekday", "weekend"):
                kn, conv, _ = visitors_split(records, fit, day_type, known)
                row[f"visitors_{day_type}"] = kn + conv
            row["gateway_attractions"] = len(records)

        for day_type, wk, vis in (("weekday", "workers", "visitors_weekday"), ("weekend", "workers_weekend", "visitors_weekend")):
            parts = (row["residents"], row[wk], row[vis])
            row[f"people_{day_type}"] = None if any(p is None for p in parts) else sum(parts)
        row["daily_people"] = row["people_weekday"]
        for w in COMPETITION_WINDOWS:
            people, count = row[f"people_{w.day_type}"], row[f"{w.key}_competitor_count"]
            row[f"{w.key}_competitors_per_10k_people"] = (
                None if not people or count is None else count / (people / PEOPLE_UNIT))
        rows.append(row)
        print(f"  [{i}/{len(stations)}] {st['station']}: residents={row['residents'] and round(row['residents'])} "
              f"workers={row['workers'] and round(row['workers'])} visitors={row['visitors'] and round(row['visitors'])}",
              flush=True)

    keys = ["residents", "workers", "workers_weekend", "visitors_weekday", "visitors_weekend",
            "people_weekday", "people_weekend"] + [f"{w.key}_competitors_per_10k_people" for w in COMPETITION_WINDOWS]
    out = {
        "computed_at": now.isoformat(),
        "radius_m": RADIUS_M,
        "competition_anchors": anchors_path.name,
        "visitor_fit": fit_path.name,
        "pageview_window": [d.isoformat() for d in PAGEVIEW_WINDOW],
        "n_stations": len(rows),
        "n_complete": sum(1 for r in rows if r["daily_people"]),
        "failed": failed,
        "percentiles": {k: percentiles([r[k] for r in rows if r.get(k) is not None]) for k in keys},
        "stations": rows,
    }
    path = CALIBRATION_DIR / f"demand_anchors_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"{out['n_complete']}/{len(rows)} complete stations -> {path.relative_to(PROJECT_ROOT)}")
    for k, pct in out["percentiles"].items():
        print(f"  {k:40s} " + "  ".join(f"{q}={v:,.1f}" for q, v in pct.items()))
    return out


if __name__ == "__main__":
    run()
