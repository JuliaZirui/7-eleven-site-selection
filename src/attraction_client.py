"""HTTP clients for the visitor-draw signal: notable attractions from
Overpass (with full geometry, so large/linear sites like East Side
Gallery or Großer Tiergarten are measured to their nearest edge), and
annual Wikipedia pageviews as a uniform popularity weight.

Kept thin like osm_client.py -- query construction and HTTP I/O only.
The gateway rule and day/night split live in attraction_pipeline.py.
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import date

import httpx

from osm_client import HTTP_HEADERS, MIN_BUILDINGS_CANARY, OVERPASS_URLS, building_count

# Candidate attractions are searched this far out; whether one counts
# for a node is decided later by the gateway rule, not by this radius.
SEARCH_RADIUS_M = 2000
DEFAULT_TIMEOUT_S = 150
MAX_ATTEMPTS = 5
# This query is ~5x heavier than the competitor query; public mirrors
# answer it but regularly 504 first, so back off harder than
# osm_client's 2s.
RETRY_BACKOFF_S = 15.0

# Only elements with a wikidata tag are candidates -- the notability
# filter that drops plaques, small memorial stones and unnamed parks.
OVERPASS_ATTRACTION_FILTERS: tuple[str, ...] = (
    '["tourism"~"^(attraction|museum|gallery|zoo|theme_park|viewpoint)$"]',
    '["historic"~"^(monument|memorial|city_gate|castle|palace)$"]',
    '["amenity"~"^(theatre|arts_centre|events_venue|concert_hall|music_venue|nightclub|planetarium)$"]',
    '["leisure"~"^(stadium|park|sports_centre)$"]',
)

WIKIMEDIA_UA = {"User-Agent": "7eleven-site-selection-research/0.1 (Berlin site-selection prototype)"}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
PAGEVIEW_SITES = {"dewiki": "de.wikipedia", "enwiki": "en.wikipedia"}
# The per-article endpoint rate-limits bursts with 429 + Retry-After.
PAGEVIEW_PAUSE_S = 1.2
PAGEVIEW_MAX_429 = 8


class AttractionFetchError(Exception):
    """Raised when Overpass or the Wikimedia APIs fail after retries."""


def build_attraction_query(lat: float, lon: float, radius_m: int, timeout_s: int = DEFAULT_TIMEOUT_S) -> str:
    search = f"(around:{SEARCH_RADIUS_M},{lat},{lon})"
    clauses = "\n  ".join(f'nwr["wikidata"]{f}{search};' for f in OVERPASS_ATTRACTION_FILTERS)
    return (
        f"[out:json][timeout:{timeout_s}];\n"
        f"(\n  {clauses}\n);\n"
        # Full geometry, including relation members (not `out geom tags`,
        # which drops members and leaves relations with no shape at all).
        f"out geom;\n"
        f'way["building"](around:{radius_m},{lat},{lon});\n'
        f"out count;"
    )


def fetch_attractions(lat: float, lon: float, radius_m: int) -> tuple[dict, str]:
    """Raw Overpass JSON of notable attractions within SEARCH_RADIUS_M.

    Same mirror order and building-count canary as osm_client; returns
    (raw_json, source_url)."""
    query = build_attraction_query(lat, lon, radius_m)
    errors: list[str] = []
    for url in OVERPASS_URLS:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(url, data={"data": query}, headers=HTTP_HEADERS, timeout=DEFAULT_TIMEOUT_S + 10)
                if response.status_code == 200:
                    try:
                        raw_json = response.json()
                    except ValueError:
                        errors.append(f"{url} -> 200 with a non-JSON body")
                        break
                    buildings = building_count(raw_json)
                    if buildings is None or buildings < MIN_BUILDINGS_CANARY:
                        errors.append(f"{url} -> incomplete dataset: {buildings} buildings in radius")
                        break
                    return raw_json, url
                errors.append(f"{url} -> {response.status_code} (attempt {attempt}/{MAX_ATTEMPTS})")
            except httpx.HTTPError as exc:
                errors.append(f"{url} -> {type(exc).__name__} (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_S * attempt)
    raise AttractionFetchError("All Overpass endpoints failed:\n" + "\n".join(errors))


def fetch_sitelinks(wikidata_ids: list[str]) -> dict[str, dict[str, str]]:
    """{Qid: {"dewiki": title, "enwiki": title}} -- only sites that exist."""
    out: dict[str, dict[str, str]] = {}
    for i in range(0, len(wikidata_ids), 50):
        try:
            for attempt in range(PAGEVIEW_MAX_429):
                response = httpx.get(
                    WIKIDATA_API,
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(wikidata_ids[i : i + 50]),
                        "props": "sitelinks",
                        "sitefilter": "|".join(PAGEVIEW_SITES),
                        "format": "json",
                    },
                    headers=WIKIMEDIA_UA,
                    timeout=60,
                )
                if response.status_code != 429:
                    break
                time.sleep(int(response.headers.get("retry-after") or 5 * (attempt + 1)) + 1)
            response.raise_for_status()
            time.sleep(PAGEVIEW_PAUSE_S)
        except httpx.HTTPError as exc:
            raise AttractionFetchError(f"Wikidata sitelinks fetch failed: {exc}") from exc
        for qid, entity in response.json().get("entities", {}).items():
            out[qid] = {site: link["title"] for site, link in entity.get("sitelinks", {}).items()}
    return out


def pageview_window(today: date) -> tuple[date, date]:
    """The last 12 complete calendar months before `today`."""
    end = date(today.year, today.month, 1)
    start = date(end.year - 1, end.month, 1)
    return start, end


def fetch_pageviews(site: str, title: str, start: date, end: date) -> int:
    """Total user (non-bot) pageviews over [start, end). A 404 means the
    API has no data for the article in that window -- a genuine 0."""
    article = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = (
        f"{PAGEVIEWS_API}/{PAGEVIEW_SITES[site]}/all-access/user/{article}/monthly/"
        f"{start.strftime('%Y%m%d')}00/{end.strftime('%Y%m%d')}00"
    )
    for _ in range(PAGEVIEW_MAX_429):
        try:
            response = httpx.get(url, headers=WIKIMEDIA_UA, timeout=60)
        except httpx.HTTPError as exc:
            raise AttractionFetchError(f"pageviews {site}:{title} failed: {exc}") from exc
        if response.status_code != 429:
            break
        time.sleep(int(response.headers.get("retry-after") or 5) + 1)
    time.sleep(PAGEVIEW_PAUSE_S)
    if response.status_code == 404:
        return 0
    if response.status_code != 200:
        raise AttractionFetchError(f"pageviews {site}:{title} -> {response.status_code}")
    # The end month is exclusive: the API treats `end` as inclusive of
    # that month's timestamp, so drop it explicitly.
    stop = end.strftime("%Y%m%d") + "00"
    return sum(item["views"] for item in response.json().get("items", []) if item["timestamp"] < stop)
