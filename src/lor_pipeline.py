"""Day 2 pipeline: fetch LOR Planungsraum boundaries + MSS population,
match each candidate node's anchor coordinate to its Planungsraum, and
write source_snapshots + observations (plus areas.lor_code /
lor_boundary_version, per the schema's "Key Rules").

Run directly to process all CANDIDATE_NODES:
    python3 lor_pipeline.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Point, shape

from lor_client import (
    LOR_BOUNDARIES_LICENSE,
    LOR_BOUNDARIES_VERSION,
    MSS_POPULATION_LICENSE,
    LorFetchError,
    fetch_lor_boundaries,
    fetch_mss_population,
    population_by_plr_id,
)
from models import CANDIDATE_NODES, LocationInput

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
RAW_DIR = PROJECT_ROOT / "db" / "raw" / "lor"


class LorPipelineError(Exception):
    """Fatal error for the LOR ingestion run (boundaries/population unavailable)."""


def _match_plr(lat: float, lon: float, boundaries: dict) -> dict | None:
    """Point-in-polygon match against the 542 Planungsraum polygons.
    Returns the matched feature's properties, or None if no polygon
    contains the point (shouldn't happen for in-Berlin coordinates, but
    never silently guessed at)."""
    point = Point(lon, lat)  # GeoJSON order is (lon, lat)
    for feature in boundaries["features"]:
        polygon = shape(feature["geometry"])
        if polygon.contains(point):
            return feature["properties"]
    return None


def run(db_path: Path = DB_PATH) -> list[dict]:
    now = datetime.now(timezone.utc)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    try:
        boundaries = fetch_lor_boundaries()
        population_geojson = fetch_mss_population()
    except LorFetchError as exc:
        raise LorPipelineError(f"LOR fetch failed: {exc}") from exc

    boundaries_path = RAW_DIR / f"lor_planungsraeume_2021_{now.strftime('%Y%m%dT%H%M%SZ')}.geojson"
    boundaries_path.write_text(json.dumps(boundaries, ensure_ascii=False))
    population_path = RAW_DIR / f"mss2025_population_{now.strftime('%Y%m%dT%H%M%SZ')}.geojson"
    population_path.write_text(json.dumps(population_geojson, ensure_ascii=False))

    pop_index = population_by_plr_id(population_geojson)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    results = []
    try:
        boundaries_snapshot_id = conn.execute(
            """
            INSERT INTO source_snapshots
                (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "berlin_lor_boundaries",
                LOR_BOUNDARIES_VERSION,
                now.isoformat(),
                LOR_BOUNDARIES_LICENSE,
                str(boundaries_path.relative_to(PROJECT_ROOT)),
                f"542 Planungsraum polygons; endpoint=https://gdi.berlin.de/services/wfs/lor_2021",
            ),
        ).lastrowid

        # MSS population is a substitute for the (currently unreachable)
        # EWR register export — see lor_client.py module docstring.
        # data_as_of_date is read per-feature from `zeit` below via
        # pop_index, but the snapshot as a whole is dated to the MSS
        # 2025 edition's stated coverage end (31.12.2024).
        population_snapshot_id = conn.execute(
            """
            INSERT INTO source_snapshots
                (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "berlin_mss_population",
                "2024-12-31",
                now.isoformat(),
                MSS_POPULATION_LICENSE,
                str(population_path.relative_to(PROJECT_ROOT)),
                "Substitute for the unreachable EWR_L21 CSV export (statistik-berlin-brandenburg.de "
                "migrated its /opendata/ paths to a MATS portal that only exposes Kreis/Gemeinde-level "
                "population, not LOR-Planungsraum; Wayback snapshots of the old CSV URL are also the "
                "broken SPA shell). Sourced from MSS 2025 context indicators (`ew` field), "
                "endpoint=https://gdi.berlin.de/services/wfs/mss_2025",
            ),
        ).lastrowid

        for node in CANDIDATE_NODES:
            result = _ingest_node(conn, node, boundaries, pop_index, population_snapshot_id)
            print(
                f"[{result['area_id']}] LOR {result.get('plr_id', 'NO MATCH')} "
                f"({result.get('plr_name', '?')}): population={result.get('population', 'MISSING')}"
            )
            results.append(result)

        conn.commit()
    finally:
        conn.close()
    return results


def _ingest_node(
    conn: sqlite3.Connection,
    node: LocationInput,
    boundaries: dict,
    pop_index: dict[str, dict],
    population_snapshot_id: int,
) -> dict:
    match = _match_plr(node.lat, node.lon, boundaries)
    if match is None:
        # Never guess — record the gap instead of fabricating a match.
        return {"area_id": node.area_id, "error": "anchor coordinate matched no LOR polygon"}

    plr_id = match["plr_id"]
    plr_name = match["plr_name"]

    conn.execute(
        "UPDATE areas SET lor_code = ?, lor_boundary_version = ? WHERE area_id = ?",
        (plr_id, LOR_BOUNDARIES_VERSION, node.area_id),
    )

    pop_record = pop_index.get(plr_id)
    if pop_record is None or pop_record["population"] is None:
        return {
            "area_id": node.area_id,
            "plr_id": plr_id,
            "plr_name": plr_name,
            "population": None,
            "note": "matched LOR but no population figure in MSS 2025 layer (never defaulted to 0)",
        }

    conn.execute(
        """
        INSERT INTO observations
            (snapshot_id, area_id, entity_type, external_ref, name, category,
             lat, lon, opening_hours_raw, payload_json)
        VALUES (?, ?, 'lor_population', ?, ?, 'population', ?, ?, NULL, ?)
        """,
        (
            population_snapshot_id,
            node.area_id,
            f"plr/{plr_id}",
            plr_name,
            node.lat,
            node.lon,
            json.dumps(
                {
                    "plr_id": plr_id,
                    "plr_name": plr_name,
                    "population": pop_record["population"],
                    "as_of_period": pop_record["as_of_period"],
                    "lor_boundary_version": LOR_BOUNDARIES_VERSION,
                },
                ensure_ascii=False,
            ),
        ),
    )

    return {
        "area_id": node.area_id,
        "plr_id": plr_id,
        "plr_name": plr_name,
        "population": pop_record["population"],
        "as_of_period": pop_record["as_of_period"],
    }


if __name__ == "__main__":
    run()
