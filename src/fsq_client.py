"""Foursquare OS Places client: gated Hugging Face dataset via DuckDB.

Since 2025 FSQ OS Places (Apache-2.0) is published as a *gated*
dataset on Hugging Face (huggingface.co/datasets/foursquare/fsq-os-places):
the files are free, but each user must log in, accept the terms on the
dataset page, and read it with their own access token. This client
expects that token in the HF_TOKEN environment variable and never
stores it.

The release is ~100 parquet files (~11 GB) that are not spatially
partitioned, so a per-area query would re-scan the world three times.
Instead the first run extracts one Berlin bounding box into a local
cache file (db/raw/fsq/fsq_os_places_berlin_<release>.parquet), and
every per-area query reads that cache. The cache is keyed by release
date, so a new release triggers a fresh extract rather than silently
reusing old data.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import httpx

FSQ_DATASET = "foursquare/fsq-os-places"
FSQ_LICENSE = "Apache-2.0 (Foursquare OS Places)"
HF_API = f"https://huggingface.co/api/datasets/{FSQ_DATASET}"
HTTP_TIMEOUT_S = 30

# Berlin city limits plus a margin; every candidate node lies inside.
BERLIN_BBOX = (13.05, 52.33, 13.78, 52.69)  # xmin, ymin, xmax, ymax


class FsqError(Exception):
    """Raised when FSQ OS Places can't be read (no token, no access, I/O)."""


def latest_release() -> str:
    """Release date (dt=YYYY-MM-DD) of the dataset's default 'places' config."""
    try:
        response = httpx.get(HF_API, timeout=HTTP_TIMEOUT_S)
        response.raise_for_status()
        for config in response.json()["cardData"]["configs"]:
            if config["config_name"] == "places":
                path = config["data_files"] if isinstance(config["data_files"], str) else config["data_files"][0]
                return path.split("dt=")[1].split("/")[0]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise FsqError(f"could not resolve the FSQ OS Places release: {exc}") from exc
    raise FsqError("FSQ OS Places dataset card lists no 'places' config")


def ensure_berlin_extract(cache_dir: Path, token: str | None = None) -> tuple[Path, str]:
    """Return (cache_path, release), building the Berlin extract if missing."""
    release = latest_release()
    cache_path = cache_dir / f"fsq_os_places_berlin_{release}.parquet"
    if cache_path.exists():
        return cache_path, release

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise FsqError(
            "HF_TOKEN is not set. FSQ OS Places is gated: log in to huggingface.co, accept the terms at "
            f"https://huggingface.co/datasets/{FSQ_DATASET}, create a read token, then `export HF_TOKEN=...`."
        )

    import duckdb

    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".parquet.partial")
    xmin, ymin, xmax, ymax = BERLIN_BBOX
    try:
        con = duckdb.connect()
        con.execute("SET enable_progress_bar = false")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("CREATE SECRET fsq_hf (TYPE huggingface, TOKEN ?)", [token])
        con.execute(
            f"""
            COPY (
                SELECT * FROM read_parquet('hf://datasets/{FSQ_DATASET}/release/dt={release}/places/parquet/*.parquet')
                WHERE latitude BETWEEN {ymin} AND {ymax} AND longitude BETWEEN {xmin} AND {xmax}
            ) TO '{tmp_path}' (FORMAT parquet)
            """
        )
    except duckdb.Error as exc:
        tmp_path.unlink(missing_ok=True)
        hint = " (401/403 usually means the dataset terms were not accepted for this token)" if "40" in str(exc) else ""
        raise FsqError(f"FSQ OS Places Berlin extract failed{hint}: {exc}") from exc
    tmp_path.rename(cache_path)
    return cache_path, release


def fetch_fsq_places(lat: float, lon: float, radius_m: int, cache_dir: Path) -> tuple[list[dict], dict]:
    """All FSQ places in the bbox around (lat, lon), read from the Berlin cache.

    Returns (rows, meta). Rows are unfiltered (closed/stale places
    included) and not yet category-mapped -- poi_pipeline applies those
    rules so the raw file keeps what Foursquare actually holds.
    """
    import duckdb

    cache_path, release = ensure_berlin_extract(cache_dir)
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    try:
        con = duckdb.connect()
        con.execute("SET enable_progress_bar = false")
        cursor = con.execute(
            """
            SELECT fsq_place_id, name, latitude AS lat, longitude AS lon,
                   CAST(date_refreshed AS VARCHAR) AS date_refreshed,
                   CAST(date_closed AS VARCHAR) AS date_closed,
                   fsq_category_labels
            FROM read_parquet(?)
            WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
            """,
            [str(cache_path), lat - dlat, lat + dlat, lon - dlon, lon + dlon],
        )
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    except duckdb.Error as exc:
        raise FsqError(f"FSQ cache query failed ({cache_path.name}): {exc}") from exc
    return rows, {"release": release, "cache_file": cache_path.name}
