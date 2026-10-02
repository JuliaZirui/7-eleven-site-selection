"""Berlin IHK "Gewerbedaten" client: fetches individual, anonymized
business-registration points (IHK Berlin's active-member register) via
the Berlin Senate's public WFS service (gdi.berlin.de).

Source note: this is the Day 4 (if-time-allows) "IHK or VBB data
ingestion" item from project_plan_v3.md's 7-day plan. IHK was chosen
over VBB: VBB's open data is GTFS schedule/route data, not ridership
or foot-traffic figures, so ingesting it would mostly re-derive a
transit-magnitude signal Day 1 already settled via DB's Preisklasse
grading (see site_candidates.md's "Data Verification Log"), without a
materially different metric. IHK Gewerbedaten instead adds a genuinely
new signal -- registered-business density and branch composition
within each node's 400m radius -- that can corroborate or challenge
the customer-profile hypotheses in site_candidates.md (e.g. "office +
tourist mix" for Warschauer Straße) with real evidence instead of only
the qualitative rationale used to build the node shortlist.

License: Datenlizenz Deutschland - Zero - Version 2.0 (dl-de-zero-2.0)
-- public-domain-equivalent, no attribution required (unlike the CC BY
licenses on the OSM/MSS sources already in this project).

The WFS's DWITHIN spatial filter returned zero matches when tested
against this endpoint at multiple radii and both coordinate orders,
while a plain BBOX filter against the same layer worked immediately --
most likely because the layer's underlying geometry column is stored
in EPSG:25833 and this GeoServer instance doesn't reproject a
degree/meter DWITHIN distance against it. This client works around
that the same way poi_pipeline.py already handles Overpass-vs-WFS
asymmetry: fetch a padded bounding box here, and let the caller cut to
the exact radius_m circle with geo.haversine_m.
"""

from __future__ import annotations

import math

import httpx

GEWERBEDATEN_WFS_URL = "https://gdi.berlin.de/services/wfs/gewerbedaten"
GEWERBEDATEN_LICENSE = "Datenlizenz Deutschland - Zero - Version 2.0 (dl-de-zero-2.0)"
DEFAULT_TIMEOUT_S = 30

# Padding applied to the radius before converting to a bounding box, so
# the box is a guaranteed superset of the exact circle for the caller
# to cut down with haversine_m -- not a precision knob.
BBOX_MARGIN = 1.3


class IhkFetchError(Exception):
    """Raised when the IHK Gewerbedaten WFS cannot be reached or returns bad data."""


def _bbox_for_radius(
    lat: float, lon: float, radius_m: float, margin: float = BBOX_MARGIN
) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) padded box guaranteed to
    contain the full radius_m circle around (lat, lon)."""
    lat_delta = (radius_m * margin) / 111_320.0
    lon_delta = (radius_m * margin) / (111_320.0 * math.cos(math.radians(lat)))
    return (lon - lon_delta, lat - lat_delta, lon + lon_delta, lat + lat_delta)


def fetch_gewerbedaten_bbox(lat: float, lon: float, radius_m: float) -> dict:
    """Fetch the padded-bbox GeoJSON superset of business points around
    (lat, lon). Callers must cut to the exact radius_m circle
    themselves -- see module docstring for why the WFS can't do this
    filtering itself."""
    min_lon, min_lat, max_lon, max_lat = _bbox_for_radius(lat, lon, radius_m)
    url = (
        f"{GEWERBEDATEN_WFS_URL}?service=WFS&version=2.0.0&request=GetFeature"
        f"&typeNames=gewerbedaten:gewerbedaten&outputFormat=application/json"
        f"&srsName=EPSG:4326&bbox={min_lon},{min_lat},{max_lon},{max_lat},EPSG:4326"
    )
    try:
        response = httpx.get(url, timeout=DEFAULT_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise IhkFetchError(f"WFS request failed: {url} ({exc})") from exc
    if response.status_code != 200:
        raise IhkFetchError(f"WFS returned {response.status_code}: {url}")
    data = response.json()
    if data.get("type") != "FeatureCollection":
        raise IhkFetchError(f"WFS response is not a FeatureCollection: {url}")
    return data
