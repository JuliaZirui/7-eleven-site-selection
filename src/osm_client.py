"""Overpass API client: builds the competitor-POI query and fetches it.

Kept deliberately thin — query construction and HTTP I/O only. Cleaning,
dedup, and classification live in poi_pipeline.py / poi_taxonomy.py so
each concern can be unit-tested (and failure-tested) independently.
"""

from __future__ import annotations

import time

import httpx

from poi_taxonomy import (
    OVERPASS_STATION_FILTERS,
    OVERPASS_TAG_FILTERS,
    STATION_ONLY_SHOP_FILTER,
    STATION_RADIUS_M,
)

# Endpoint order matters more than it looks. overpass-api.de, the main
# instance, answers 406 to httpx's default User-Agent (verified
# 2026-09-30: same query, 406 with "python-httpx/...", 200 with the
# identifying agent below). Earlier runs read that 406 as an IP block
# and routed everything through maps.mail.ru; with HTTP_HEADERS sent,
# the main instance comes first again and mail.ru is the fallback.
# overpass.atownsend.org.uk answers, but its database is badly
# incomplete: on 2026-09-28 it held 0-1 buildings and 5 shop/amenity
# nodes inside each 400m circle, where maps.mail.ru held 167-2,350
# buildings -- the same competitor query returned 2 vs 22 elements at
# Brandenburger Tor and 12 vs 92 at Schönhauser Allee. That mirror, not
# OSM itself, was why the v1 snapshots looked so thin. The completeness
# canary below now rejects it automatically; it stays last in the list
# only as a documented dead end.
OVERPASS_URLS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.atownsend.org.uk/api/interpreter",
)

# Overpass's usage policy asks clients to identify themselves; the main
# instance rejects generic library agents outright. Every Overpass call
# in the project (competitors, attractions, calibration) sends this.
HTTP_HEADERS = {"User-Agent": "berlin-site-selection-prototype/0.2 (research prototype; Overpass read-only queries)"}
DEFAULT_TIMEOUT_S = 60
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 2.0

# Completeness canary: every query also counts building ways in the
# same circle. Any inner-Berlin 400m circle holds well over a hundred
# (167 at the sparsest candidate node); a mirror reporting fewer than
# this is serving a partial extract, and its "few competitors" answer
# would be read as a genuine market feature. Rejected like an outage.
MIN_BUILDINGS_CANARY = 25


class OverpassError(Exception):
    """Raised when the Overpass API cannot be reached or fails after retries."""


def build_overpass_query(lat: float, lon: float, radius_m: int, timeout_s: int = DEFAULT_TIMEOUT_S) -> str:
    around = f"(around:{radius_m},{lat},{lon})"
    poi_clauses = "\n  ".join(f'nwr["{key}"~"{pattern}"]{around};' for key, pattern in OVERPASS_TAG_FILTERS)
    station_clauses = "\n  ".join(f"nwr{f}{around};" for f in OVERPASS_STATION_FILTERS)
    key, pattern = STATION_ONLY_SHOP_FILTER
    return (
        f"[out:json][timeout:{timeout_s}];\n"
        f"(\n  {station_clauses}\n)->.stations;\n"
        f"(\n  {poi_clauses}\n"
        f'  nwr(around.stations:{STATION_RADIUS_M})["{key}"~"{pattern}"];\n'
        f")->.pois;\n"
        f".pois out center tags;\n"
        # Station anchors with full geometry: platforms are ways, and
        # the in-station test measures distance to the line, not its center.
        f".stations out geom;\n"
        f'way["building"]{around};\n'
        f"out count;"
    )


def building_count(raw_json: dict) -> int | None:
    """The canary's `out count` element, or None if the response has none."""
    for element in raw_json.get("elements", []):
        if element.get("type") == "count":
            return int(element.get("tags", {}).get("ways", 0))
    return None


def fetch_overpass_pois(lat: float, lon: float, radius_m: int) -> tuple[dict, str]:
    """Fetch raw Overpass JSON for competitor POIs around (lat, lon).

    Tries each URL in OVERPASS_URLS in order, retrying transient failures
    (timeout, 429/5xx, connection errors) per URL with linear backoff
    before falling through to the next mirror. Raises OverpassError only
    if every URL fails — callers (poi_pipeline) decide whether that's
    fatal for a given area or just means that area's snapshot is
    skipped/flagged (this is the "API failure" fixed test case).

    A 200 response that fails the building-count canary (or isn't JSON)
    is an incomplete/broken mirror, not a transient error: it is not
    retried, the next mirror is tried instead.

    Returns (raw_json, source_url) — source_url is recorded in
    source_snapshots.notes for traceability.
    """
    query = build_overpass_query(lat, lon, radius_m)
    errors: list[str] = []

    for url in OVERPASS_URLS:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    url,
                    data={"data": query},
                    headers=HTTP_HEADERS,
                    timeout=DEFAULT_TIMEOUT_S + 5,
                )
                if response.status_code == 200:
                    try:
                        raw_json = response.json()
                    except ValueError:
                        errors.append(f"{url} -> 200 with a non-JSON body (attempt {attempt}/{MAX_ATTEMPTS})")
                        break
                    buildings = building_count(raw_json)
                    if buildings is None or buildings < MIN_BUILDINGS_CANARY:
                        errors.append(
                            f"{url} -> incomplete dataset: {buildings} buildings in radius "
                            f"(canary minimum {MIN_BUILDINGS_CANARY})"
                        )
                        break
                    return raw_json, url
                errors.append(f"{url} -> {response.status_code} (attempt {attempt}/{MAX_ATTEMPTS})")
            except httpx.TimeoutException as exc:
                errors.append(f"{url} -> timeout (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
            except httpx.HTTPError as exc:
                errors.append(f"{url} -> connection error (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")

            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_S * attempt)

    raise OverpassError("All Overpass endpoints failed:\n" + "\n".join(errors))
