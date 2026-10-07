"""Candidate generator for the data-plausibility audit (plan_llm_data_audit.md §2).

Code decides *what* gets audited, so the LLM auditor's cost is bounded
and every run sees the same worklist. Everything here is deterministic
and runs on the pipeline's raw, pre-review state: registrations at their
size-class midpoint, no hand-added employers, every visitor figure from
the fame conversion, competitors conflated without the conflation_v2
quality rules or the verified POI corrections. The correction tables are
what the audit is measured against, so they must not shape its input.

Five modules, one per issue type:

  - concentration:      one record carrying >= 25% of a node's worker or
                        visitor segment, or >= 1,000 people/day;
  - large_registration: every IHK registration with size-class floor >= 500,
                        with the OSM names within 40 m (IHK data are
                        anonymised; the names are the identification hint);
  - missing_entity:     OSM office / government / embassy / company
                        objects in the circle with no IHK registration
                        within 40 m, and buildings of >= 5,000 m² floor
                        area whose IHK headcount within 40 m is below one
                        person per 100 m² (part (a); part (b) is the LLM's
                        own list, Phase 2);
  - poi_category:       competitors whose name carries a non-food word, or
                        whose provider category disagrees with a same-name
                        OSM object within 15 m;
  - poi_duplicate:      two competitors of one category within 15 m that
                        conflation kept apart.

Every candidate has a stable candidate_id (module:record ref). Thresholds
are the plan's, fixed before any audit code existed, except where a rule
was changed on the development node -- each change is logged in
comparison/audit_candidate_recall.md.

Inputs: the candidate sites from db/site_selection.db (read only); the
reference stations (generalisation set) from the calibration caches plus
one IHK WFS call each. Both need an OSM context snapshot per node --
named objects and building footprints in the circle, which the
competitor snapshots do not hold -- fetched once with --fetch and stored
under db/raw/audit/osm_context/.

    python3 src/audit_candidates.py [--fetch]

writes comparison/audit_runs/worklists/<area>.json per node.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from demand_model import annual_visitors, employees_estimate, latest_visitor_fit
from geo import haversine_m
from models import LocationInput

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
AUDIT_RAW_DIR = PROJECT_ROOT / "db" / "raw" / "audit"
OSM_CONTEXT_DIR = AUDIT_RAW_DIR / "osm_context"
IHK_DIR = AUDIT_RAW_DIR / "ihk"
WORKLIST_DIR = PROJECT_ROOT / "comparison" / "audit_runs" / "worklists"

GENERATOR_VERSION = "audit_candidates_v2"

# Thresholds from the plan (§2), set before any audit code existed.
CONCENTRATION_SHARE = 0.25
CONCENTRATION_PEOPLE_PER_DAY = 1_000.0
LARGE_REGISTRATION_FLOOR = 500
NAME_HINT_M = 40.0
MISSING_ENTITY_NO_IHK_M = 40.0
MISSING_ENTITY_FLOOR_AREA_M2 = 5_000.0
# v2 (development node): a large building qualifies when the IHK headcount
# registered within MISSING_ENTITY_NO_IHK_M of it is below one person per
# this many m² of floor area. Offices hold one workplace per 15-25 m², so
# this flags buildings with under ~20% of a plausible workforce on
# record. v1 required *no* registration within 40 m, which a tower with a
# few small firms at its foot never meets.
MISSING_ENTITY_M2_PER_REGISTERED = 100.0
# Large buildings that house people, cars or worship, not workplaces.
NON_WORKPLACE_BUILDINGS = frozenset(
    "residential apartments house detached semidetached_house terrace dormitory garage garages parking "
    "carport roof construction church cathedral".split()
)
POI_SAME_PLACE_M = 15.0
# The OSM context reaches this far past the circle, so a registration on
# the edge still gets the names within NAME_HINT_M around it.
OSM_CONTEXT_MARGIN_M = 50
# At most this many OSM names attached to one registration, nearest first.
MAX_NAME_HINTS = 12

# Words that say a place is not a food shop, or is a full-service
# restaurant rather than a café / snack counter. Matched as whole words,
# case-insensitive. Generic by design: a reviewed list of types, never a
# name of a place (tests/test_audit_prompt.py checks it against the
# answer key). A hit makes a record a candidate, nothing more -- the
# auditor and then a person decide.
NON_FOOD_NAME_WORDS = frozenset(
    """steakhouse steakhaus restaurant ristorante trattoria osteria brasserie hotel hostel
    haus hof bank büro office tower center centre galerie kanzlei praxis botschaft
    ministerium verwaltung apotheke friseur""".split()
)

# OSM tags marking a workplace that IHK may not register (public sector,
# embassies, companies filed elsewhere).
WORKPLACE_KEYS = ("office", "government", "diplomatic", "company")
WORKPLACE_BUILDING_VALUES = frozenset({"office", "government", "embassy", "public"})

# Named elements of these kinds are streets, routes and areas, not
# places that could be a workplace or a shop.
_NOT_A_PLACE_KEYS = ("highway", "railway", "route", "boundary", "public_transport", "waterway", "landuse",
                     "natural", "place", "power")


# --------------------------------------------------------------------------
# Node context
# --------------------------------------------------------------------------


@dataclass
class NodeContext:
    area_id: str
    name: str
    lat: float
    lon: float
    radius_m: float
    registrations: list[dict]  # IHK properties + lat/lon, in the circle
    visitor_records: list[dict]  # attraction gateway records (pageviews, share, titles)
    competitors: list[dict]  # raw conflated set (conflation_v1, no corrections)
    osm_elements: list[dict] | None  # OSM context snapshot, None if not fetched
    uses_this_station: Callable[[dict], bool] | None = None
    provenance: dict = field(default_factory=dict)


class _Projector:
    """Local equirectangular metres around the node (as catchment_population)."""

    def __init__(self, lat: float, lon: float):
        self.lat0, self.lon0 = lat, lon
        self.kx = 111_320.0 * math.cos(math.radians(lat))
        self.ky = 110_540.0

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky


def _geometry(element: dict, proj: _Projector):
    """Shapely geometry of an Overpass element (out geom) in local metres:
    a point for nodes, a polygon for closed ways and multipolygon
    relations, a line otherwise. None if it has no coordinates."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import polygonize, unary_union

    if element["type"] == "node":
        return Point(proj.xy(element["lat"], element["lon"]))
    if element["type"] == "way":
        pts = [proj.xy(p["lat"], p["lon"]) for p in element.get("geometry") or [] if p]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            poly = Polygon(pts)
            return poly if poly.is_valid else poly.buffer(0)
        return LineString(pts) if len(pts) >= 2 else (Point(pts[0]) if pts else None)
    lines = []
    for m in element.get("members", []):
        pts = [proj.xy(p["lat"], p["lon"]) for p in m.get("geometry") or [] if p]
        if m.get("role", "outer") in ("outer", "") and len(pts) >= 2:
            lines.append(LineString(pts))
    polys = list(polygonize(unary_union(lines))) if lines else []
    if polys:
        return unary_union(polys)
    return unary_union(lines) if lines else None


@dataclass
class _Place:
    element: dict
    geom: object
    tags: dict

    @property
    def ref(self) -> str:
        return f"{self.element['type']}/{self.element['id']}"


def _places(ctx: NodeContext, proj: _Projector) -> list[_Place]:
    out = []
    for e in ctx.osm_elements or []:
        tags = e.get("tags") or {}
        if not tags or any(k in tags for k in _NOT_A_PLACE_KEYS) and "building" not in tags:
            continue
        g = _geometry(e, proj)
        if g is not None and not g.is_empty:
            out.append(_Place(e, g, tags))
    return out


def _floor_area(place: _Place) -> tuple[float | None, float | None, bool]:
    """(footprint m², floor area m², levels tagged). Floor area is the
    footprint times building:levels; untagged levels count as one, so the
    figure is a lower bound there."""
    if "building" not in place.tags or place.geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None, None, False
    footprint = place.geom.area
    try:
        levels = float(str(place.tags.get("building:levels", "")).split(";")[0])
    except ValueError:
        levels = None
    return footprint, footprint * (levels or 1.0), levels is not None


def _address(tags: dict) -> str | None:
    street, number = tags.get("addr:street"), tags.get("addr:housenumber")
    return f"{street} {number}" if street and number else street or None


def _hint(place: _Place, distance_m: float) -> dict:
    keys = ("name", "office", "company", "operator", "government", "diplomatic", "amenity", "shop",
            "building", "building:levels")
    return {"osm_ref": place.ref, "distance_m": round(distance_m, 1),
            **{k: place.tags[k] for k in keys if k in place.tags}, "address": _address(place.tags)}


# --------------------------------------------------------------------------
# Modules
# --------------------------------------------------------------------------


def _reg_ref(reg: dict) -> str:
    return f"ihk:{reg.get('opendata_id')}"


def _reg_summary(reg: dict) -> dict:
    keys = ("opendata_id", "employees_range", "nace_id", "nace_desc", "ihk_branch_desc", "branch_top_level_desc",
            "business_age", "business_type", "planungsraum", "lat", "lon")
    return {k: reg.get(k) for k in keys}


def concentration(ctx: NodeContext, fit: dict) -> list[dict]:
    """Single records carrying a large part of the worker or visitor segment."""
    out = []
    counted = [(reg, employees_estimate(reg.get("employees_range"))) for reg in ctx.registrations
               if ctx.uses_this_station is None or ctx.uses_this_station(reg)]
    counted = [(reg, v) for reg, v in counted if v]
    worker_total = sum(v for _, v in counted)
    for reg, v in counted:
        if v >= CONCENTRATION_PEOPLE_PER_DAY or v >= CONCENTRATION_SHARE * worker_total:
            out.append({
                "candidate_id": f"concentration:{_reg_ref(reg)}",
                "module": "concentration", "segment": "workers", "record_ref": _reg_ref(reg),
                "record": _reg_summary(reg),
                "value_people_per_day": round(v, 1), "segment_total": round(worker_total, 1),
                "share_of_segment": round(v / worker_total, 3),
                "conversion": "IHK size class at its geometric midpoint, counted as on-site headcount",
            })
    visitors = [(r, annual_visitors(r["pageviews"], fit) * r["share"] / 365.0) for r in ctx.visitor_records
                if r.get("pageviews") is not None]
    visitor_total = sum(v for _, v in visitors)
    for r, v in visitors:
        if v >= CONCENTRATION_PEOPLE_PER_DAY or (visitor_total and v >= CONCENTRATION_SHARE * visitor_total):
            out.append({
                "candidate_id": f"concentration:wikidata:{r['qid']}",
                "module": "concentration", "segment": "visitors", "record_ref": f"wikidata:{r['qid']}",
                "record": {"qid": r["qid"], "name": r.get("name"), "kind": r.get("kind"),
                           "wikipedia_titles": r.get("wikipedia_titles"), "distance_m": r.get("distance_m"),
                           "gateway_share": round(r["share"], 3), "pageviews_12m": r["pageviews"]},
                "value_people_per_day": round(v, 1), "segment_total": round(visitor_total, 1),
                "share_of_segment": round(v / visitor_total, 3),
                "annual_visitors_from_pageviews": round(annual_visitors(r["pageviews"], fit)),
                "conversion": (f"log10(visitors) = {fit['intercept']:.3f} + {fit['slope']:.3f} * log10(12-month de+en "
                               f"pageviews), times the 1/k gateway share, / 365"),
            })
    return out


def large_registration(ctx: NodeContext, places: list[_Place], proj: _Projector) -> list[dict]:
    from shapely.geometry import Point

    out = []
    for reg in ctx.registrations:
        floor = _class_floor(reg.get("employees_range"))
        if floor is None or floor < LARGE_REGISTRATION_FLOOR:
            continue
        pt = Point(proj.xy(reg["lat"], reg["lon"]))
        near = sorted(((p, p.geom.distance(pt)) for p in places), key=lambda x: x[1])
        hints = [_hint(p, d) for p, d in near if d <= NAME_HINT_M and _is_named_place(p.tags)][:MAX_NAME_HINTS]
        out.append({
            "candidate_id": f"large_registration:{_reg_ref(reg)}",
            "module": "large_registration", "record_ref": _reg_ref(reg), "record": _reg_summary(reg),
            "counted_as": round(employees_estimate(reg.get("employees_range")) or 0, 1),
            "counted_at_this_station": ctx.uses_this_station is None or ctx.uses_this_station(reg),
            "osm_names_within_40m": hints if ctx.osm_elements is not None else None,
        })
    return out


def _is_named_place(tags: dict) -> bool:
    return any(k in tags for k in ("name", "office", "company", "operator", "addr:housenumber"))


def _is_workplace(tags: dict) -> bool:
    return (any(k in tags for k in WORKPLACE_KEYS) or tags.get("amenity") == "embassy"
            or tags.get("building") in WORKPLACE_BUILDING_VALUES)


def missing_entity(ctx: NodeContext, places: list[_Place], proj: _Projector,
                   nearest_station: Callable[[float, float], tuple[str, float]] | None = None) -> list[dict]:
    """Part (a): workplaces in the circle with no IHK registration within
    40 m, and large buildings whose registered headcount is far below what
    their floor area holds. A tagged node inside a qualifying building is
    folded into that building's candidate."""
    from shapely.geometry import Point

    regs = [(Point(proj.xy(r["lat"], r["lon"])), employees_estimate(r.get("employees_range")) or 0.0)
            for r in ctx.registrations]
    center = Point(0.0, 0.0)
    qualifying = []
    registered: dict[str, tuple[int, float]] = {}
    for p in places:
        centroid = p.geom.centroid
        if centroid.distance(center) > ctx.radius_m:
            continue
        footprint, floor_area, levels_tagged = _floor_area(p)
        large = (floor_area is not None and floor_area >= MISSING_ENTITY_FLOOR_AREA_M2
                 and p.tags.get("building") not in NON_WORKPLACE_BUILDINGS)
        if not (_is_workplace(p.tags) or large):
            continue
        near = [h for rp, h in regs if p.geom.distance(rp) <= MISSING_ENTITY_NO_IHK_M]
        headcount = sum(near)
        if near and not (large and headcount < floor_area / MISSING_ENTITY_M2_PER_REGISTERED):
            continue
        registered[p.ref] = (len(near), headcount)
        qualifying.append((p, footprint, floor_area, levels_tagged))

    buildings = [q for q in qualifying if q[1] is not None]
    folded: dict[str, list[dict]] = {}
    out_places = []
    for q in qualifying:
        p = q[0]
        if q[1] is None:
            host = next((b for b in buildings if b[0].geom.contains(p.geom.centroid)), None)
            if host is not None:
                folded.setdefault(host[0].ref, []).append(_hint(p, 0.0))
                continue
        out_places.append(q)

    out = []
    for p, footprint, floor_area, levels_tagged in out_places:
        c = p.geom.centroid
        lat, lon = proj.lat0 + c.y / proj.ky, proj.lon0 + c.x / proj.kx
        station = nearest_station(lat, lon) if nearest_station else None
        out.append({
            "candidate_id": f"missing_entity:osm:{p.ref}",
            "module": "missing_entity", "record_ref": f"osm:{p.ref}",
            "record": {**_hint(p, 0.0), "lat": round(lat, 6), "lon": round(lon, 6),
                       "tags": {k: v for k, v in p.tags.items() if not k.startswith("source")}},
            "footprint_m2": None if footprint is None else round(footprint),
            "floor_area_m2": None if floor_area is None else round(floor_area),
            "levels_tagged": levels_tagged,
            "reason_listed": ("no IHK registration within 40 m" if not registered[p.ref][0]
                              else "IHK headcount within 40 m below 1 per 100 m² of floor area"),
            "ihk_within_40m": {"registrations": registered[p.ref][0], "headcount_at_midpoints": round(registered[p.ref][1], 1)},
            "tagged_objects_inside": folded.get(p.ref, []),
            "nearest_station": None if station is None else {"name": station[0], "distance_m": round(station[1])},
            "nearest_ihk_m": round(min((p.geom.distance(rp) for rp, _ in regs), default=float("nan")), 1),
        })
    return out


def _words(name: str | None) -> set[str]:
    return set(re.findall(r"\w+", (name or "").lower()))


def poi_category(ctx: NodeContext, places: list[_Place], proj: _Projector) -> list[dict]:
    from shapely.geometry import Point

    from poi_conflation import names_match
    from poi_taxonomy import classify

    out = []
    for c in ctx.competitors:
        members = c["payload"]["members"]
        hits = sorted({w for m in members for w in _words(m["name"]) & NON_FOOD_NAME_WORDS})
        same_name = []
        if not any(m["source"] == "osm_overpass" for m in members):
            pt = Point(proj.xy(c["lat"], c["lon"]))
            for p in places:
                d = p.geom.distance(pt)
                if d <= POI_SAME_PLACE_M and any(names_match(p.tags.get("name"), m["name"]) for m in members):
                    osm_cat = classify(p.tags)
                    if (osm_cat.value if osm_cat else None) != c["category"]:
                        same_name.append({**_hint(p, d), "osm_category": osm_cat.value if osm_cat else None})
        if not hits and not same_name:
            continue
        out.append({
            "candidate_id": f"poi_category:{c['external_ref']}",
            "module": "poi_category", "record_ref": c["external_ref"],
            "record": _competitor_summary(c),
            "non_food_words": hits,
            "same_name_osm_objects_15m": same_name,
            "osm_objects_15m": _nearby(places, proj, c, POI_SAME_PLACE_M) if ctx.osm_elements is not None else None,
        })
    return out


def poi_duplicate(ctx: NodeContext) -> list[dict]:
    out = []
    comps = sorted(ctx.competitors, key=lambda c: c["external_ref"])
    for i, a in enumerate(comps):
        for b in comps[i + 1:]:
            if a["category"] != b["category"]:
                continue
            d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            if d <= POI_SAME_PLACE_M:
                out.append({
                    "candidate_id": f"poi_duplicate:{a['external_ref']}|{b['external_ref']}",
                    "module": "poi_duplicate", "record_ref": f"{a['external_ref']}|{b['external_ref']}",
                    "records": [_competitor_summary(a), _competitor_summary(b)],
                    "distance_m": round(d, 1),
                })
    return out


def _competitor_summary(c: dict) -> dict:
    return {"external_ref": c["external_ref"], "name": c["name"], "category": c["category"],
            "lat": c["lat"], "lon": c["lon"], "providers": c["payload"]["providers"],
            "members": c["payload"]["members"]}


def _nearby(places: list[_Place], proj: _Projector, c: dict, radius: float) -> list[dict]:
    from shapely.geometry import Point

    pt = Point(proj.xy(c["lat"], c["lon"]))
    near = sorted(((p, p.geom.distance(pt)) for p in places), key=lambda x: x[1])
    return [_hint(p, d) for p, d in near if d <= radius and _is_named_place(p.tags)][:MAX_NAME_HINTS]


def _class_floor(employees_range: str | None) -> int | None:
    head = (employees_range or "").split()
    return int(head[0]) if head and head[0].isdigit() else None


def build_worklist(ctx: NodeContext, fit: dict,
                   nearest_station: Callable[[float, float], tuple[str, float]] | None = None) -> dict:
    proj = _Projector(ctx.lat, ctx.lon)
    places = _places(ctx, proj)
    candidates = concentration(ctx, fit) + large_registration(ctx, places, proj)
    if ctx.osm_elements is not None:
        candidates += missing_entity(ctx, places, proj, nearest_station)
    candidates += poi_category(ctx, places, proj) + poi_duplicate(ctx)
    ids = [c["candidate_id"] for c in candidates]
    assert len(ids) == len(set(ids)), "candidate_id collision"
    return {
        "generator": GENERATOR_VERSION,
        "area_id": ctx.area_id, "name": ctx.name, "lat": ctx.lat, "lon": ctx.lon, "radius_m": ctx.radius_m,
        "state": "corrections_off",
        "provenance": ctx.provenance,
        "modules_skipped": [] if ctx.osm_elements is not None else ["missing_entity (no OSM context snapshot)"],
        "counts": {m: sum(1 for c in candidates if c["module"] == m)
                   for m in ("concentration", "large_registration", "missing_entity", "poi_category", "poi_duplicate")},
        "candidates": candidates,
    }


# --------------------------------------------------------------------------
# OSM context snapshot
# --------------------------------------------------------------------------


def osm_context_query(lat: float, lon: float, radius_m: float) -> str:
    around = f"around:{int(radius_m)},{lat:.6f},{lon:.6f}"
    not_lines = "".join(f'[!"{k}"]' for k in ("highway", "railway", "route", "boundary", "waterway"))
    clauses = [f'nwr({around})["name"]{not_lines};']
    clauses += [f'nwr({around})["{k}"];' for k in (*WORKPLACE_KEYS, "operator")]
    clauses += [f'nwr({around})["amenity"="embassy"];', f'way({around})["building"];',
                f'relation({around})["building"];']
    return "[out:json][timeout:180];\n(\n  " + "\n  ".join(clauses) + "\n);\nout tags geom;"


def _key(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name)


def latest_osm_context(key: str) -> tuple[dict, Path] | None:
    files = sorted(OSM_CONTEXT_DIR.glob(f"{key}_2*.json"))
    return (json.loads(files[-1].read_text()), files[-1]) if files else None


def fetch_osm_context(key: str, lat: float, lon: float, radius_m: float) -> tuple[dict, Path]:
    """One Overpass call (mirror fallback, as osm_client); stored raw."""
    import httpx

    from osm_client import HTTP_HEADERS, OVERPASS_URLS

    query = osm_context_query(lat, lon, radius_m + OSM_CONTEXT_MARGIN_M)
    errors = []
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                r = httpx.post(url, data={"data": query}, headers=HTTP_HEADERS, timeout=200)
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {exc}")
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                if data.get("remark") and "error" in data["remark"].lower():
                    errors.append(f"{url}: {data['remark'][:200]}")
                    break
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                OSM_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
                path = OSM_CONTEXT_DIR / f"{key}_{stamp}.json"
                data["query"] = {"lat": lat, "lon": lon, "radius_m": radius_m + OSM_CONTEXT_MARGIN_M,
                                 "overpass_url": url, "fetched_at": stamp, "ql": query}
                path.write_text(json.dumps(data, ensure_ascii=False))
                return data, path
            errors.append(f"{url}: HTTP {r.status_code}")
            if r.status_code not in (429, 502, 503, 504):
                break
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("OSM context fetch failed:\n" + "\n".join(errors))


def _osm_context(key: str, lat: float, lon: float, radius_m: float, fetch: bool) -> tuple[list[dict] | None, str | None]:
    found = latest_osm_context(key)
    if found is None and fetch:
        found = fetch_osm_context(key, lat, lon, radius_m)
        time.sleep(2)
    if found is None:
        return None, None
    data, path = found
    return data.get("elements", []), str(path.relative_to(PROJECT_ROOT))


# --------------------------------------------------------------------------
# Context loaders
# --------------------------------------------------------------------------


def candidate_context(node: LocationInput, db_path: Path = DB_PATH, fetch: bool = False) -> NodeContext:
    """A candidate site from the database, read only, in the raw state."""
    from metrics_pipeline import _station_assigner, latest_snapshot_id
    from poi_conflation import (SOURCE_PRIORITY, _latest_source_snapshot, _load_records,
                                _station_paths_from_raw, build_competitor_set)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ihk_snapshot = latest_snapshot_id(conn, node.area_id, "business")
        registrations = [
            {**json.loads(r[0]), "lat": r[1], "lon": r[2]}
            for r in conn.execute("SELECT payload_json, lat, lon FROM observations WHERE area_id = ? AND "
                                  "entity_type = 'business' AND snapshot_id = ?", (node.area_id, ihk_snapshot))
        ]
        attraction_snapshot = latest_snapshot_id(conn, node.area_id, "attraction")
        visitors = [
            {**json.loads(r[2]), "qid": r[0], "name": r[1]}
            for r in conn.execute("SELECT external_ref, name, payload_json FROM observations WHERE area_id = ? AND "
                                  "entity_type = 'attraction' AND snapshot_id = ?", (node.area_id, attraction_snapshot))
        ]
        snaps = {s: _latest_source_snapshot(conn, node.area_id, s) for s in SOURCE_PRIORITY}
        snaps = {s: v for s, v in snaps.items() if v is not None}
        by_source = {s: _load_records(conn, node.area_id, s, v["snapshot_id"]) for s, v in snaps.items()}
    finally:
        conn.close()
    competitors, _ = build_competitor_set(node, by_source, _station_paths_from_raw(snaps["osm_overpass"]["raw_file_ref"]),
                                          quality_rules=False, corrections={})
    osm, osm_ref = _osm_context(node.area_id, node.lat, node.lon, node.radius_m, fetch)
    return NodeContext(
        area_id=node.area_id, name=node.name, lat=node.lat, lon=node.lon, radius_m=node.radius_m,
        registrations=registrations, visitor_records=visitors, competitors=competitors, osm_elements=osm,
        uses_this_station=_station_assigner(node),
        provenance={"ihk_snapshot_id": ihk_snapshot, "attraction_snapshot_id": attraction_snapshot,
                    "poi_source_snapshot_ids": {s: v["snapshot_id"] for s, v in snaps.items()},
                    "conflation": "conflation_v1 (quality rules and verified corrections off)",
                    "osm_context": osm_ref},
    )


def reference_context(station: dict, fetch: bool = False) -> NodeContext:
    """A reference station (generalisation set) from the calibration
    caches: OSM + Overture competitors as calibrate_anchors, gateway
    attractions as calibrate_demand, and IHK registrations with ids and
    coordinates (the calibration cache holds size classes only, so they
    are fetched once with --fetch and stored under db/raw/audit/ihk/).
    Reference stations are not station-assigned, as in calibrate_demand."""
    import calibrate_anchors
    import calibrate_demand
    from attraction_pipeline import rail_stations
    from poi_conflation import build_competitor_set
    from poi_pipeline import _station_paths, clean_and_classify, normalize_overture

    name, lat, lon = station["station"], station["lat"], station["lon"]
    radius = calibrate_demand.RADIUS_M
    key = _key(name)
    node = LocationInput(area_id=key, name=name, lat=lat, lon=lon, radius_m=radius)

    ihk_files = sorted(IHK_DIR.glob(f"{key}_2*.json"))
    if not ihk_files and fetch:
        from ihk_client import fetch_gewerbedaten_bbox

        raw = fetch_gewerbedaten_bbox(lat, lon, radius)
        IHK_DIR.mkdir(parents=True, exist_ok=True)
        ihk_files = [IHK_DIR / f"{key}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"]
        ihk_files[0].write_text(json.dumps(raw, ensure_ascii=False))
    registrations = []
    if ihk_files:
        for f in json.loads(ihk_files[-1].read_text()).get("features", []):
            if not f.get("geometry"):
                continue
            flon, flat = f["geometry"]["coordinates"][:2]
            if haversine_m(lat, lon, flat, flon) <= radius:
                registrations.append({**f["properties"], "lat": flat, "lon": flon})

    gtfs = rail_stations()
    dists = {n: min(haversine_m(lat, lon, a, b) for a, b in locs) for n, locs in gtfs.items()}
    gtfs_name = min(dists, key=dists.get)
    visitors = []
    if dists[gtfs_name] <= calibrate_demand.GTFS_MATCH_M:
        by_station = calibrate_demand.gateway_records(calibrate_demand.ring_attractions(), gtfs)
        recs = by_station.get(gtfs_name, {})
        pv = calibrate_demand.pageviews_for(sorted(recs))
        links = calibrate_demand.sitelinks_sparql(sorted(recs))
        visitors = [{**r, "pageviews": pv.get(q), "wikipedia_titles": links.get(q, {})} for q, r in recs.items()]

    osm_raw = calibrate_anchors.station_osm({"name": name, "lat": lat, "lon": lon})
    osm_records, _ = clean_and_classify(osm_raw["elements"])
    osm = [{"source": "osm_overpass", "ref": r["external_ref"], "name": r["name"], "category": r["category"],
            "lat": r["lat"], "lon": r["lon"], "opening_hours_raw": r["opening_hours_raw"],
            "payload": {"in_station": r["in_station"]}} for r in osm_records]
    overture_rows, overture_meta = calibrate_anchors.ring_overture()
    overture = [{"source": "overture_places", "ref": r["external_ref"], "name": r["name"], "category": r["category"],
                 "lat": r["lat"], "lon": r["lon"], "opening_hours_raw": None, "payload": r["payload"]}
                for r in normalize_overture(overture_rows)[0]
                if haversine_m(lat, lon, r["lat"], r["lon"]) <= radius + 100]
    competitors, _ = build_competitor_set(node, {"osm_overpass": osm, "overture_places": overture},
                                          _station_paths(osm_raw["elements"]), quality_rules=False, corrections={})
    osm_ctx, osm_ref = _osm_context(key, lat, lon, radius, fetch)
    return NodeContext(
        area_id=key, name=name, lat=lat, lon=lon, radius_m=radius, registrations=registrations,
        visitor_records=visitors, competitors=competitors, osm_elements=osm_ctx, uses_this_station=None,
        provenance={"ihk": str(ihk_files[-1].relative_to(PROJECT_ROOT)) if ihk_files else None,
                    "gtfs_station": gtfs_name if dists[gtfs_name] <= calibrate_demand.GTFS_MATCH_M else None,
                    "attractions": "db/raw/calibration/demand_cache (ring query, pageviews, sitelinks)",
                    "poi_sources": {"osm_overpass": "db/raw/calibration/osm_cache",
                                    "overture_places": f"overture_ring {overture_meta.get('release')}"},
                    "conflation": "conflation_v1 (quality rules and verified corrections off)",
                    "osm_context": osm_ref},
    )


def _nearest_station_fn() -> Callable[[float, float], tuple[str, float]]:
    from attraction_pipeline import rail_stations

    stations = rail_stations()

    def nearest(lat: float, lon: float) -> tuple[str, float]:
        best = min(((n, min(haversine_m(lat, lon, a, b) for a, b in locs)) for n, locs in stations.items()),
                   key=lambda x: x[1])
        return best

    return nearest


def run(fetch: bool = False, out_dir: Path = WORKLIST_DIR) -> dict[str, dict]:
    """Worklists for every node in the audit's sets (audit_eval.AUDIT_SETS)."""
    from audit_eval import AUDIT_SETS, reference_station
    from models import CANDIDATE_NODES

    fit, fit_path = latest_visitor_fit()
    nearest = _nearest_station_fn()
    by_id = {n.area_id: n for n in CANDIDATE_NODES}
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for set_name, nodes in AUDIT_SETS.items():
        for node_id in nodes:
            ctx = (candidate_context(by_id[node_id], fetch=fetch) if node_id in by_id
                   else reference_context(reference_station(node_id), fetch=fetch))
            wl = build_worklist(ctx, fit, nearest)
            wl["set"] = set_name
            wl["provenance"]["visitor_fit"] = fit_path.name
            (out_dir / f"{ctx.area_id}.json").write_text(json.dumps(wl, ensure_ascii=False, indent=1))
            results[ctx.area_id] = wl
            print(f"[{set_name}] {ctx.name}: {len(wl['candidates'])} candidates {wl['counts']}"
                  + (f"  skipped: {wl['modules_skipped']}" if wl["modules_skipped"] else ""))
    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build the data-plausibility audit worklists (no LLM).")
    ap.add_argument("--fetch", action="store_true",
                    help="fetch missing OSM context snapshots (Overpass) and reference-station IHK records (WFS)")
    run(fetch=ap.parse_args().fetch)
