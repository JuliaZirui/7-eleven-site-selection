"""Cross-source POI conflation: OSM + Overture + Foursquare -> one competitor set.

The project rule "records from different sources are never
summed directly" is what this module enforces for POIs. Each source's
latest per-area snapshot (entity_type='poi_source') is cut to the same
radius, matched shop-to-shop, and written as one new snapshot
(source_name='poi_conflated', entity_type='poi') -- the only POI set
metrics_pipeline / scoring / report / agent_followup read.

Matching rules (deterministic, Program work -- no LLM judgment):
  - two records match if both are named, sit within MATCH_DISTANCE_M,
    and their names agree (equal after normalization, one contains the
    other, or difflib ratio >= NAME_SIMILARITY_MIN);
  - candidate pairs are merged closest-first, and a cluster may hold at
    most one OSM record: OSM was already deduplicated within itself
    (poi_pipeline.DEDUP_DISTANCE_M), so two OSM shops are two shops even
    if an Overture row sits between them;
  - unnamed records are never merged (same rule as the OSM dedup).

Data-quality rules (conflation_v2), from the defects the manual benchmark
found (comparison/manual_vs_tool.md); every record they remove or merge
is listed in the snapshot's stats, so each correction stays auditable:
  - near-duplicate merge: two records from different sources within
    NEAR_DUPLICATE_M, with the same category and a distinctive name
    word in common ("Ha's Café" / "Bäckerei Bich Ha") are one shop,
    even though the full names fail names_match;
  - name/category conflict: a non-OSM record classified café or bakery
    whose name says it is a restaurant or not a food shop at all
    ("MAREDO Steakhouse" tagged coffee_shop) is dropped -- OSM's
    category is trusted, the conflated providers' is not;
  - verified corrections: records a person checked and found wrong
    (POI_CORRECTIONS_CSV, one row per record with its evidence) are
    dropped wherever they appear, candidate or reference station.

Each output record keeps its provenance: every member record, and the
set of independent upstream providers behind it. Overture is itself a
conflation of Meta / Microsoft / Foursquare / AllThePlaces /
BrightQuery, so providers are counted upstream -- a place seen by
Overture-via-Foursquare and by FSQ OS Places is one provider, not two.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from geo import haversine_m
from models import LocationInput
from poi_taxonomy import POICategory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "db" / "raw" / "conflated"

SOURCE_ENTITY_TYPE = "poi_source"
CONFLATED_SOURCE_NAME = "poi_conflated"
CONFLATION_METHOD_VERSION = "conflation_v2"

# Priority when picking a cluster's primary record (name, category,
# coordinates, opening hours): OSM carries hours and was the v1 source
# of record; Overture next (confidence-scored); FSQ last.
SOURCE_PRIORITY = ("osm_overpass", "overture_places", "fsq_os_places")
REQUIRED_SOURCE = "osm_overpass"

# 60m: the same shop's coordinates differ between sources by up to a
# building's width (entrance node vs. geocoded address vs. check-in
# centroid); 60m absorbs that while two same-name branches (e.g. two
# Starbucks at Warschauer Straße) still sit further apart.
MATCH_DISTANCE_M = 60.0
NAME_SIMILARITY_MIN = 0.8
MIN_CONTAINMENT_LEN = 4

# conflation_v2 data-quality rules (module docstring). 15m: Ha's Café and
# Bäckerei Bich Ha sit 8m apart; two genuinely different same-category
# shops sharing a name word closer than 15m are rare enough to accept.
NEAR_DUPLICATE_M = 15.0
MIN_SHARED_WORD_LEN = 2
# Name words that contradict a café/bakery category: a full-service
# restaurant or a non-food place. Kept short and unambiguous -- "grill",
# "restaurant" or "bar" also name genuine snack and coffee places.
NON_SHOP_NAME_WORDS = frozenset(
    "steakhouse steakhaus ristorante trattoria osteria brasserie hotel hostel".split()
)
# Words that name a food type, not a business: neighbours sharing only
# one of these ("Manhattan Burger" / "Charlotten Burger") are not merged.
FOOD_TYPE_WORDS = frozenset(
    """burger döner doner kebap kebab gemüse gemuse pizza chicken sushi back feinkost konditorei
    patisserie spätshop tabak getränke asia supermarkt super market fresh food eatery original""".split()
)
NAME_CHECKED_CATEGORIES = frozenset({POICategory.CAFE.value, POICategory.BAKERY.value})
POI_CORRECTIONS_CSV = PROJECT_ROOT / "db" / "raw" / "calibration" / "poi_corrections_verified.csv"

CONFLATED_LICENSE = "Derived: ODbL (OSM) + Overture (CDLA-Permissive-2.0/ODbL) + Apache-2.0 (FSQ OS Places)"


class ConflationError(Exception):
    """Raised when an area has no OSM snapshot to conflate against."""


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return "".join(ch.lower() for ch in name if ch.isalnum())


# Words that say what a place is or where, not which place it is --
# Overture/FSQ names often carry them ("ama Café", "Berlin Einshtein
# Kaffee") where OSM has the bare name ("ama", "Cafe Einstein"). Removed
# before comparing; if nothing is left ("Kiosk"), the full name is used.
GENERIC_NAME_WORDS = frozenset(
    """cafe café kaffee coffee coffeeshop espresso bar restaurant bistro imbiss
    bäckerei baeckerei backerei bakery backshop berlin shop store späti spätkauf
    kiosk markt to go the und and am an der die das gmbh""".split()
)


def _core_name(name: str | None) -> str:
    if not name:
        return ""
    words = re.findall(r"\w+", name.lower())
    core = "".join(w for w in words if w not in GENERIC_NAME_WORDS)
    return core or _normalize_name(name)


def names_match(a: str | None, b: str | None) -> bool:
    if not _normalize_name(a) or not _normalize_name(b):
        return False
    na, nb = _core_name(a), _core_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = sorted((na, nb), key=len)
    if len(shorter) >= MIN_CONTAINMENT_LEN and shorter in longer:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= NAME_SIMILARITY_MIN


def _name_words(name: str | None) -> set[str]:
    """Distinctive words of a name: generic words and 1-letter tokens out."""
    return {w for w in re.findall(r"\w+", (name or "").lower())
            if w not in GENERIC_NAME_WORDS and w not in FOOD_TYPE_WORDS and len(w) >= MIN_SHARED_WORD_LEN}


def near_duplicate(a: dict, b: dict, distance_m: float) -> bool:
    return (
        distance_m <= NEAR_DUPLICATE_M
        and a["source"] != b["source"]
        and a["category"] == b["category"]
        and bool(_name_words(a["name"]) & _name_words(b["name"]))
    )


def name_contradicts_category(record: dict) -> bool:
    if record["source"] == "osm_overpass" or record["category"] not in NAME_CHECKED_CATEGORIES:
        return False
    return bool(set(re.findall(r"\w+", (record["name"] or "").lower())) & NON_SHOP_NAME_WORDS)


def load_verified_corrections(path: Path = POI_CORRECTIONS_CSV) -> dict[str, dict]:
    """external_ref -> correction row. Empty if the file is absent."""
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {row["external_ref"]: row for row in csv.DictReader(f) if row["action"] == "exclude"}


def providers_of(record: dict) -> set[str]:
    """Independent upstream providers behind one source record."""
    if record["source"] == "osm_overpass":
        return {"openstreetmap"}
    if record["source"] == "fsq_os_places":
        return {"foursquare"}
    upstream = record["payload"].get("upstream_datasets") or []
    return {d.lower() for d in upstream} or {"overture"}


def _latest_source_snapshot(conn: sqlite3.Connection, area_id: str, source_name: str) -> dict | None:
    row = conn.execute(
        """
        SELECT s.snapshot_id, s.data_as_of_date, s.raw_file_ref
        FROM observations o JOIN source_snapshots s ON s.snapshot_id = o.snapshot_id
        WHERE o.area_id = ? AND o.entity_type = ? AND s.source_name = ?
        ORDER BY s.fetched_at DESC, s.snapshot_id DESC
        LIMIT 1
        """,
        (area_id, SOURCE_ENTITY_TYPE, source_name),
    ).fetchone()
    if row is None:
        return None
    return {"snapshot_id": row[0], "data_as_of_date": row[1], "raw_file_ref": row[2]}


def _load_records(conn: sqlite3.Connection, area_id: str, source_name: str, snapshot_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT external_ref, name, category, lat, lon, opening_hours_raw, payload_json
        FROM observations
        WHERE area_id = ? AND entity_type = ? AND snapshot_id = ?
        """,
        (area_id, SOURCE_ENTITY_TYPE, snapshot_id),
    ).fetchall()
    return [
        {
            "source": source_name,
            "ref": r[0],
            "name": r[1],
            "category": r[2],
            "lat": r[3],
            "lon": r[4],
            "opening_hours_raw": r[5],
            "payload": json.loads(r[6] or "{}"),
        }
        for r in rows
    ]


def _station_paths_from_raw(raw_file_ref: str | None) -> list | None:
    """Station anchors from the OSM snapshot's raw Overpass file, so
    Overture/FSQ-only records get the same in-station test as OSM ones.
    None if the raw file is unavailable."""
    from poi_pipeline import _station_paths

    if not raw_file_ref:
        return None
    path = PROJECT_ROOT / raw_file_ref
    if not path.exists():
        return None
    return _station_paths(json.loads(path.read_text()).get("elements", []))


def cluster_records(records: list[dict], quality_rules: bool = True,
                    near_duplicate_log: list | None = None) -> list[list[dict]]:
    """Group source records into physical shops (see module docstring).
    quality_rules=False reproduces conflation_v1 (no near-duplicate merge).
    Merges made only by the near-duplicate rule are appended to
    near_duplicate_log as [record_a, record_b, distance_m]."""
    parent = list(range(len(records)))
    osm_count = [1 if r["source"] == "osm_overpass" else 0 for r in records]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            if a["source"] == "osm_overpass" and b["source"] == "osm_overpass":
                continue
            d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            by_name = d <= MATCH_DISTANCE_M and names_match(a["name"], b["name"])
            if by_name or (quality_rules and near_duplicate(a, b, d)):
                pairs.append((d, i, j, by_name))

    for d, i, j, by_name in sorted(pairs):
        ri, rj = find(i), find(j)
        if ri == rj or osm_count[ri] + osm_count[rj] > 1:
            continue
        parent[rj] = ri
        osm_count[ri] += osm_count[rj]
        if not by_name and near_duplicate_log is not None:
            near_duplicate_log.append([records[i], records[j], d])

    clusters: dict[int, list[dict]] = {}
    for i, rec in enumerate(records):
        clusters.setdefault(find(i), []).append(rec)
    return list(clusters.values())


def _primary(cluster: list[dict]) -> dict:
    return min(
        cluster,
        key=lambda r: (SOURCE_PRIORITY.index(r["source"]), -(r["payload"].get("confidence") or 0.0)),
    )


def build_competitor_set(
    node: LocationInput, records_by_source: dict[str, list[dict]], station_paths: list | None,
    quality_rules: bool = True, corrections: dict[str, dict] | None = None,
) -> tuple[list[dict], dict]:
    """Pure function: per-source records -> conflated competitor records + stats.
    quality_rules=False reproduces conflation_v1; corrections defaults to
    POI_CORRECTIONS_CSV when the rules are on."""
    from poi_pipeline import _in_station

    if corrections is None:
        corrections = load_verified_corrections() if quality_rules else {}
    in_radius, outside = [], 0
    excluded = {"name_category_conflict": [], "verified_correction": []}
    for records in records_by_source.values():
        for rec in records:
            if haversine_m(node.lat, node.lon, rec["lat"], rec["lon"]) > node.radius_m:
                outside += 1
            elif rec["ref"] in corrections:
                excluded["verified_correction"].append(
                    {"ref": rec["ref"], "name": rec["name"], "category": rec["category"],
                     "reason": corrections[rec["ref"]]["reason"]})
            elif quality_rules and name_contradicts_category(rec):
                excluded["name_category_conflict"].append(
                    {"ref": rec["ref"], "name": rec["name"], "category": rec["category"]})
            else:
                in_radius.append(rec)

    competitors = []
    merge_log: list = []
    clusters = cluster_records(in_radius, quality_rules, merge_log)
    near_duplicates = [
        {"records": [{"source": r["source"], "ref": r["ref"], "name": r["name"]} for r in (a, b)],
         "distance_m": round(d, 1)}
        for a, b, d in merge_log
    ]
    for cluster in clusters:
        primary = _primary(cluster)
        osm_member = next((r for r in cluster if r["source"] == "osm_overpass"), None)
        providers = set().union(*(providers_of(r) for r in cluster))
        if osm_member is not None and "in_station" in osm_member["payload"]:
            in_station = bool(osm_member["payload"]["in_station"])
        elif station_paths is not None:
            in_station = _in_station(primary["lat"], primary["lon"], station_paths)
        else:
            in_station = False
        competitors.append(
            {
                "external_ref": primary["ref"],
                "name": primary["name"],
                "category": primary["category"],
                "lat": primary["lat"],
                "lon": primary["lon"],
                "opening_hours_raw": osm_member["opening_hours_raw"] if osm_member else None,
                "payload": {
                    "primary_source": primary["source"],
                    "sources": sorted({r["source"] for r in cluster}, key=SOURCE_PRIORITY.index),
                    "providers": sorted(providers),
                    "n_providers": len(providers),
                    "in_station": in_station,
                    "category_conflict": len({r["category"] for r in cluster}) > 1,
                    "members": [
                        {"source": r["source"], "ref": r["ref"], "name": r["name"], "category": r["category"]}
                        for r in cluster
                    ],
                },
            }
        )
    stats = {
        "source_records": sum(len(v) for v in records_by_source.values()),
        "outside_radius": outside,
        "competitors": len(competitors),
        "with_osm": sum(1 for c in competitors if "osm_overpass" in c["payload"]["sources"]),
        "corroborated": sum(1 for c in competitors if c["payload"]["n_providers"] >= 2),
        "method": CONFLATION_METHOD_VERSION if quality_rules else "conflation_v1",
        "excluded": excluded,
        "near_duplicates_merged": near_duplicates,
    }
    return competitors, stats


def conflate_area(conn: sqlite3.Connection, node: LocationInput, quality_rules: bool = True,
                  raw_dir: Path | None = None) -> dict:
    """Build and persist the conflated competitor snapshot for one area.
    quality_rules=False: conflation_v1, no verified corrections (the
    pre-review state; raw_dir then points outside db/raw/)."""
    now = datetime.now(timezone.utc)
    snapshots = {name: _latest_source_snapshot(conn, node.area_id, name) for name in SOURCE_PRIORITY}
    if snapshots[REQUIRED_SOURCE] is None:
        raise ConflationError(f"[{node.area_id}] no OSM source snapshot to conflate against")
    used = {name: snap for name, snap in snapshots.items() if snap is not None}
    records_by_source = {
        name: _load_records(conn, node.area_id, name, snap["snapshot_id"]) for name, snap in used.items()
    }
    station_paths = _station_paths_from_raw(used[REQUIRED_SOURCE]["raw_file_ref"])
    competitors, stats = build_competitor_set(node, records_by_source, station_paths, quality_rules)
    method = stats["method"]

    raw_dir = raw_dir or RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{node.area_id}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    raw_path.write_text(json.dumps({"stats": stats, "competitors": competitors}, ensure_ascii=False, indent=2))

    missing = [name for name in SOURCE_PRIORITY if name not in used]
    notes = (
        f"method={method}; radius_m={node.radius_m}; "
        f"source_snapshots={json.dumps({n: s['snapshot_id'] for n, s in used.items()})}; "
        f"source_as_of={json.dumps({n: s['data_as_of_date'] for n, s in used.items()})}; "
        f"missing_sources={json.dumps(missing)}; stats={json.dumps(stats)}"
    )
    cur = conn.execute(
        """
        INSERT INTO source_snapshots (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            CONFLATED_SOURCE_NAME,
            # A derived set is only as current as its oldest input.
            min(s["data_as_of_date"] for s in used.values()),
            now.isoformat(),
            CONFLATED_LICENSE,
            _ref(raw_path),
            notes,
        ),
    )
    snapshot_id = cur.lastrowid
    for c in competitors:
        conn.execute(
            """
            INSERT OR IGNORE INTO observations
                (snapshot_id, area_id, entity_type, external_ref, name, category,
                 lat, lon, opening_hours_raw, payload_json)
            VALUES (?, ?, 'poi', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                node.area_id,
                c["external_ref"],
                c["name"],
                c["category"],
                c["lat"],
                c["lon"],
                c["opening_hours_raw"],
                json.dumps(c["payload"], ensure_ascii=False),
            ),
        )
    conn.commit()
    return {"area_id": node.area_id, "snapshot_id": snapshot_id, "missing_sources": missing, **stats}


def _ref(path: Path) -> str:
    """Raw-file reference as stored in source_snapshots: project-relative
    when the file is inside the project."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def run(db_path: Path = PROJECT_ROOT / "db" / "site_selection.db", quality_rules: bool = True,
        raw_dir: Path | None = None) -> list[dict]:
    """Re-conflate every candidate from its latest stored source snapshots
    -- no network call. Used when the conflation rules change but the
    source data have not: python3 poi_conflation.py
    quality_rules=False is for a database copy only (metrics_pipeline.run_raw)."""
    from models import CANDIDATE_NODES

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        results = [conflate_area(conn, node, quality_rules, raw_dir) for node in CANDIDATE_NODES]
    finally:
        conn.close()
    for r in results:
        print(f"[{r['area_id']}] conflated snapshot #{r['snapshot_id']}: {r['competitors']} competitors "
              f"({r['method']}; {len(r['near_duplicates_merged'])} near-duplicates merged, "
              f"{sum(len(v) for v in r['excluded'].values())} records excluded)")
    return results


if __name__ == "__main__":
    run()
