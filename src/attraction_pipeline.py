"""Visitor-demand pipeline: day-attraction draw and night-venue draw per
candidate node, from notable attractions for which the node's station is
a main public-transport gateway -- not only those inside the 400m circle.

Per node:
  1. Overpass: every wikidata-tagged attraction/venue within 2km, with
     full geometry (attraction_client.py).
  2. Gateway rule, against every S/U station in the VBB GTFS snapshot:
     distance = station to the attraction's nearest mapped vertex. A
     station is a gateway if it is within GATEWAY_BAND_M of the nearest
     station, or within EDGE_M of the attraction's edge (so both ends of
     a long site count). The node keeps the attraction only if its own
     station is a gateway and within MAX_GATEWAY_DISTANCE_M, and gets a
     1/k share when k stations qualify.
  3. Weight = annual Wikipedia pageviews (de + en), a uniform notability
     proxy -- it measures fame, not footfall (Berghain scores like a
     top landmark), which is why night venues are kept separate.
  4. Metrics: share-weighted pageview sum and gateway count, day and
     night separately.

Straight-line distances ignore barriers (the Spree between Warschauer
Straße and Schlesisches Tor), so some attractions are over-shared.

Run directly to process all CANDIDATE_NODES:
    python3 attraction_pipeline.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from attraction_client import (
    SEARCH_RADIUS_M,
    AttractionFetchError,
    fetch_attractions,
    fetch_pageviews,
    fetch_sitelinks,
)
from demand_model import PAGEVIEW_WINDOW
from geo import haversine_m
from metrics_pipeline import _write_metric
from models import CANDIDATE_NODES, LocationInput
from poi_pipeline import OSM_LICENSE
from transit_supply import GTFS_STOPS_ZIP, GTFS_ZIP, _rows

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
RAW_DIR = PROJECT_ROOT / "db" / "raw" / "attractions"

SOURCE_NAME = "osm_attractions_gateway"
ENTITY_TYPE = "attraction"
METHOD_VERSION = "attraction_gateway_v1"

GATEWAY_BAND_M = 250
EDGE_M = 400
MAX_GATEWAY_DISTANCE_M = 1500
# Vertices per attraction kept for the distance test (large relations
# like Großer Tiergarten have thousands; an evenly thinned outline is
# accurate to well under the 250m band).
MAX_VERTICES = 400

# GTFS parent-station name(s) of each node. VBB models the Warschauer
# Straße tram stop at Revaler Str. as a separate "S Warschauer Str."
# parent; it is the same interchange and is merged here.
NODE_STATIONS: dict[str, tuple[str, ...]] = {
    "brandenburger_tor": ("S+U Brandenburger Tor (Berlin)",),
    "warschauer_strasse": ("S+U Warschauer Str. (Berlin)", "S Warschauer Str. (Berlin)"),
    "schoenhauser_allee": ("S+U Schönhauser Allee (Berlin)",),
}

# Evening-programme venues and nightlife; everything else is daytime.
NIGHT_KINDS = frozenset({
    "nightclub", "music_venue", "events_venue", "concert_hall", "theatre", "stadium", "sports_centre",
})


class AttractionPipelineError(Exception):
    """Fatal error for a single area's attraction ingestion."""


def rail_stations(zip_path: Path = GTFS_STOPS_ZIP) -> dict[str, list[tuple[float, float]]]:
    """S/U parent stations in Berlin from the GTFS snapshot, with each
    node's aliases folded into its primary station name."""
    alias = {a: names[0] for names in NODE_STATIONS.values() for a in names[1:]}
    stations: dict[str, list[tuple[float, float]]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for r in _rows(zf, "stops.txt"):
            name = r["stop_name"]
            # VBB names Berlin stations "... (Berlin)" except the main
            # station, "S+U Berlin Hauptbahnhof" -- missed by the suffix
            # test until 2026-10-01 (no effect on the three candidates).
            in_berlin = "(Berlin)" in name or name.startswith("S+U Berlin ")
            if r["location_type"] == "1" and re.match(r"^(S\+U|S|U) ", name) and in_berlin:
                stations.setdefault(alias.get(name, name), []).append((float(r["stop_lat"]), float(r["stop_lon"])))
    return stations


def _vertices(element: dict) -> list[tuple[float, float]]:
    if element["type"] == "node":
        return [(element["lat"], element["lon"])]
    points = [(p["lat"], p["lon"]) for p in element.get("geometry") or [] if p]
    for member in element.get("members", []):
        points += [(p["lat"], p["lon"]) for p in member.get("geometry") or [] if p]
        if member["type"] == "node" and "lat" in member:
            points.append((member["lat"], member["lon"]))
    if len(points) > MAX_VERTICES:
        points = points[:: len(points) // MAX_VERTICES + 1]
    return points


def attraction_kind(tags: dict[str, str]) -> str | None:
    for key in ("amenity", "tourism", "historic", "leisure"):
        if key in tags:
            return tags[key]
    return None


def gateways(
    points: list[tuple[float, float]],
    stations: dict[str, list[tuple[float, float]]],
) -> dict[str, float]:
    """Every gateway station for these points -> its distance. The 1/k
    share is 1/len(result); a station only *counts* the attraction if it
    is also within MAX_GATEWAY_DISTANCE_M. Shared with calibrate_demand.py."""
    lat0, lon0 = points[0]
    dist: dict[str, float] = {}
    for name, locs in stations.items():
        # Cheap prefilter before the full vertex scan.
        if min(haversine_m(lat0, lon0, a, b) for a, b in locs) > SEARCH_RADIUS_M + 3000:
            continue
        dist[name] = min(haversine_m(a, b, la, lo) for la, lo in points for a, b in locs)
    if not dist:
        return {}
    nearest = min(dist.values())
    return {n: d for n, d in dist.items() if d <= nearest + GATEWAY_BAND_M or d <= EDGE_M}


def gateway_share(
    points: list[tuple[float, float]],
    node_station: str,
    stations: dict[str, list[tuple[float, float]]],
) -> tuple[float, float, list[str]] | None:
    """(node station distance, share, co-gateway names) if the node's
    station is a gateway for these points, else None."""
    gw = gateways(points, stations)
    mine = gw.get(node_station)
    if mine is None or mine > MAX_GATEWAY_DISTANCE_M:
        return None
    return mine, 1 / len(gw), sorted(n for n in gw if n != node_station)


def select_gateway_attractions(
    elements: list[dict],
    node: LocationInput,
    stations: dict[str, list[tuple[float, float]]],
) -> list[dict]:
    """One record per wikidata id (OSM often maps a site twice, e.g. the
    gate as node and as way), keeping the closest mapping."""
    node_station = NODE_STATIONS[node.area_id][0]
    best: dict[str, dict] = {}
    for element in elements:
        tags = element.get("tags", {})
        qid, name, kind = tags.get("wikidata"), tags.get("name"), attraction_kind(tags)
        if element.get("type") == "count" or not qid or not name or kind is None:
            continue
        if tags.get("memorial") == "stolperstein":
            continue
        points = _vertices(element)
        if not points:
            continue
        hit = gateway_share(points, node_station, stations)
        if hit is None:
            continue
        distance, share, co = hit
        if qid in best and best[qid]["distance_m"] <= distance:
            continue
        lat = sum(p[0] for p in points) / len(points)
        lon = sum(p[1] for p in points) / len(points)
        best[qid] = {
            "external_ref": qid,
            "osm_ref": f"{element['type']}/{element['id']}",
            "name": name,
            "kind": kind,
            "category": "night" if kind in NIGHT_KINDS else "day",
            "lat": lat,
            "lon": lon,
            "distance_m": round(distance),
            "share": share,
            "co_gateways": co,
        }
    return list(best.values())


def attach_pageviews(records: list[dict], today) -> tuple[str, str]:
    """Fills record['pageviews'] (None if the item has no de/en article
    -- not notable enough to weight, excluded from the index rather
    than counted as 0). Returns the window labels."""
    # One window for the candidates, the visitor fit and the reference
    # calibration (demand_model.PAGEVIEW_WINDOW) -- a rolling "last 12
    # months" here would silently drift away from the other two. Advance
    # it deliberately, together with calibrate_visitors / calibrate_demand.
    start, end = PAGEVIEW_WINDOW
    links = fetch_sitelinks(sorted({r["external_ref"] for r in records}))
    for r in records:
        titles = links.get(r["external_ref"], {})
        r["wikipedia_titles"] = titles
        if not titles:
            r["pageviews"] = None
            continue
        per_site = {site: fetch_pageviews(site, title, start, end) for site, title in titles.items()}
        r["pageviews_by_site"] = per_site
        r["pageviews"] = sum(per_site.values())
    return start.isoformat(), end.isoformat()


def ingest_area(conn: sqlite3.Connection, node: LocationInput, stations: dict) -> dict:
    now = datetime.now(timezone.utc)
    try:
        raw_json, source_url = fetch_attractions(node.lat, node.lon, node.radius_m)
        elements = raw_json.get("elements", [])
        records = select_gateway_attractions(elements, node, stations)
        window_start, window_end = attach_pageviews(records, now.date())
    except AttractionFetchError as exc:
        raise AttractionPipelineError(f"[{node.area_id}] attraction fetch failed: {exc}") from exc

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{node.area_id}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    raw_path.write_text(json.dumps({"overpass": raw_json, "gateway_records": records}, ensure_ascii=False))
    period = f"pageviews {window_start}..{window_end} (end exclusive)"

    cur = conn.execute(
        """
        INSERT INTO source_snapshots
            (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_NAME,
            now.date().isoformat(),
            now.isoformat(),
            f"{OSM_LICENSE}; Wikimedia pageviews CC0; VBB GTFS CC BY 4.0",
            str(raw_path.relative_to(PROJECT_ROOT)),
            (
                f"endpoint={source_url}; search_radius_m={SEARCH_RADIUS_M}; raw_elements={len(elements)}; "
                f"gtfs={GTFS_ZIP.name}; band_m={GATEWAY_BAND_M}; edge_m={EDGE_M}; "
                f"max_gateway_m={MAX_GATEWAY_DISTANCE_M}; kept={len(records)}; "
                f"no_wikipedia_article={sum(r['pageviews'] is None for r in records)}; {period}"
            ),
        ),
    )
    snapshot_id = cur.lastrowid
    for r in records:
        payload = {k: r.get(k) for k in (
            "osm_ref", "kind", "distance_m", "share", "co_gateways",
            "pageviews", "pageviews_by_site", "wikipedia_titles",
        )}
        conn.execute(
            """
            INSERT INTO observations
                (snapshot_id, area_id, entity_type, external_ref, name, category, lat, lon, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, node.area_id, ENTITY_TYPE, r["external_ref"], r["name"], r["category"],
             r["lat"], r["lon"], json.dumps(payload, ensure_ascii=False)),
        )

    summary = {"area_id": node.area_id, "snapshot_id": snapshot_id}
    for category in ("day", "night"):
        weighted = [r for r in records if r["category"] == category and r["pageviews"] is not None]
        index = sum(r["pageviews"] * r["share"] for r in weighted)
        label = "day_attraction" if category == "day" else "night_venue"
        _write_metric(conn, area_id=node.area_id, metric_name=f"{label}_draw_index", value=round(index),
                      unit="share_weighted_annual_pageviews", period_label=period,
                      method_version=METHOD_VERSION, snapshot_ids=[snapshot_id])
        _write_metric(conn, area_id=node.area_id, metric_name=f"{label}_gateway_count", value=float(len(weighted)),
                      unit="count", period_label=period, method_version=METHOD_VERSION, snapshot_ids=[snapshot_id])
        summary[f"{label}_draw_index"] = round(index)
        summary[f"{label}_gateway_count"] = len(weighted)
    conn.commit()
    return summary


def run(db_path: Path = DB_PATH) -> list[dict]:
    stations = rail_stations()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    results = []
    try:
        for node in CANDIDATE_NODES:
            try:
                result = ingest_area(conn, node, stations)
            except AttractionPipelineError as exc:
                print(f"SKIPPED {exc}")
                continue
            print(result)
            results.append(result)
    finally:
        conn.close()
    return results


if __name__ == "__main__":
    run()
