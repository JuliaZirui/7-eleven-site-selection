"""Calibrate scoring.py's competition anchors on a Berlin reference set.

scoring_v1's competition anchors (5 direct-format competitors, 30
competitors/km², 150m to the nearest competitor) were sized against the
under-counted v1 POI data -- OSM from an incomplete Overpass mirror. On
the multi-source competitor set every candidate node saturates them, so
the pillar stops discriminating.

Rather than re-guess them, this script measures the same three metrics
around every S/U-Bahn station inside Berlin's Ringbahn -- the inner-city
transit nodes the shortlist was drawn from -- using exactly the pipeline
the candidates go through (same Overpass filters + station expansion,
same Overture quality gates, same conflation, same 400m radius). The
anchors are then percentiles of that distribution: a node at the P90
of direct-format competition scores 0, one at the P10 scores 100.

A single bulk Overpass query over the whole ring times out on public
mirrors (504), so OSM is fetched per station with the candidates' own
client -- ~100 calls, cached under db/raw/calibration/osm_cache/ so a
failed run resumes. Overture is one bbox extract. Writes the full
per-station distribution to db/raw/calibration/ and never touches
site_selection.db. Run it only
when the POI pipeline or taxonomy changes:
    python3 calibrate_anchors.py
To re-run on the same stations and the same Overture release (so only a
code change moves the anchors), with no network call at all:
    python3 calibrate_anchors.py --stations-from <earlier competition_anchors_*.json>
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from geo import haversine_m
from day_types import COMPETITION_WINDOWS, count_window_competitors
from models import LocationInput
from osm_client import HTTP_HEADERS, OVERPASS_URLS, OverpassError, fetch_overpass_pois
from overture_client import fetch_overture_places
from poi_conflation import CONFLATION_METHOD_VERSION, build_competitor_set
from poi_pipeline import _station_paths, clean_and_classify, normalize_overture
from poi_taxonomy import DIRECT_FORMAT_CATEGORIES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "db" / "raw" / "calibration"

RADIUS_M = 400
# Bounding box of the Ringbahn (Westkreuz -> Ostkreuz, Gesundbrunnen ->
# Südkreuz), stations on the ring included. All three candidates lie in it.
RING_BBOX = (52.470, 13.280, 52.552, 13.470)  # south, west, north, east
# S- and U-Bahn platforms of the same station (e.g. "Warschauer Straße"
# S and U) are one node for siting purposes.
SAME_STATION_M = 300
MIN_EXPECTED_STATIONS = 50  # sanity floor: the Ringbahn area holds well over 100 S/U station nodes
POLITE_DELAY_S = 1.0  # between per-station calls to a shared public mirror
CACHE_DIR = OUT_DIR / "osm_cache"
# Pinned so a re-calibration moves the anchors only through code changes,
# not through a new monthly release. The ring extract is cached next to
# the OSM cache (~3 MB).
OVERTURE_RELEASE = "2026-09-23.1"
OVERTURE_RING_CACHE = OUT_DIR / f"overture_ring_{OVERTURE_RELEASE}.json"



def fetch_station_list() -> tuple[list[dict], str]:
    """S/U-Bahn station nodes inside the Ringbahn bbox (small query)."""
    s, w, n, e = RING_BBOX
    query = (
        "[out:json][timeout:60];\n"
        f'node["railway"~"^(station|halt)$"]["station"~"^(subway|light_rail)$"]({s},{w},{n},{e});\n'
        "out body;"
    )
    errors = []
    for url in OVERPASS_URLS:
        for _ in range(3):
            try:
                r = httpx.post(url, data={"data": query}, headers=HTTP_HEADERS, timeout=90)
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {exc}")
                continue
            if r.status_code == 200:
                elements = r.json().get("elements", [])
                if len(elements) >= MIN_EXPECTED_STATIONS:
                    return elements, url
                errors.append(f"{url}: only {len(elements)} stations -- incomplete mirror")
                break
            errors.append(f"{url}: HTTP {r.status_code}")
            time.sleep(5)
    raise RuntimeError("station list fetch failed:\n" + "\n".join(errors))


def reference_stations(elements: list[dict]) -> list[dict]:
    """Station nodes, same-name S+U pairs within SAME_STATION_M merged."""
    merged: list[dict] = []
    for e in elements:
        name = e.get("tags", {}).get("name")
        if not name:
            continue
        for m in merged:
            if m["name"] == name and haversine_m(m["lat"], m["lon"], e["lat"], e["lon"]) <= SAME_STATION_M:
                m["members"].append((e["lat"], e["lon"]))
                m["lat"] = statistics.fmean(p[0] for p in m["members"])
                m["lon"] = statistics.fmean(p[1] for p in m["members"])
                break
        else:
            merged.append({"name": name, "lat": e["lat"], "lon": e["lon"], "members": [(e["lat"], e["lon"])]})
    return merged


def station_osm(station: dict) -> dict:
    """Per-station Overpass response via the candidates' own client
    (same query, canary and mirror fallback), cached so a flaky public
    mirror can be resumed rather than restarted."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{station['name']}_{station['lat']:.5f}_{station['lon']:.5f}".replace("/", "-").replace(" ", "_")
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    raw, _ = fetch_overpass_pois(station["lat"], station["lon"], RADIUS_M)
    path.write_text(json.dumps(raw, ensure_ascii=False))
    time.sleep(POLITE_DELAY_S)
    return raw


def ring_overture() -> tuple[list[dict], dict]:
    """The pinned release's in-scope places for the whole ring, cached."""
    if OVERTURE_RING_CACHE.exists():
        cached = json.loads(OVERTURE_RING_CACHE.read_text())
        return cached["rows"], cached["meta"]
    s, w, n, e = RING_BBOX
    center_lat, center_lon = (s + n) / 2, (w + e) / 2
    half_extent_m = max(haversine_m(center_lat, center_lon, n, center_lon), haversine_m(center_lat, center_lon, center_lat, e))
    rows, meta = fetch_overture_places(center_lat, center_lon, int(half_extent_m) + 500, release=OVERTURE_RELEASE)
    OVERTURE_RING_CACHE.write_text(json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, default=str))
    return rows, meta


def station_metrics(station: dict, osm_elements: list[dict], overture_records: list[dict]) -> dict:
    node = LocationInput(area_id=station["name"], name=station["name"], lat=station["lat"], lon=station["lon"],
                         radius_m=RADIUS_M)
    osm_records, _ = clean_and_classify(osm_elements)
    osm = [
        {"source": "osm_overpass", "ref": r["external_ref"], "name": r["name"], "category": r["category"],
         "lat": r["lat"], "lon": r["lon"], "opening_hours_raw": r["opening_hours_raw"],
         "payload": {"in_station": r["in_station"]}}
        for r in osm_records
    ]
    overture = [
        {"source": "overture_places", "ref": r["external_ref"], "name": r["name"], "category": r["category"],
         "lat": r["lat"], "lon": r["lon"], "opening_hours_raw": None, "payload": r["payload"]}
        for r in overture_records
        if haversine_m(node.lat, node.lon, r["lat"], r["lon"]) <= RADIUS_M + 100
    ]
    competitors, stats = build_competitor_set(
        node, {"osm_overpass": osm, "overture_places": overture}, _station_paths(osm_elements)
    )
    area_km2 = math.pi * (RADIUS_M / 1000.0) ** 2
    distances = [haversine_m(node.lat, node.lon, c["lat"], c["lon"]) for c in competitors]
    direct_distances = [
        haversine_m(node.lat, node.lon, c["lat"], c["lon"]) for c in competitors if c["category"] in DIRECT_FORMAT_CATEGORIES
    ]
    return {
        "station": station["name"],
        "lat": node.lat,
        "lon": node.lon,
        "competitors": len(competitors),
        "direct_format_competitors": len(direct_distances),
        "competitor_density_per_km2": len(competitors) / area_km2,
        "nearest_competitor_m": min(distances) if distances else None,
        "nearest_direct_format_m": min(direct_distances) if direct_distances else None,
        **{f"{w.key}_competitor_count": count_window_competitors(competitors, w)[0] for w in COMPETITION_WINDOWS},
        "quality_corrections": {
            "near_duplicates_merged": [[r["name"] for r in m["records"]] for m in stats["near_duplicates_merged"]],
            **{k: [x["name"] for x in v] for k, v in stats["excluded"].items()},
        },
    }


def percentiles(values: list[float]) -> dict[str, float]:
    qs = statistics.quantiles(sorted(values), n=20, method="inclusive")  # 5% steps
    return {"p10": qs[1], "p25": qs[4], "p50": qs[9], "p75": qs[14], "p90": qs[17]}


def run(stations_from: Path | None = None) -> dict:
    now = datetime.now(timezone.utc)
    if stations_from is not None:
        earlier = json.loads(stations_from.read_text())
        stations = [{"name": r["station"], "lat": r["lat"], "lon": r["lon"]} for r in earlier["stations"]]
        osm_url = earlier["osm_endpoint"]
    else:
        station_elements, osm_url = fetch_station_list()
        stations = reference_stations(station_elements)

    overture_rows, overture_meta = ring_overture()
    overture_records, _ = normalize_overture(overture_rows)

    per_station, failed = [], []
    for i, st in enumerate(stations, 1):
        try:
            per_station.append(station_metrics(st, station_osm(st)["elements"], overture_records))
        except OverpassError as exc:
            failed.append(st["name"])
            print(f"  [{i}/{len(stations)}] {st['name']}: Overpass failed, excluded ({str(exc).splitlines()[-1]})")
            continue
        print(f"  [{i}/{len(stations)}] {st['name']}: {per_station[-1]['competitors']} competitors", flush=True)

    summary = {
        key: percentiles([r[key] for r in per_station if r[key] is not None])
        for key in ("direct_format_competitors", "competitor_density_per_km2", "nearest_competitor_m",
                    "nearest_direct_format_m", *(f"{w.key}_competitor_count" for w in COMPETITION_WINDOWS))
    }
    out = {
        "computed_at": now.isoformat(),
        "radius_m": RADIUS_M,
        "ring_bbox": RING_BBOX,
        "osm_endpoint": osm_url,
        "failed_stations": failed,
        "overture_release": overture_meta["release"],
        "conflation_method": CONFLATION_METHOD_VERSION,
        "stations_from": stations_from.name if stations_from else None,
        "n_stations": len(per_station),
        "percentiles": summary,
        "stations": per_station,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"competition_anchors_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"{len(per_station)} reference stations -> {path.relative_to(PROJECT_ROOT)}")
    for key, pct in summary.items():
        print(f"  {key:30s} " + "  ".join(f"{k}={v:.1f}" for k, v in pct.items()))
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stations-from", type=Path, help="reuse the station set of an earlier anchors file")
    run(parser.parse_args().stations_from)
