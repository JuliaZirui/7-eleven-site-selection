"""Day 2 pipeline (v2: multi-source): fetch competitor POIs from OSM,
Overture Maps Places and Foursquare OS Places, clean/classify each into
the shared taxonomy, write one source_snapshots + observations set per
source per area, then hand off to poi_conflation.py to build the single
deduplicated competitor set the metrics read.

Run directly to process all CANDIDATE_NODES:
    python3 poi_pipeline.py
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from geo import distance_to_path_m, haversine_m
from models import CANDIDATE_NODES, LocationInput
from fsq_client import FSQ_LICENSE, FsqError, fetch_fsq_places
from osm_client import OverpassError, building_count, fetch_overpass_pois
from overture_client import OVERTURE_LICENSE, OvertureError, fetch_overture_places
from poi_taxonomy import (
    STATION_RADIUS_M,
    classify,
    classify_fsq,
    classify_overture,
    fsq_leaf,
    is_station_feature,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
RAW_DIR = PROJECT_ROOT / "db" / "raw" / "osm"
OVERTURE_RAW_DIR = PROJECT_ROOT / "db" / "raw" / "overture"
FSQ_RAW_DIR = PROJECT_ROOT / "db" / "raw" / "fsq"
FSQ_CACHE_DIR = FSQ_RAW_DIR / "cache"

# Two raw elements are treated as the same physical POI (dedup, not two
# competitors) if they share a normalized name and sit within this
# distance of each other. 25m covers e.g. a shop mapped as both an
# entrance node and a building way/relation; it's tight enough not to
# collapse two distinct kiosks on the same street corner.
DEDUP_DISTANCE_M = 25.0

OSM_LICENSE = "ODbL (OpenStreetMap contributors)"

# Every per-source POI record (OSM, Overture, Foursquare) is written as
# entity_type='poi_source'. Only poi_conflation.py writes 'poi' -- the
# one deduplicated competitor set per area that metrics, scoring and
# the report read -- so no consumer can ever count the same shop once
# per source.
SOURCE_ENTITY_TYPE = "poi_source"


class PoiPipelineError(Exception):
    """Fatal error for a single area's POI ingestion (caller decides whether to continue)."""


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return "".join(ch.lower() for ch in name if ch.isalnum())


def _element_coords(element: dict) -> tuple[float, float] | None:
    """Node has lat/lon directly; way/relation need 'out center' -> center.lat/lon."""
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    return None


def _station_paths(elements: list[dict]) -> list[list[tuple[float, float]]]:
    """Geometry of every station anchor (station node, platform way,
    station building) in an `out geom` response, as (lat, lon) paths."""
    paths: list[list[tuple[float, float]]] = []
    for element in elements:
        if element.get("type") == "count" or not is_station_feature(element.get("tags", {})):
            continue
        if "geometry" in element:
            paths.append([(p["lat"], p["lon"]) for p in element["geometry"] if p])
        elif "members" in element:
            for member in element["members"]:
                if member.get("geometry"):
                    paths.append([(p["lat"], p["lon"]) for p in member["geometry"] if p])
                elif "lat" in member and "lon" in member:
                    paths.append([(member["lat"], member["lon"])])
        elif "lat" in element and "lon" in element:
            paths.append([(element["lat"], element["lon"])])
    return paths


def _in_station(lat: float, lon: float, station_paths: list[list[tuple[float, float]]]) -> bool:
    return any(distance_to_path_m(lat, lon, path) <= STATION_RADIUS_M for path in station_paths)


def clean_and_classify(elements: list[dict]) -> tuple[list[dict], int]:
    """Parse raw Overpass elements into clean POI records, classify, and
    dedup near-identical entries. Returns (records, n_duplicates_dropped).

    Station anchors in the same response (see osm_client) are used only
    to set each record's in_station flag -- they are never competitors.
    Station-only shop types (tobacco, food counters, ...) are kept only
    when that flag is set.

    Each record: external_ref, name, category, lat, lon,
    opening_hours_raw, in_station, tags (full raw tag dict for payload_json).
    """
    station_paths = _station_paths(elements)
    candidates: list[dict] = []
    for element in elements:
        if element.get("type") == "count":
            continue
        tags = element.get("tags", {})
        if is_station_feature(tags):
            continue
        coords = _element_coords(element)
        if coords is None:
            continue
        lat, lon = coords
        in_station = _in_station(lat, lon, station_paths)
        category = classify(tags, in_station=in_station)
        if category is None:
            continue
        candidates.append(
            {
                "external_ref": f"{element['type']}/{element['id']}",
                "name": tags.get("name"),
                "category": category.value,
                "lat": lat,
                "lon": lon,
                "opening_hours_raw": tags.get("opening_hours"),
                "in_station": in_station,
                "tags": tags,
            }
        )

    # Dedup: two candidates with the same normalized name (non-empty)
    # within DEDUP_DISTANCE_M are the same physical POI. Unnamed POIs
    # are never deduped against each other (too ambiguous to merge
    # blind) — they're kept as-is, distinctness unresolved by name.
    kept: list[dict] = []
    dropped = 0
    for cand in candidates:
        cand_key = _normalize_name(cand["name"])
        is_dup = False
        if cand_key:
            for k in kept:
                if _normalize_name(k["name"]) != cand_key:
                    continue
                if haversine_m(cand["lat"], cand["lon"], k["lat"], k["lon"]) <= DEDUP_DISTANCE_M:
                    is_dup = True
                    break
        if is_dup:
            dropped += 1
        else:
            kept.append(cand)

    return kept, dropped


def _write_source_snapshot(
    conn: sqlite3.Connection,
    node: LocationInput,
    *,
    source_name: str,
    fetched: datetime,
    data_as_of: str,
    license_: str,
    raw_path: Path,
    notes: str,
    records: list[dict],
) -> int:
    """One source_snapshots row plus its entity_type='poi_source' observations."""
    cur = conn.execute(
        """
        INSERT INTO source_snapshots
            (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_name, data_as_of, fetched.isoformat(), license_, str(raw_path.relative_to(PROJECT_ROOT)), notes),
    )
    snapshot_id = cur.lastrowid
    for rec in records:
        conn.execute(
            """
            INSERT OR IGNORE INTO observations
                (snapshot_id, area_id, entity_type, external_ref, name, category,
                 lat, lon, opening_hours_raw, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                node.area_id,
                SOURCE_ENTITY_TYPE,
                rec["external_ref"],
                rec["name"],
                rec["category"],
                rec["lat"],
                rec["lon"],
                rec["opening_hours_raw"],
                json.dumps(rec["payload"], ensure_ascii=False, default=str),
            ),
        )
    conn.commit()
    return snapshot_id


def _write_raw(raw_dir: Path, node: LocationInput, now: datetime, payload) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{node.area_id}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return raw_path


def ingest_area(conn: sqlite3.Connection, node: LocationInput) -> dict:
    """Fetch, clean, and persist one area's OSM competitor POIs.

    Raises PoiPipelineError if the Overpass fetch fails after exhausting
    all endpoints/retries — the area gets no snapshot and no observations
    (never silently written with partial/fabricated data).
    """
    now = datetime.now(timezone.utc)

    try:
        raw_json, source_url = fetch_overpass_pois(node.lat, node.lon, node.radius_m)
    except OverpassError as exc:
        raise PoiPipelineError(f"[{node.area_id}] Overpass fetch failed: {exc}") from exc

    raw_path = _write_raw(RAW_DIR, node, now, raw_json)
    elements = raw_json.get("elements", [])
    records, n_dropped = clean_and_classify(elements)
    for rec in records:
        rec["payload"] = {"in_station": rec["in_station"], "tags": rec["tags"]}
    n_station = sum(1 for e in elements if e.get("type") != "count" and is_station_feature(e.get("tags", {})))

    snapshot_id = _write_source_snapshot(
        conn,
        node,
        source_name="osm_overpass",
        fetched=now,
        data_as_of=now.date().isoformat(),  # OSM is a live dataset; "as of" = the date it was queried
        license_=OSM_LICENSE,
        raw_path=raw_path,
        notes=(
            f"endpoint={source_url}; radius_m={node.radius_m}; raw_elements={len(elements)}; "
            f"station_anchors={n_station}; buildings_canary={building_count(raw_json)}; "
            f"kept={len(records)}; in_station={sum(r['in_station'] for r in records)}; deduped={n_dropped}"
        ),
        records=records,
    )
    return {
        "area_id": node.area_id,
        "snapshot_id": snapshot_id,
        "raw_elements": len(elements),
        "kept": len(records),
        "deduped": n_dropped,
    }


# Overture quality gate. Records below this confidence are dominated by
# BrightQuery company-register rows (confidence exactly 0.5): registered
# seats such as "Barney's Roadhouse" filed as a cafe, often with no
# storefront. Those are the IHK signal again, not competitors. Closed
# places are dropped outright. Both thresholds are recorded in notes.
OVERTURE_MIN_CONFIDENCE = 0.6
OVERTURE_EXCLUDED_STATUS = ("permanently_closed", "temporarily_closed")


def normalize_overture(rows: list[dict]) -> tuple[list[dict], dict]:
    records, dropped = [], {"closed": 0, "low_confidence": 0, "out_of_scope": 0, "no_coords": 0, "duplicate_id": 0}
    seen: set[str] = set()
    for row in rows:
        if row["id"] in seen:
            dropped["duplicate_id"] += 1
            continue
        seen.add(row["id"])
        category = classify_overture(row.get("category_hierarchy"), row.get("category_primary"))
        if category is None:
            dropped["out_of_scope"] += 1
            continue
        if row.get("operating_status") in OVERTURE_EXCLUDED_STATUS:
            dropped["closed"] += 1
            continue
        if (row.get("confidence") or 0.0) < OVERTURE_MIN_CONFIDENCE:
            dropped["low_confidence"] += 1
            continue
        if row.get("lat") is None or row.get("lon") is None:
            dropped["no_coords"] += 1
            continue
        records.append(
            {
                "external_ref": f"overture:{row['id']}",
                "name": row.get("name"),
                "category": category.value,
                "lat": row["lat"],
                "lon": row["lon"],
                "opening_hours_raw": None,  # Overture carries no opening hours
                "payload": {
                    "confidence": row.get("confidence"),
                    "operating_status": row.get("operating_status"),
                    "brand": row.get("brand"),
                    "category_primary": row.get("category_primary"),
                    "upstream_datasets": list(row.get("upstream_datasets") or []),
                },
            }
        )
    return records, dropped


def ingest_overture_area(conn: sqlite3.Connection, node: LocationInput) -> dict:
    now = datetime.now(timezone.utc)
    try:
        rows, meta = fetch_overture_places(node.lat, node.lon, node.radius_m)
    except OvertureError as exc:
        raise PoiPipelineError(f"[{node.area_id}] Overture fetch failed: {exc}") from exc
    raw_path = _write_raw(OVERTURE_RAW_DIR, node, now, {"meta": meta, "rows": rows})
    records, dropped = normalize_overture(rows)
    snapshot_id = _write_source_snapshot(
        conn,
        node,
        source_name="overture_places",
        fetched=now,
        data_as_of=meta["release"][:10],  # monthly release: the data is as of its release date
        license_=OVERTURE_LICENSE,
        raw_path=raw_path,
        notes=(
            f"release={meta['release']}; bbox_rows={len(rows)}; kept={len(records)}; "
            f"min_confidence={OVERTURE_MIN_CONFIDENCE}; dropped={json.dumps(dropped)}"
        ),
        records=records,
    )
    return {"area_id": node.area_id, "snapshot_id": snapshot_id, "raw_elements": len(rows), "kept": len(records)}


# Foursquare keeps places long after they stop being checked in to;
# a place nobody has refreshed in two years is more likely closed than
# open, so it is dropped rather than counted as a competitor.
FSQ_MAX_AGE_DAYS = 730


def normalize_fsq(rows: list[dict], today: date) -> tuple[list[dict], dict, Counter]:
    records, dropped, unmapped = [], {"closed": 0, "stale": 0, "out_of_scope": 0}, Counter()
    cutoff = (today - timedelta(days=FSQ_MAX_AGE_DAYS)).isoformat()
    for row in rows:
        labels = list(row.get("fsq_category_labels") or [])
        category = classify_fsq(labels)
        if category is None:
            dropped["out_of_scope"] += 1
            unmapped.update(fsq_leaf(label) for label in labels[:1])
            continue
        if row.get("date_closed"):
            dropped["closed"] += 1
            continue
        if not row.get("date_refreshed") or row["date_refreshed"][:10] < cutoff:
            dropped["stale"] += 1
            continue
        records.append(
            {
                "external_ref": f"fsq:{row['fsq_place_id']}",
                "name": row.get("name"),
                "category": category.value,
                "lat": row["lat"],
                "lon": row["lon"],
                "opening_hours_raw": None,  # not part of the open dataset
                "payload": {"date_refreshed": row.get("date_refreshed"), "labels": labels},
            }
        )
    return records, dropped, unmapped


def ingest_fsq_area(conn: sqlite3.Connection, node: LocationInput) -> dict:
    now = datetime.now(timezone.utc)
    try:
        rows, meta = fetch_fsq_places(node.lat, node.lon, node.radius_m, FSQ_CACHE_DIR)
    except FsqError as exc:
        raise PoiPipelineError(f"[{node.area_id}] Foursquare OS Places fetch failed: {exc}") from exc
    raw_path = _write_raw(FSQ_RAW_DIR, node, now, {"meta": meta, "rows": rows})
    records, dropped, unmapped = normalize_fsq(rows, now.date())
    snapshot_id = _write_source_snapshot(
        conn,
        node,
        source_name="fsq_os_places",
        fetched=now,
        data_as_of=meta["release"],
        license_=FSQ_LICENSE,
        raw_path=raw_path,
        notes=(
            f"release={meta['release']}; bbox_rows={len(rows)}; kept={len(records)}; "
            f"max_age_days={FSQ_MAX_AGE_DAYS}; dropped={json.dumps(dropped)}; "
            f"top_unmapped_leaves={json.dumps(unmapped.most_common(10), ensure_ascii=False)}"
        ),
        records=records,
    )
    return {"area_id": node.area_id, "snapshot_id": snapshot_id, "raw_elements": len(rows), "kept": len(records)}


# Ingest step per source. OSM is mandatory for conflation (it carries
# opening hours and the station anchors); the other two only add
# coverage, so their failure degrades the area to fewer sources -- which
# the conflated snapshot's notes then state -- instead of skipping it.
SOURCE_INGESTERS = {
    "osm_overpass": ingest_area,
    "overture_places": ingest_overture_area,
    "fsq_os_places": ingest_fsq_area,
}


def run(db_path: Path = DB_PATH, sources: tuple[str, ...] = tuple(SOURCE_INGESTERS)) -> list[dict]:
    from poi_conflation import conflate_area

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    results = []
    try:
        for node in CANDIDATE_NODES:
            area_result: dict = {"area_id": node.area_id, "sources": {}}
            for source_name in sources:
                try:
                    result = SOURCE_INGESTERS[source_name](conn, node)
                    print(
                        f"[{node.area_id}] {source_name} snapshot #{result['snapshot_id']}: "
                        f"{result['raw_elements']} raw -> {result['kept']} kept"
                    )
                    area_result["sources"][source_name] = result
                except PoiPipelineError as exc:
                    print(f"SKIPPED: {exc}")
                    area_result["sources"][source_name] = {"error": str(exc)}
            if "error" in area_result["sources"].get("osm_overpass", {"error": "not requested"}):
                area_result["error"] = area_result["sources"].get("osm_overpass", {}).get("error", "OSM not ingested")
                print(f"[{node.area_id}] no fresh OSM snapshot -- conflation skipped, previous competitor set stays current")
            else:
                conflated = conflate_area(conn, node)
                print(
                    f"[{node.area_id}] conflated snapshot #{conflated['snapshot_id']}: "
                    f"{conflated['competitors']} competitors from {conflated['source_records']} source records"
                )
                area_result["conflated"] = conflated
            results.append(area_result)
    finally:
        conn.close()
    return results


if __name__ == "__main__":
    run()
