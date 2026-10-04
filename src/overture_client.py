"""Overture Maps Places client: bbox extract of one release via DuckDB.

Overture publishes each monthly release as GeoParquet on a public S3
bucket (no key needed), partitioned into ~16 spatially sorted files.
The release's STAC catalog lists every file with its bbox, so a query
touches only the one or two files that cover Berlin and DuckDB's
row-group pruning on the `bbox` column reads a few MB of each -- about
10s per 400m circle, versus scanning ~80M places worldwide.

Like osm_client.py this is I/O only: category mapping, quality filters
and dedup live in poi_pipeline.py / poi_taxonomy.py / poi_conflation.py.
Overture's own `sources` column is kept per record because Overture is
itself a conflation of Meta, Microsoft, Foursquare, AllThePlaces and
BrightQuery -- poi_conflation.py uses it to count independent upstream
providers rather than treating "Overture" as one vote.
"""

from __future__ import annotations

import math

import httpx

from poi_taxonomy import OVERTURE_CATEGORY_MAP

STAC_ROOT = "https://stac.overturemaps.org/catalog.json"
OVERTURE_LICENSE = "CDLA-Permissive-2.0 / ODbL (per-source; see docs.overturemaps.org/attribution)"
HTTP_TIMEOUT_S = 30
PREFERRED_ASSET = "aws"


class OvertureError(Exception):
    """Raised when the Overture release or its place files can't be read."""


def _bbox(lat: float, lon: float, radius_m: int) -> tuple[float, float, float, float]:
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def _get_json(url: str) -> dict:
    response = httpx.get(url, timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def resolve_place_files(xmin: float, ymin: float, xmax: float, ymax: float,
                        release: str | None = None) -> tuple[str, list[str]]:
    """(release_id, parquet URLs whose bbox intersects the query bbox)
    for the given Overture release (default: the latest), read from its
    STAC catalog."""
    try:
        release = release or _get_json(STAC_ROOT)["latest"]
        collection_url = f"https://stac.overturemaps.org/{release}/places/place/collection.json"
        collection = _get_json(collection_url)
        item_urls = [link["href"] for link in collection["links"] if link.get("rel") == "item"]
        files = []
        for item_url in item_urls:
            item = _get_json(item_url)
            bxmin, bymin, bxmax, bymax = item["bbox"]
            if bxmin <= xmax and bxmax >= xmin and bymin <= ymax and bymax >= ymin:
                # Each item lists the same file once per cloud mirror
                # (aws, azure); reading both returns every row twice.
                assets = item["assets"]
                asset = assets.get(PREFERRED_ASSET) or next(iter(assets.values()))
                files.append(asset["href"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise OvertureError(f"Overture STAC lookup failed: {exc}") from exc
    if not files:
        raise OvertureError(f"no Overture place file covers bbox {(xmin, ymin, xmax, ymax)} in release {release}")
    return release, files


def fetch_overture_places(lat: float, lon: float, radius_m: int, release: str | None = None) -> tuple[list[dict], dict]:
    """All in-scope-category Overture places in the bbox around (lat, lon).

    Returns (rows, meta). Rows are unfiltered by confidence/status and
    not yet cut to the circle -- poi_pipeline applies those rules so the
    raw file keeps what Overture actually returned. meta carries the
    release id and file URLs for source_snapshots.notes.
    """
    import duckdb  # local import: only this source needs it

    xmin, ymin, xmax, ymax = _bbox(lat, lon, radius_m)
    release, files = resolve_place_files(xmin, ymin, xmax, ymax, release)
    try:
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs;")
        cursor = con.execute(
            """
            SELECT id,
                   names."primary" AS name,
                   taxonomy."primary" AS category_primary,
                   taxonomy.hierarchy AS category_hierarchy,
                   confidence,
                   operating_status,
                   brand.names."primary" AS brand,
                   list_distinct([s.dataset FOR s IN sources]) AS upstream_datasets,
                   (bbox.ymin + bbox.ymax) / 2 AS lat,
                   (bbox.xmin + bbox.xmax) / 2 AS lon
            FROM read_parquet(?)
            WHERE bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ?
              AND list_has_any(taxonomy.hierarchy, ?)
            """,
            [files, xmax, xmin, ymax, ymin, sorted(OVERTURE_CATEGORY_MAP)],
        )
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    except duckdb.Error as exc:
        raise OvertureError(f"Overture parquet query failed: {exc}") from exc
    return rows, {"release": release, "files": files, "bbox": [xmin, ymin, xmax, ymax]}
