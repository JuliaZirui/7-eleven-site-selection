"""Competitor POI taxonomy, shared by every POI source.

Scope follows site_candidates.md's "Business Analysis Scope > Competition":
Spati/kiosk, supermarkets, gas-station convenience stores, and other
formats offering fresh/ready-to-eat food -- widened in v2 to beverage
stores (Getränkemarkt) and drugstores (Drogerie: dm, Rossmann), which
compete for the drinks / toiletries share of a convenience basket, and
to the small shop formats that only matter inside a station (tobacco,
food, confectionery counters on the concourse).

This is a competition-signal taxonomy, not a general POI classifier --
anything outside it (pure fuel stations with no shop, pharmacies,
clothing, ...) is left unclassified and dropped. Each source (OSM,
Overture, Foursquare) gets its own mapping into the same categories
here, so poi_conflation.py compares like with like.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum


class POICategory(str, Enum):
    SPATI_KIOSK = "spati_kiosk"  # convenience / kiosk / newsagent, not at a fuel station
    SUPERMARKET = "supermarket"
    BAKERY = "bakery"
    FAST_FOOD = "fast_food"
    CAFE = "cafe"
    GAS_STATION_CONVENIENCE = "gas_station_convenience"
    BEVERAGES = "beverages"  # Getränkemarkt / drinks & spirits shop
    CHEMIST = "chemist"  # Drogerie (dm, Rossmann) -- not Apotheke (amenity=pharmacy)
    OTHER_FRESH_FOOD = "other_fresh_food"  # deli; in-station food counters


# Formats that substitute for a 7-Eleven-style basket as a whole, not
# just its coffee or snack part. Shared by metrics, scoring, the report
# and the anchor calibration so all four mean the same thing.
DIRECT_FORMAT_CATEGORIES: tuple[str, ...] = (
    POICategory.SPATI_KIOSK.value,
    POICategory.SUPERMARKET.value,
    POICategory.GAS_STATION_CONVENIENCE.value,
)

# Competitor sets per commute peak (scoring_v4). Each peak counts only
# the formats a commuter would buy from in that window: breakfast and
# coffee in the morning, the evening top-up / snack basket after work.
# Supermarkets and fuel-station shops serve both. Chemists, beverage
# stores and delis are in neither -- they don't substitute for a
# 7-Eleven's peak-hour basket.
MORNING_PEAK_CATEGORIES: tuple[str, ...] = (
    POICategory.BAKERY.value,
    POICategory.CAFE.value,
    POICategory.SUPERMARKET.value,
    POICategory.GAS_STATION_CONVENIENCE.value,
)
EVENING_PEAK_CATEGORIES: tuple[str, ...] = (
    POICategory.SPATI_KIOSK.value,
    POICategory.FAST_FOOD.value,
    POICategory.SUPERMARKET.value,
    POICategory.GAS_STATION_CONVENIENCE.value,
)


# ---------------------------------------------------------------------------
# OSM
# ---------------------------------------------------------------------------

# Overpass tag filters for the area-wide query. Each entry is
# (osm_key, regex_of_values) fed into a `nwr["key"~"values"]` clause.
# amenity=fuel is queried broadly (not restricted to stations with a
# shop tag) because that restriction can only be applied after seeing
# each element's tags.
OVERPASS_TAG_FILTERS: tuple[tuple[str, str], ...] = (
    ("shop", "^(convenience|kiosk|newsagent|supermarket|bakery|pastry|deli|beverages|alcohol|chemist)$"),
    ("amenity", "^(fast_food|cafe|fuel)$"),
)

# Shop types that only count as competitors when they sit inside a
# station (concourse / platform level). A tobacco or confectionery
# counter on a U-Bahn platform sells the same impulse basket as a
# kiosk; the same shop on a residential street is a specialist.
STATION_ONLY_SHOP_FILTER = ("shop", "^(tobacco|food|confectionery|coffee)$")

# How close a POI must be to a station feature (station node, platform,
# station building) to count as "in the station". Berlin S/U stations
# are mapped as bare nodes plus platform ways, not station polygons, so
# a distance test is the only workable definition. 40m keeps
# concourse/platform shops and drops the next shop row down the street.
STATION_RADIUS_M = 40

# Station features queried to anchor the in-station distance test.
# public_transport=platform is only taken for rail modes -- bus stops
# are also tagged public_transport=platform and would drag every
# street-corner shop "into a station".
OVERPASS_STATION_FILTERS: tuple[str, ...] = (
    '["public_transport"="station"]',
    '["railway"~"^(station|halt|platform)$"]',
    '["public_transport"="platform"]["train"="yes"]',
    '["public_transport"="platform"]["subway"="yes"]',
    '["public_transport"="platform"]["light_rail"="yes"]',
    '["building"="train_station"]',
)

_OSM_SHOP_MAP = {
    "convenience": POICategory.SPATI_KIOSK,
    "kiosk": POICategory.SPATI_KIOSK,
    "newsagent": POICategory.SPATI_KIOSK,
    "supermarket": POICategory.SUPERMARKET,
    "bakery": POICategory.BAKERY,
    "pastry": POICategory.BAKERY,
    "deli": POICategory.OTHER_FRESH_FOOD,
    "beverages": POICategory.BEVERAGES,
    "alcohol": POICategory.BEVERAGES,
    "chemist": POICategory.CHEMIST,
}

_OSM_STATION_ONLY_SHOP_MAP = {
    "tobacco": POICategory.SPATI_KIOSK,
    "food": POICategory.OTHER_FRESH_FOOD,
    "confectionery": POICategory.OTHER_FRESH_FOOD,
    "coffee": POICategory.CAFE,
}


def is_station_feature(tags: dict[str, str]) -> bool:
    """True for the station/platform elements the in-station test is
    measured against (they are anchors, never competitors)."""
    if tags.get("public_transport") == "station" or tags.get("building") == "train_station":
        return True
    if tags.get("railway") in ("station", "halt", "platform"):
        return True
    if tags.get("public_transport") == "platform":
        return any(tags.get(mode) == "yes" for mode in ("train", "subway", "light_rail"))
    return False


def classify(tags: dict[str, str], in_station: bool = False) -> POICategory | None:
    """Map an OSM element's tags to a competitor category, or None if
    it falls outside the competition-analysis scope (e.g. a fuel
    station with no attached shop, or a tobacco shop off-station)."""
    shop = tags.get("shop")
    amenity = tags.get("amenity")

    # A fuel station only counts as a competitor if it has an attached
    # shop selling more than just fuel; otherwise it's out of scope.
    if amenity == "fuel":
        if shop in ("convenience", "kiosk", "supermarket"):
            return POICategory.GAS_STATION_CONVENIENCE
        return None

    if shop in _OSM_SHOP_MAP:
        return _OSM_SHOP_MAP[shop]
    if in_station and shop in _OSM_STATION_ONLY_SHOP_MAP:
        return _OSM_STATION_ONLY_SHOP_MAP[shop]
    if amenity == "fast_food":
        return POICategory.FAST_FOOD
    if amenity == "cafe":
        return POICategory.CAFE

    return None


# ---------------------------------------------------------------------------
# Overture Maps Places (taxonomy.hierarchy, most specific first)
# ---------------------------------------------------------------------------

# Leaf/intermediate Overture taxonomy ids -> competitor category. Walked
# from the most specific hierarchy entry upward, so an explicit leaf
# (doner_kebab_restaurant) wins over its generic parent (restaurant,
# which is deliberately unmapped: sit-down restaurants are out of scope).
OVERTURE_CATEGORY_MAP: dict[str, POICategory] = {
    "convenience_store": POICategory.SPATI_KIOSK,
    "kiosk": POICategory.SPATI_KIOSK,
    "newspaper_and_magazines_store": POICategory.SPATI_KIOSK,
    "supermarket": POICategory.SUPERMARKET,
    "grocery_store": POICategory.SUPERMARKET,
    "bakery": POICategory.BAKERY,
    "fast_food_restaurant": POICategory.FAST_FOOD,
    "sandwich_shop": POICategory.FAST_FOOD,
    "doner_kebab_restaurant": POICategory.FAST_FOOD,
    "cafe": POICategory.CAFE,
    "coffee_shop": POICategory.CAFE,
    # Overture carries no "has a shop" attribute for fuel stations; in
    # inner-city Berlin virtually every staffed station runs one, so
    # gas_station is mapped straight through (a stated assumption --
    # stricter than nothing, looser than the OSM shop-tag rule).
    "gas_station": POICategory.GAS_STATION_CONVENIENCE,
    "liquor_store": POICategory.BEVERAGES,
    "beer_wine_spirits_store": POICategory.BEVERAGES,
    "beverage_store": POICategory.BEVERAGES,
    "drugstore": POICategory.CHEMIST,
    "delicatessen": POICategory.OTHER_FRESH_FOOD,
}


def classify_overture(hierarchy: list[str] | None, primary: str | None = None) -> POICategory | None:
    path = list(hierarchy or [])
    if primary and primary not in path:
        path.append(primary)
    for level in reversed(path):
        if level in OVERTURE_CATEGORY_MAP:
            return OVERTURE_CATEGORY_MAP[level]
    return None


# ---------------------------------------------------------------------------
# Foursquare OS Places (fsq_category_labels, "Top > Mid > Leaf")
# ---------------------------------------------------------------------------

# Keyed on the normalized leaf label. Foursquare's taxonomy has shifted
# between releases (e.g. "Café" vs "Cafe", "Gas Station" vs "Fuel
# Station"), so both spellings are listed; unmapped leaves are counted
# in the snapshot notes by fsq_client so a taxonomy drift shows up
# instead of silently shrinking the competitor set.
FSQ_LEAF_MAP: dict[str, POICategory] = {
    "convenience store": POICategory.SPATI_KIOSK,
    "newsagent": POICategory.SPATI_KIOSK,
    "newsstand": POICategory.SPATI_KIOSK,
    "kiosk": POICategory.SPATI_KIOSK,
    "supermarket": POICategory.SUPERMARKET,
    "grocery store": POICategory.SUPERMARKET,
    "grocery store / supermarket": POICategory.SUPERMARKET,
    "organic grocery": POICategory.SUPERMARKET,
    "bakery": POICategory.BAKERY,
    "fast food restaurant": POICategory.FAST_FOOD,
    "sandwich spot": POICategory.FAST_FOOD,
    "sandwich restaurant": POICategory.FAST_FOOD,
    "doner restaurant": POICategory.FAST_FOOD,
    "kebab restaurant": POICategory.FAST_FOOD,
    "cafe": POICategory.CAFE,
    "coffee shop": POICategory.CAFE,
    "cafe, coffee, and tea house": POICategory.CAFE,
    "gas station": POICategory.GAS_STATION_CONVENIENCE,
    "fuel station": POICategory.GAS_STATION_CONVENIENCE,
    "liquor store": POICategory.BEVERAGES,
    "beer store": POICategory.BEVERAGES,
    "beverage store": POICategory.BEVERAGES,
    "drugstore": POICategory.CHEMIST,
    "deli": POICategory.OTHER_FRESH_FOOD,
    "delicatessen": POICategory.OTHER_FRESH_FOOD,
}


def normalize_label(label: str) -> str:
    """Lowercase, strip accents, collapse whitespace ("Café" -> "cafe")."""
    decomposed = unicodedata.normalize("NFKD", label)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def fsq_leaf(label: str) -> str:
    return normalize_label(label.split(">")[-1])


def classify_fsq(labels: list[str] | None) -> POICategory | None:
    """First label (Foursquare's primary category) that maps wins."""
    for label in labels or []:
        category = FSQ_LEAF_MAP.get(fsq_leaf(label))
        if category is not None:
            return category
    return None
