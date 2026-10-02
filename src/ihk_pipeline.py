"""Day 4 (if-time-allows) pipeline: fetch IHK Gewerbedaten business
points within each candidate node's 400m radius, cut to the exact
circle, and write a source_snapshots + observations row set per area.

Mirrors poi_pipeline.py's per-area ingestion shape. Unlike Overpass
(which filters by radius itself), this WFS's own DWITHIN filter
doesn't work -- see ihk_client.py -- so the exact-radius cut happens
here in Python instead, over a padded bbox fetch.

Run directly to process all CANDIDATE_NODES:
    python3 ihk_pipeline.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from geo import haversine_m
from ihk_client import GEWERBEDATEN_LICENSE, IhkFetchError, fetch_gewerbedaten_bbox
from models import CANDIDATE_NODES, LocationInput

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
RAW_DIR = PROJECT_ROOT / "db" / "raw" / "ihk"


class IhkPipelineError(Exception):
    """Fatal error for a single area's IHK ingestion (caller decides whether to continue)."""


def _within_radius(node: LocationInput, feature: dict) -> bool:
    lon, lat = feature["geometry"]["coordinates"]
    return haversine_m(node.lat, node.lon, lat, lon) <= node.radius_m


def ingest_area(conn: sqlite3.Connection, node: LocationInput) -> dict:
    """Fetch, radius-cut, and persist one area's IHK business points.

    Raises IhkPipelineError if the WFS fetch fails -- the area gets no
    snapshot and no observations (never silently written with
    partial/fabricated data), same rule as poi_pipeline.py.
    """
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    try:
        raw_json = fetch_gewerbedaten_bbox(node.lat, node.lon, node.radius_m)
    except IhkFetchError as exc:
        raise IhkPipelineError(f"[{node.area_id}] IHK Gewerbedaten fetch failed: {exc}") from exc

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{node.area_id}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    raw_path.write_text(json.dumps(raw_json, ensure_ascii=False, indent=2))

    all_features = raw_json.get("features", [])
    within = [f for f in all_features if _within_radius(node, f)]

    cur = conn.execute(
        """
        INSERT INTO source_snapshots
            (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "ihk_gewerbedaten",
            # WFS abstract states the register is refreshed monthly;
            # the daten.berlin.de portal listing's own "Aktualisiert"
            # date is stale page metadata, not the live service -- so
            # this is treated the same as OSM: a live dataset, "as of"
            # = the date it was queried (see ihk_client.py).
            today,
            now.isoformat(),
            GEWERBEDATEN_LICENSE,
            str(raw_path.relative_to(PROJECT_ROOT)),
            f"radius_m={node.radius_m}; bbox_candidates={len(all_features)}; within_radius={len(within)}",
        ),
    )
    snapshot_id = cur.lastrowid

    for feature in within:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        conn.execute(
            """
            INSERT OR IGNORE INTO observations
                (snapshot_id, area_id, entity_type, external_ref, name, category,
                 lat, lon, opening_hours_raw, payload_json)
            VALUES (?, ?, 'business', ?, NULL, ?, ?, ?, NULL, ?)
            """,
            (
                snapshot_id,
                node.area_id,
                f"gewerbedaten/{props['opendata_id']}",
                props.get("branch_top_level_desc"),
                lat,
                lon,
                json.dumps(props, ensure_ascii=False),
            ),
        )

    conn.commit()
    return {
        "area_id": node.area_id,
        "snapshot_id": snapshot_id,
        "bbox_candidates": len(all_features),
        "within_radius": len(within),
    }


def run(db_path: Path = DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    results = []
    try:
        for node in CANDIDATE_NODES:
            try:
                result = ingest_area(conn, node)
                print(
                    f"[{result['area_id']}] snapshot #{result['snapshot_id']}: "
                    f"{result['bbox_candidates']} bbox candidates -> "
                    f"{result['within_radius']} within {node.radius_m}m"
                )
                results.append(result)
            except IhkPipelineError as exc:
                print(f"SKIPPED: {exc}")
                results.append({"area_id": node.area_id, "error": str(exc)})
    finally:
        conn.close()
    return results


if __name__ == "__main__":
    run()
