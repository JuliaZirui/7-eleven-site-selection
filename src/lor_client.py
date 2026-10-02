"""Berlin LOR (Lebensweltlich orientierte Räume) client: fetches the
current Planungsraum boundaries and per-Planungsraum population counts,
both via the Berlin Senate's public WFS services (gdi.berlin.de).

Source note: the Amt für Statistik's old static CSV export
(statistik-berlin-brandenburg.de/opendata/EWR_L21_*.csv, listed on
daten.berlin.de) now 404s into that site's SPA shell for every path —
the site was migrated to a new "MATS" data portal that, as of this
writing, only exposes population by district/municipality (Kreis/
Gemeinde), not by the finer 542-Planungsraum LOR geography this
project needs. The Wayback Machine's snapshots of the CSV URL are
likewise already the broken SPA shell, so there is no live or archived
source for the raw EWR register export at LOR granularity.

Population is instead sourced from the Senate's "Monitoring Soziale
Stadtentwicklung" (MSS) 2025 WFS layer, whose context indicators
include `ew` (Einwohnerzahl / population count) and `zeit` (as-of
period, YYYYMM) per Planungsraum — data as of 31.12.2024. This is an
official SenStadt publication and a reasonable v1 substitute, but it
is one step removed from the primary EWR register; if the migrated
opendata portal later exposes LOR-level EWR figures directly, prefer
those. This substitution is logged in source_snapshots.notes, not
silently swapped in.
"""

from __future__ import annotations

import httpx

LOR_BOUNDARIES_WFS_URL = (
    "https://gdi.berlin.de/services/wfs/lor_2021"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=lor_2021:a_lor_plr_2021&outputFormat=application/json&srsName=EPSG:4326"
)
LOR_BOUNDARIES_LICENSE = "CC BY 3.0 DE (Geoportal Berlin / SenStadt)"
LOR_BOUNDARIES_VERSION = "2021-01-01"  # "Stand" of the a_lor_plr_2021 layer (542 Planungsräume)

MSS_POPULATION_WFS_URL = (
    "https://gdi.berlin.de/services/wfs/mss_2025"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=mss_2025:mss2025_indexind_542&outputFormat=application/json"
)
MSS_POPULATION_LICENSE = "Datenlizenz Deutschland - Zero - Version 2.0 (SenStadt, MSS 2025)"

DEFAULT_TIMEOUT_S = 30


class LorFetchError(Exception):
    """Raised when a Berlin Senate WFS endpoint cannot be reached or returns bad data."""


def _fetch_wfs_geojson(url: str) -> dict:
    try:
        response = httpx.get(url, timeout=DEFAULT_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise LorFetchError(f"WFS request failed: {url} ({exc})") from exc
    if response.status_code != 200:
        raise LorFetchError(f"WFS returned {response.status_code}: {url}")
    data = response.json()
    if data.get("type") != "FeatureCollection" or not data.get("features"):
        raise LorFetchError(f"WFS response has no features: {url}")
    return data


def fetch_lor_boundaries() -> dict:
    """Fetch the 542-Planungsraum boundary polygons (EPSG:4326 GeoJSON)."""
    return _fetch_wfs_geojson(LOR_BOUNDARIES_WFS_URL)


def fetch_mss_population() -> dict:
    """Fetch per-Planungsraum population (`ew`) from the MSS 2025 context indicators."""
    return _fetch_wfs_geojson(MSS_POPULATION_WFS_URL)


def population_by_plr_id(mss_geojson: dict) -> dict[str, dict]:
    """Index the MSS population layer by plr_id -> {ew, zeit, plr_name}."""
    index: dict[str, dict] = {}
    for feature in mss_geojson["features"]:
        props = feature["properties"]
        plr_id = props.get("plr_id")
        if not plr_id:
            continue
        index[plr_id] = {
            "population": props.get("ew"),
            "as_of_period": props.get("zeit"),  # YYYYMM, e.g. 202412
            "plr_name": props.get("plr_name"),
        }
    return index
