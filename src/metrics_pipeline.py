"""Day 3 pipeline: fixed computation layer. Reads the observations
already written by poi_pipeline.py / lor_pipeline.py (Day 1-2) and
computes deterministic area_metrics rows — no new API calls, no LLM
judgment. Per project_plan_v3.md's "Division of Responsibilities",
this whole file is Program work: cleaning is already done, this stage
is spatial calculation, opening-hours parsing, and scoring inputs.

Writes, per area:
  - competitor counts (total + per category)
  - competitor density (per km^2, per 1,000 residents)
  - nearest / mean competitor distance from the anchor coordinate
  - resident population (carried over from the LOR observation)
  - opening-hours coverage: how many competitors are open during each
    of the three DEFAULT_TIME_WINDOWS, evaluated on a representative
    weekday (We) and weekend day (Sa) — see opening_hours.py for why
  - opening-hours data completeness (known vs. unknown per area)
  - (scoring_v4 inputs) commute-peak competitor counts: the morning
    set (bakery, café, supermarket, fuel shop) and the evening set
    (späti, fast food, supermarket, fuel shop), minus those whose
    hours show them closed in the window

An area with no POI snapshot at all gets every competitor-derived
metric written as missing (NO_POI_SNAPSHOT), never as 0.
  - (Day 5 scoring inputs) IHK business mix: registered businesses,
    businesses with 10+ employees, office-type share
  - (Day 5 scoring inputs) area-weighted resident estimate for the
    400m circle itself, and how much of it the anchor Planungsraum
    actually covers

area_metrics is append-only: each run adds a fresh set of rows dated
by computed_at, rather than overwriting the previous run's numbers —
consistent with "snapshots are appended, never overwritten" applied to
derived data, not just raw ones. Re-running after new source snapshots
lets old and new computed figures sit side by side for comparison.

Run directly to process all CANDIDATE_NODES:
    python3 metrics_pipeline.py
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from day_types import COMPETITION_WINDOWS, CompetitionWindow, count_window_competitors
from day_types import PERIOD as DAY_TYPE_PERIOD
from demand_model import (
    latest_visitor_fit,
    load_known_attendance,
    visitors_split,
    load_onsite_verifications,
    load_unregistered_employers,
    visitors_per_day,
    workers_onsite,
)
from geo import haversine_m
from models import CANDIDATE_NODES, DEFAULT_TIME_WINDOWS, LocationInput
from opening_hours import (
    REPRESENTATIVE_WEEKDAY,
    REPRESENTATIVE_WEEKEND,
    WEEKDAY_TOKENS,
    is_open_during,
    parse_opening_hours,
)
from poi_conflation import CONFLATED_SOURCE_NAME, CONFLATION_METHOD_VERSION
from poi_taxonomy import DIRECT_FORMAT_CATEGORIES, EVENING_PEAK_CATEGORIES, MORNING_PEAK_CATEGORIES, POICategory
from models import TimeWindowLabel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"

SPATIAL_METHOD_VERSION = "spatial_v1"
HOURS_METHOD_VERSION = "opening_hours_v1"
POPULATION_METHOD_VERSION = "population_v1"
CATCHMENT_POPULATION_METHOD_VERSION = "catchment_population_v1"
BUSINESS_MIX_METHOD_VERSION = "ihk_business_mix_v1"
PEAK_COMPETITION_METHOD_VERSION = "window_competitors_v2"
VISITOR_METHOD_VERSION = "visitor_estimate_v3"
# Day type (None = annual average day) -> visitor metric.
VISITOR_METRICS: dict[str | None, str] = {
    None: "visitor_daily_estimate",
    "weekday": "visitor_weekday_estimate",
    "weekend": "visitor_weekend_estimate",
}

# Written for every competitor-derived metric when the area has no POI
# snapshot at all (e.g. its first fetch failed). An absent snapshot is
# unknown competition, never a count of 0 -- a 0 would score as an
# empty competitive field.
NO_POI_SNAPSHOT = "no competitor POI snapshot for this area"

# Weekday commute-peak competitor sets, kept for callers that predate the
# weekend windows; day_types.COMPETITION_WINDOWS is the full list.
PEAK_COMPETITOR_SETS: tuple[tuple[str, TimeWindowLabel, tuple[str, ...]], ...] = (
    ("morning_peak", TimeWindowLabel.MORNING_PEAK, MORNING_PEAK_CATEGORIES),
    ("evening_peak", TimeWindowLabel.EVENING_PEAK, EVENING_PEAK_CATEGORIES),
)

# NACE Rev. 2 divisions counted as office-type employers for the
# "office commuter" demand signal: J (58-63 information/communication),
# K (64-66 finance/insurance), L (68 real estate), M (69-74 professional/
# scientific/technical, excluding 75 veterinary). A registered-address
# proxy, not a headcount -- see _compute_business_mix.
OFFICE_NACE_DIVISIONS = frozenset(
    [f"{d:02d}" for d in range(58, 64)]
    + ["64", "65", "66", "68"]
    + [f"{d:02d}" for d in range(69, 75)]
)

# employees_range values at or above this size class. Chosen over a
# raw business count because 50-65% of registered businesses in each
# radius are "0 Beschäftigte" (sole traders, often registered at a home
# address), which would make a raw count track residential density,
# not daytime workforce.
WORKPLACE_MIN_EMPLOYEES = 10

_DAY_TYPES: tuple[tuple[str, int], ...] = (
    (f"weekday_{WEEKDAY_TOKENS[REPRESENTATIVE_WEEKDAY]}", REPRESENTATIVE_WEEKDAY),
    (f"weekend_{WEEKDAY_TOKENS[REPRESENTATIVE_WEEKEND]}", REPRESENTATIVE_WEEKEND),
)


def _write_metric(
    conn: sqlite3.Connection,
    *,
    area_id: str,
    metric_name: str,
    value: float | None,
    unit: str,
    period_label: str,
    method_version: str,
    snapshot_ids: list[int],
    missing_reason: str | None = None,
) -> None:
    is_missing = value is None
    if is_missing and not missing_reason:
        raise ValueError(f"{metric_name}: missing value written without a missing_reason")
    conn.execute(
        """
        INSERT INTO area_metrics
            (area_id, metric_name, metric_value, is_missing, missing_reason, unit,
             period_label, computation_method_version, source_snapshot_ids_json, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            area_id,
            metric_name,
            value,
            1 if is_missing else 0,
            missing_reason,
            unit,
            period_label,
            method_version,
            json.dumps(sorted(set(snapshot_ids))),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _write_missing(
    conn: sqlite3.Connection,
    node: LocationInput,
    specs: list[tuple[str, str, str]],
    method_version: str,
    reason: str,
) -> None:
    """Write each (metric_name, unit, period_label) as missing with one reason."""
    for metric_name, unit, period_label in specs:
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name=metric_name,
            value=None,
            unit=unit,
            period_label=period_label,
            method_version=method_version,
            snapshot_ids=[],
            missing_reason=reason,
        )


def latest_snapshot_id(conn: sqlite3.Connection, area_id: str, entity_type: str) -> int | None:
    """The newest snapshot holding this area's observations of one type.

    Snapshots are appended, never overwritten, so after a Day 4 Agent
    refetch an area holds the same POIs/businesses in two snapshots.
    Every consumer that counts or describes "the area as it is now"
    must read only this one, or it double-counts (caught by the Day 6
    duplicate-records tests). Cross-snapshot reads are only for
    detecting conflicts between snapshots.

    Known limit: source_snapshots has no area_id, so a refetch that
    returned zero rows for an area leaves no trace here and the older
    snapshot is still read as current.
    """
    row = conn.execute(
        """
        SELECT o.snapshot_id
        FROM observations o JOIN source_snapshots s ON s.snapshot_id = o.snapshot_id
        WHERE o.area_id = ? AND o.entity_type = ?
        ORDER BY s.fetched_at DESC, s.snapshot_id DESC
        LIMIT 1
        """,
        (area_id, entity_type),
    ).fetchone()
    return row[0] if row else None


def latest_area_snapshots(conn: sqlite3.Connection, area_id: str) -> list[dict]:
    """Newest snapshot per source_name among those with observations for
    this area -- i.e. the snapshots the area's current figures rest on."""
    rows = conn.execute(
        """
        SELECT DISTINCT s.snapshot_id, s.source_name, s.fetched_at, s.data_as_of_date
        FROM source_snapshots s JOIN observations o ON o.snapshot_id = s.snapshot_id
        WHERE o.area_id = ?
        ORDER BY s.fetched_at DESC, s.snapshot_id DESC
        """,
        (area_id,),
    ).fetchall()
    latest: dict[str, dict] = {}
    for snapshot_id, source_name, fetched_at, data_as_of_date in rows:
        latest.setdefault(
            source_name,
            {"snapshot_id": snapshot_id, "source_name": source_name, "fetched_at": fetched_at,
             "data_as_of_date": data_as_of_date},
        )
    return list(latest.values())


def _load_pois(conn: sqlite3.Connection, area_id: str) -> list[dict] | None:
    """The area's current competitor set, or None if it has no POI
    snapshot at all (unknown -- not the same as an empty set)."""
    if latest_snapshot_id(conn, area_id, "poi") is None:
        return None
    rows = conn.execute(
        """
        SELECT o.snapshot_id, o.category, o.lat, o.lon, o.opening_hours_raw, o.payload_json, s.source_name
        FROM observations o JOIN source_snapshots s ON s.snapshot_id = o.snapshot_id
        WHERE o.area_id = ? AND o.entity_type = 'poi' AND o.snapshot_id = ?
        """,
        (area_id, latest_snapshot_id(conn, area_id, "poi")),
    ).fetchall()
    return [
        {
            "snapshot_id": r[0],
            "category": r[1],
            "lat": r[2],
            "lon": r[3],
            "opening_hours_raw": r[4],
            "payload": json.loads(r[5] or "{}"),
            "source_name": r[6],
        }
        for r in rows
    ]


def _load_population(conn: sqlite3.Connection, area_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT snapshot_id, payload_json
        FROM observations
        WHERE area_id = ? AND entity_type = 'lor_population' AND snapshot_id = ?
        """,
        (area_id, latest_snapshot_id(conn, area_id, "lor_population")),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[1])
    return {"snapshot_id": row[0], **payload}


def _compute_spatial(conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None) -> None:
    if pois is None:
        _write_missing(
            conn,
            node,
            [("competitor_count_total", "count", "current")]
            + [(f"competitor_count_{c.value}", "count", "current") for c in POICategory]
            + [
                ("competitor_density_per_km2", "count_per_km2", "current"),
                ("nearest_competitor_distance_m", "meters", "current"),
                ("mean_competitor_distance_m", "meters", "current"),
            ],
            SPATIAL_METHOD_VERSION,
            NO_POI_SNAPSHOT,
        )
        return
    poi_snapshot_ids = [p["snapshot_id"] for p in pois]
    total = len(pois)

    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="competitor_count_total",
        value=float(total),
        unit="count",
        period_label="current",
        method_version=SPATIAL_METHOD_VERSION,
        snapshot_ids=poi_snapshot_ids,
    )

    counts_by_category = {c.value: 0 for c in POICategory}
    for p in pois:
        counts_by_category[p["category"]] += 1
    for category, count in counts_by_category.items():
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name=f"competitor_count_{category}",
            value=float(count),
            unit="count",
            period_label="current",
            method_version=SPATIAL_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
        )

    area_km2 = math.pi * (node.radius_m / 1000.0) ** 2
    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="competitor_density_per_km2",
        value=total / area_km2,
        unit="count_per_km2",
        period_label="current",
        method_version=SPATIAL_METHOD_VERSION,
        snapshot_ids=poi_snapshot_ids,
    )

    if total == 0:
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="nearest_competitor_distance_m",
            value=None,
            unit="meters",
            period_label="current",
            method_version=SPATIAL_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
            missing_reason="no competitor POIs observed within radius",
        )
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="mean_competitor_distance_m",
            value=None,
            unit="meters",
            period_label="current",
            method_version=SPATIAL_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
            missing_reason="no competitor POIs observed within radius",
        )
    else:
        distances = [haversine_m(node.lat, node.lon, p["lat"], p["lon"]) for p in pois]
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="nearest_competitor_distance_m",
            value=min(distances),
            unit="meters",
            period_label="current",
            method_version=SPATIAL_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
        )
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="mean_competitor_distance_m",
            value=sum(distances) / len(distances),
            unit="meters",
            period_label="current",
            method_version=SPATIAL_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
        )


def _compute_nearest_direct_format(conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None) -> None:
    """Distance from the anchor to the nearest direct-format competitor
    (späti/kiosk, supermarket, fuel-station shop) -- the intercept risk
    that matters for a basket format. Scoring uses this instead of the
    nearest competitor of any kind, which sits 8-85m (P10-P90) from
    every inner-Berlin station exit and so only measures urban density.

    No direct-format competitor inside the radius is a *known* outcome,
    not a missing one: the value is written as the radius itself (a
    right-censored ">= radius" reading), which scores at least as well
    as any observed distance.
    """
    if pois is None:
        _write_missing(
            conn,
            node,
            [("nearest_direct_format_competitor_distance_m", "meters", "current")],
            SPATIAL_METHOD_VERSION,
            NO_POI_SNAPSHOT,
        )
        return
    poi_snapshot_ids = [p["snapshot_id"] for p in pois]
    distances = [
        haversine_m(node.lat, node.lon, p["lat"], p["lon"]) for p in pois if p["category"] in DIRECT_FORMAT_CATEGORIES
    ]
    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="nearest_direct_format_competitor_distance_m",
        value=min(distances) if distances else float(node.radius_m),
        unit="meters",
        period_label="current",
        method_version=SPATIAL_METHOD_VERSION,
        snapshot_ids=poi_snapshot_ids,
    )


def _compute_population(
    conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None, population: dict | None
) -> None:
    poi_snapshot_ids = [p["snapshot_id"] for p in pois or []]

    if population is None or population.get("population") is None:
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="resident_population",
            value=None,
            unit="persons",
            period_label="unknown",
            method_version=POPULATION_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
            missing_reason="no LOR population figure available for this node's Planungsraum",
        )
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="competitor_density_per_1000_residents",
            value=None,
            unit="count_per_1000_residents",
            period_label="unknown",
            method_version=POPULATION_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
            missing_reason="resident_population is unknown for this area",
        )
        return

    pop_value = population["population"]
    pop_snapshot_ids = poi_snapshot_ids + [population["snapshot_id"]]

    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="resident_population",
        value=float(pop_value),
        unit="persons",
        period_label=population.get("as_of_period", "unknown"),
        method_version=POPULATION_METHOD_VERSION,
        snapshot_ids=[population["snapshot_id"]],
    )

    if pop_value == 0:
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name="competitor_density_per_1000_residents",
            value=None,
            unit="count_per_1000_residents",
            period_label=population.get("as_of_period", "unknown"),
            method_version=POPULATION_METHOD_VERSION,
            snapshot_ids=pop_snapshot_ids,
            missing_reason="resident_population is 0 for this area; density undefined",
        )
        return

    if pois is None:
        _write_missing(
            conn,
            node,
            [("competitor_density_per_1000_residents", "count_per_1000_residents",
              population.get("as_of_period", "unknown"))],
            POPULATION_METHOD_VERSION,
            NO_POI_SNAPSHOT,
        )
        return

    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="competitor_density_per_1000_residents",
        value=len(pois) / (pop_value / 1000.0),
        unit="count_per_1000_residents",
        period_label=population.get("as_of_period", "unknown"),
        method_version=POPULATION_METHOD_VERSION,
        snapshot_ids=pop_snapshot_ids,
    )


def _compute_opening_hours(conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None) -> None:
    if pois is None:
        _write_missing(
            conn,
            node,
            [("competitor_hours_known_count", "count", "current"), ("competitor_hours_unknown_count", "count", "current")]
            + [
                (f"{prefix}_{window.label.value}", unit, period_label)
                for period_label, _ in _DAY_TYPES
                for window in DEFAULT_TIME_WINDOWS
                for prefix, unit in (("competitors_open", "count"), ("competitors_open_share", "ratio"))
            ],
            HOURS_METHOD_VERSION,
            NO_POI_SNAPSHOT,
        )
        return
    poi_snapshot_ids = [p["snapshot_id"] for p in pois]
    parsed = [parse_opening_hours(p["opening_hours_raw"]) for p in pois]

    known = sum(1 for p in parsed if p is not None)
    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="competitor_hours_known_count",
        value=float(known),
        unit="count",
        period_label="current",
        method_version=HOURS_METHOD_VERSION,
        snapshot_ids=poi_snapshot_ids,
    )
    _write_metric(
        conn,
        area_id=node.area_id,
        metric_name="competitor_hours_unknown_count",
        value=float(len(parsed) - known),
        unit="count",
        period_label="current",
        method_version=HOURS_METHOD_VERSION,
        snapshot_ids=poi_snapshot_ids,
    )

    for period_label, weekday in _DAY_TYPES:
        for window in DEFAULT_TIME_WINDOWS:
            open_count = sum(
                1 for p in parsed if is_open_during(p, weekday, window.start_hour, window.end_hour)
            )
            _write_metric(
                conn,
                area_id=node.area_id,
                metric_name=f"competitors_open_{window.label.value}",
                value=float(open_count),
                unit="count",
                period_label=period_label,
                method_version=HOURS_METHOD_VERSION,
                snapshot_ids=poi_snapshot_ids,
            )
            if len(pois) == 0:
                _write_metric(
                    conn,
                    area_id=node.area_id,
                    metric_name=f"competitors_open_share_{window.label.value}",
                    value=None,
                    unit="ratio",
                    period_label=period_label,
                    method_version=HOURS_METHOD_VERSION,
                    snapshot_ids=poi_snapshot_ids,
                    missing_reason="no competitor POIs observed within radius",
                )
            else:
                _write_metric(
                    conn,
                    area_id=node.area_id,
                    metric_name=f"competitors_open_share_{window.label.value}",
                    value=open_count / len(pois),
                    unit="ratio",
                    period_label=period_label,
                    method_version=HOURS_METHOD_VERSION,
                    snapshot_ids=poi_snapshot_ids,
                )


def count_peak_competitors(
    pois: list[dict], categories: tuple[str, ...], window_label: TimeWindowLabel
) -> tuple[int, int]:
    """Weekday commute-peak count (kept for the scoring_v4/v5 call sites):
    the matching day_types window with the given categories."""
    key = {TimeWindowLabel.MORNING_PEAK: "morning_peak", TimeWindowLabel.EVENING_PEAK: "evening_peak"}[window_label]
    w = next(w for w in COMPETITION_WINDOWS if w.key == key)
    count, closed = count_window_competitors(pois, CompetitionWindow(w.key, w.day_type, w.start_hour, w.end_hour,
                                                                     categories, w.label))
    return int(count), int(closed)


def _compute_peak_competitors(conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None) -> None:
    """Competition-pillar inputs (scoring_v6): competitors serving each
    window's basket -- weekday 7-9 and 16-19, weekend 10-19 and 22-02
    (day_types.COMPETITION_WINDOWS). Weekend counts average Saturday and
    Sunday and apply Berlin's Sunday trading law to competitors without
    known hours (day_types.poi_open)."""
    if pois is None:
        _write_missing(
            conn,
            node,
            [
                (f"{w.key}_{suffix}", "count", DAY_TYPE_PERIOD[w.day_type])
                for w in COMPETITION_WINDOWS
                for suffix in ("competitor_count", "known_closed_excluded")
            ],
            PEAK_COMPETITION_METHOD_VERSION,
            NO_POI_SNAPSHOT,
        )
        return
    poi_snapshot_ids = [p["snapshot_id"] for p in pois]
    for w in COMPETITION_WINDOWS:
        count, known_closed = count_window_competitors(pois, w)
        for suffix, value in (("competitor_count", count), ("known_closed_excluded", known_closed)):
            _write_metric(
                conn,
                area_id=node.area_id,
                metric_name=f"{w.key}_{suffix}",
                value=float(value),
                unit="count",
                period_label=DAY_TYPE_PERIOD[w.day_type],
                method_version=PEAK_COMPETITION_METHOD_VERSION,
                snapshot_ids=poi_snapshot_ids,
            )


def _employees_lower_bound(employees_range: str | None) -> int | None:
    """'10 - 19 Beschäftigte' -> 10, '0 Beschäftigte' -> 0, 'unbekannt' -> None."""
    if not employees_range:
        return None
    head = employees_range.split()[0]
    return int(head) if head.isdigit() else None


def _station_assigner(node: LocationInput):
    """reg -> True if the registration's nearest S/U station (straight
    line, VBB GTFS parent stations) is this node's own. Workers matter to
    a station store as commuters through it; inside a 400m circle, those
    nearer another station mostly get off there. Walking routes checked
    for every verified large employer agree with the straight-line pick
    (worker_onsite_verified.csv, station_check). None for a node with no
    known station (e.g. test fixtures): the whole circle counts."""
    from attraction_pipeline import NODE_STATIONS, rail_stations

    if node.area_id not in NODE_STATIONS:
        return None
    stations = rail_stations()
    mine = NODE_STATIONS[node.area_id][0]

    def uses_this_station(reg: dict) -> bool:
        if reg.get("lat") is None or reg.get("lon") is None:
            return True
        return min(stations, key=lambda n: min(haversine_m(reg["lat"], reg["lon"], a, b) for a, b in stations[n])) == mine

    return uses_this_station


def _compute_business_mix(conn: sqlite3.Connection, node: LocationInput) -> None:
    """IHK Gewerbedaten (Day 4 extra) -> daytime-workforce demand inputs.
    Size-class counts as before, plus (scoring_v5) people working here who
    commute through this node: size-class midpoints, large registrations
    replaced by their verified on-site headcount, and each registration
    assigned to its nearest S/U station (_station_assigner)."""
    rows = conn.execute(
        """
        SELECT snapshot_id, payload_json, lat, lon
        FROM observations
        WHERE area_id = ? AND entity_type = 'business' AND snapshot_id = ?
        """,
        (node.area_id, latest_snapshot_id(conn, node.area_id, "business")),
    ).fetchall()
    snapshot_ids = [r[0] for r in rows]

    if not rows:
        for metric_name, unit in (
            ("registered_business_count", "count"),
            ("businesses_10plus_employees_count", "count"),
            ("office_business_share", "ratio"),
            ("worker_headcount_estimate", "persons"),
            ("worker_headcount_low", "persons"),
            ("worker_headcount_high", "persons"),
            ("worker_headcount_as_registered", "persons"),
            ("worker_headcount_in_circle", "persons"),
            ("worker_headcount_other_station", "persons"),
            ("worker_headcount_unregistered_added", "persons"),
            ("worker_headcount_weekend", "persons"),
            ("worker_headcount_weekend_low", "persons"),
            ("worker_headcount_weekend_high", "persons"),
            ("worker_size_unknown_count", "count"),
            ("worker_large_unverified_count", "count"),
        ):
            _write_metric(
                conn,
                area_id=node.area_id,
                metric_name=metric_name,
                value=None,
                unit=unit,
                period_label="current",
                method_version=BUSINESS_MIX_METHOD_VERSION,
                snapshot_ids=snapshot_ids,
                missing_reason="no IHK Gewerbedaten observations for this area",
            )
        return

    payloads = [{**json.loads(r[1]), "lat": r[2], "lon": r[3]} for r in rows]
    total = len(payloads)
    sized = sum(
        1
        for p in payloads
        if (lb := _employees_lower_bound(p.get("employees_range"))) is not None and lb >= WORKPLACE_MIN_EMPLOYEES
    )
    office = sum(1 for p in payloads if p.get("branch_top_level_id") in OFFICE_NACE_DIVISIONS)
    # scoring_v5 demand input: people working in the circle -- size
    # classes at their midpoint, except large registrations, which use
    # their hand-verified on-site headcount (demand_model.workers_onsite,
    # db/raw/calibration/worker_onsite_verified.csv). Unknown sizes are
    # counted separately, never imputed.
    unregistered = [
        e for e in load_unregistered_employers()
        if e["area_id"] == node.area_id and haversine_m(node.lat, node.lon, e["lat"], e["lon"]) <= node.radius_m
    ]
    w = workers_onsite(payloads, load_onsite_verifications(), _station_assigner(node), unregistered)

    for metric_name, value, unit in (
        ("registered_business_count", float(total), "count"),
        ("businesses_10plus_employees_count", float(sized), "count"),
        ("office_business_share", office / total, "ratio"),
        ("worker_headcount_estimate", w["central"], "persons"),
        ("worker_headcount_low", w["low"], "persons"),
        ("worker_headcount_high", w["high"], "persons"),
        ("worker_headcount_as_registered", w["as_registered"], "persons"),
        ("worker_headcount_in_circle", w["in_circle"], "persons"),
        ("worker_headcount_other_station", w["other_station"], "persons"),
        ("worker_headcount_unregistered_added", w["unregistered_added"], "persons"),
        ("worker_headcount_weekend", w["weekend_central"], "persons"),
        ("worker_headcount_weekend_low", w["weekend_low"], "persons"),
        ("worker_headcount_weekend_high", w["weekend_high"], "persons"),
        ("worker_size_unknown_count", float(w["unknown_size"]), "count"),
        ("worker_large_unverified_count", float(len(w["large_unverified"])), "count"),
    ):
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name=metric_name,
            value=value,
            unit=unit,
            period_label="current",
            method_version=BUSINESS_MIX_METHOD_VERSION,
            snapshot_ids=snapshot_ids,
        )


def _latest_snapshot(conn: sqlite3.Connection, source_name: str) -> tuple[int, str] | None:
    row = conn.execute(
        """
        SELECT snapshot_id, raw_file_ref FROM source_snapshots
        WHERE source_name = ? AND raw_file_ref IS NOT NULL
        ORDER BY fetched_at DESC, snapshot_id DESC LIMIT 1
        """,
        (source_name,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def _compute_visitors(conn: sqlite3.Connection, node: LocationInput) -> None:
    """Visitors per day reached via this node's station: the latest
    attraction-gateway snapshot (pageviews and 1/k shares, written by
    attraction_pipeline.py) converted with the latest visitor fit
    (calibrate_visitors.py). No API call. The fit file is named in the
    method version so every figure traces to the exact conversion used."""
    snapshot_id = latest_snapshot_id(conn, node.area_id, "attraction")
    fit = latest_visitor_fit()
    if snapshot_id is None or fit is None:
        reason = ("no attraction-gateway snapshot for this area" if snapshot_id is None
                  else "no visitor fit on record (run calibrate_visitors.py)")
        _write_missing(conn, node, [(n, "persons_per_day", "unknown") for name in VISITOR_METRICS.values()
                                    for n in (name, name.replace("_estimate", "_from_pageviews"))],
                       VISITOR_METHOD_VERSION, reason)
        return
    fit_data, fit_path = fit
    records = [
        json.loads(r[0]) for r in conn.execute(
            "SELECT payload_json FROM observations WHERE area_id = ? AND entity_type = 'attraction' AND snapshot_id = ?",
            (node.area_id, snapshot_id),
        )
    ]
    # Opening hours come from the snapshot's raw Overpass file (the venue's
    # OSM tags); a venue without them follows day_types.venue_open_days.
    raw_ref = conn.execute("SELECT raw_file_ref FROM source_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()[0]
    tags_by_ref = {}
    if raw_ref and (PROJECT_ROOT / raw_ref).exists():
        raw = json.loads((PROJECT_ROOT / raw_ref).read_text())
        tags_by_ref = {f"{e['type']}/{e['id']}": e.get("tags", {}) for e in raw.get("overpass", {}).get("elements", [])
                       if e.get("type") != "count"}
    for r in records:
        r["opening_hours_raw"] = tags_by_ref.get(r.get("osm_ref"), {}).get("opening_hours")
    period = f"pageviews {fit_data['pageview_window'][0]}..{fit_data['pageview_window'][1]} (end exclusive)"
    known = load_known_attendance(fit_data)
    for day_type, name in VISITOR_METRICS.items():
        from_known, from_pageviews, _ = visitors_split(records, fit_data, day_type, known)
        for metric_name, value in ((name, from_known + from_pageviews),
                                   (name.replace("_estimate", "_from_pageviews"), from_pageviews)):
            _write_metric(
                conn,
                area_id=node.area_id,
                metric_name=metric_name,
                value=value,
                unit="persons_per_day",
                period_label=period,
                method_version=f"{VISITOR_METHOD_VERSION}:{fit_path.stem}",
                snapshot_ids=[snapshot_id],
            )


# Above this share of the circle in Planungsräume without an MSS figure,
# the estimate is written missing rather than treating them as empty.
MAX_UNPOPULATED_SHARE = 0.05


def catchment_population(lat: float, lon: float, radius_m: float, boundaries: dict, pop_index: dict,
                         anchor_plr: str | None = None) -> dict:
    """Pure areal apportionment of Planungsraum populations onto a circle:
    {estimate, anchor_share, unpopulated_share, periods}. Shared with
    calibrate_demand.py so reference stations are counted identically."""
    from shapely.geometry import Point, shape
    from shapely.ops import transform

    # Local equirectangular projection to meters around the anchor --
    # distortion over a 400m radius is far below the precision of the
    # population figures being apportioned.
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0

    def to_local_m(x, y, z=None):
        return ((x - lon) * kx, (y - lat) * ky)

    circle = Point(0, 0).buffer(radius_m, resolution=64)
    estimate = 0.0
    anchor_share = None
    unpopulated_share = 0.0
    periods: set[str] = set()
    for feature in boundaries["features"]:
        polygon = transform(to_local_m, shape(feature["geometry"]))
        if not polygon.intersects(circle):
            continue
        overlap = polygon.intersection(circle).area
        plr_id = feature["properties"]["plr_id"]
        if plr_id == anchor_plr:
            anchor_share = overlap / circle.area
        record = pop_index.get(plr_id)
        if record is None or record["population"] is None:
            unpopulated_share += overlap / circle.area
            continue
        estimate += record["population"] * overlap / polygon.area
        periods.add(str(record["as_of_period"]))
    return {"estimate": estimate, "anchor_share": anchor_share, "unpopulated_share": unpopulated_share,
            "periods": periods}


def _compute_catchment_population(conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None) -> None:
    """Area-weighted resident estimate for the node's own 400m circle.

    Day 3's resident_population is the *anchor's* Planungsraum only,
    but that Planungsraum covers just 25-60% of each 400m circle -- so
    dividing a 400m competitor count by it mixes two catchments. This
    re-reads the Day 2 raw LOR/MSS files (no API call), intersects the
    circle with every Planungsraum it touches, and apportions each
    Planungsraum's population by intersected area. Assumes uniform
    density within a Planungsraum -- a stated simplification, and the
    reason anchor_plr_catchment_share is written alongside it.
    """
    from lor_client import population_by_plr_id

    boundaries_snap = _latest_snapshot(conn, "berlin_lor_boundaries")
    population_snap = _latest_snapshot(conn, "berlin_mss_population")
    metric_names = (
        ("catchment_population_estimate", "persons"),
        ("anchor_plr_catchment_share", "ratio"),
        ("competitor_density_per_1000_catchment_residents", "count_per_1000_residents"),
    )
    if boundaries_snap is None or population_snap is None:
        for metric_name, unit in metric_names:
            _write_metric(
                conn,
                area_id=node.area_id,
                metric_name=metric_name,
                value=None,
                unit=unit,
                period_label="unknown",
                method_version=CATCHMENT_POPULATION_METHOD_VERSION,
                snapshot_ids=[],
                missing_reason="no LOR boundary or MSS population snapshot with a raw file on record",
            )
        return

    snapshot_ids = [boundaries_snap[0], population_snap[0]]
    boundaries = json.loads((PROJECT_ROOT / boundaries_snap[1]).read_text())
    pop_index = population_by_plr_id(json.loads((PROJECT_ROOT / population_snap[1]).read_text()))
    anchor_plr = conn.execute("SELECT lor_code FROM areas WHERE area_id = ?", (node.area_id,)).fetchone()[0]

    c = catchment_population(node.lat, node.lon, node.radius_m, boundaries, pop_index, anchor_plr)
    estimate, anchor_share, unpopulated_share = c["estimate"], c["anchor_share"], c["unpopulated_share"]
    period_label = ",".join(sorted(c["periods"])) or "unknown"
    if unpopulated_share > MAX_UNPOPULATED_SHARE:
        # Never silently treat a Planungsraum with no figure as 0 residents.
        reason = f"{unpopulated_share:.0%} of the catchment falls in Planungsräume with no MSS population figure"
        _write_metric(
            conn, area_id=node.area_id, metric_name="catchment_population_estimate", value=None,
            unit="persons", period_label=period_label, method_version=CATCHMENT_POPULATION_METHOD_VERSION,
            snapshot_ids=snapshot_ids, missing_reason=reason,
        )
        _write_metric(
            conn, area_id=node.area_id, metric_name="competitor_density_per_1000_catchment_residents",
            value=None, unit="count_per_1000_residents", period_label=period_label,
            method_version=CATCHMENT_POPULATION_METHOD_VERSION, snapshot_ids=snapshot_ids, missing_reason=reason,
        )
    else:
        _write_metric(
            conn, area_id=node.area_id, metric_name="catchment_population_estimate", value=estimate,
            unit="persons", period_label=period_label, method_version=CATCHMENT_POPULATION_METHOD_VERSION,
            snapshot_ids=snapshot_ids,
        )
        if pois is None:
            reason = NO_POI_SNAPSHOT
        elif estimate == 0:
            reason = "catchment population estimate is 0; density undefined"
        else:
            reason = None
        if reason:
            _write_metric(
                conn, area_id=node.area_id, metric_name="competitor_density_per_1000_catchment_residents",
                value=None, unit="count_per_1000_residents", period_label=period_label,
                method_version=CATCHMENT_POPULATION_METHOD_VERSION, snapshot_ids=snapshot_ids, missing_reason=reason,
            )
        else:
            _write_metric(
                conn, area_id=node.area_id, metric_name="competitor_density_per_1000_catchment_residents",
                value=len(pois) / (estimate / 1000.0), unit="count_per_1000_residents", period_label=period_label,
                method_version=CATCHMENT_POPULATION_METHOD_VERSION,
                snapshot_ids=snapshot_ids + [p["snapshot_id"] for p in pois],
            )

    if anchor_share is None:
        _write_metric(
            conn, area_id=node.area_id, metric_name="anchor_plr_catchment_share", value=None, unit="ratio",
            period_label="current", method_version=CATCHMENT_POPULATION_METHOD_VERSION,
            snapshot_ids=snapshot_ids, missing_reason="areas.lor_code not set or not found among boundaries",
        )
    else:
        _write_metric(
            conn, area_id=node.area_id, metric_name="anchor_plr_catchment_share", value=anchor_share,
            unit="ratio", period_label="current", method_version=CATCHMENT_POPULATION_METHOD_VERSION,
            snapshot_ids=snapshot_ids,
        )


def _compute_source_coverage(conn: sqlite3.Connection, node: LocationInput, pois: list[dict] | None) -> None:
    """Provenance metrics of the conflated competitor set.

    - competitor_count_in_station: competitors within STATION_RADIUS_M
      of a station anchor (concourse/platform retail).
    - competitor_count_corroborated: competitors seen by >= 2
      independent upstream providers -- the evidence-quality view of
      the same count.
    - osm_coverage_share: share of competitors OSM knows about at all;
      1 - this is the measured OSM under-mapping in this circle.

    Only defined for a conflated set; a pre-v2 single-source OSM set has
    no provenance, so these are written missing rather than as 0.
    """
    poi_snapshot_ids = [p["snapshot_id"] for p in pois or []]
    names = (
        ("competitor_count_in_station", "count"),
        ("competitor_count_corroborated", "count"),
        ("osm_coverage_share", "ratio"),
    )
    if not pois or any(p["source_name"] != CONFLATED_SOURCE_NAME for p in pois):
        if pois is None:
            reason = NO_POI_SNAPSHOT
        elif not pois:
            reason = "no competitor POIs observed within radius"
        else:
            reason = "competitor set predates multi-source conflation (no provenance recorded)"
        for metric_name, unit in names:
            _write_metric(
                conn,
                area_id=node.area_id,
                metric_name=metric_name,
                value=None,
                unit=unit,
                period_label="current",
                method_version=CONFLATION_METHOD_VERSION,
                snapshot_ids=poi_snapshot_ids,
                missing_reason=reason,
            )
        return

    values = (
        float(sum(1 for p in pois if p["payload"].get("in_station"))),
        float(sum(1 for p in pois if p["payload"].get("n_providers", 0) >= 2)),
        sum(1 for p in pois if "osm_overpass" in p["payload"].get("sources", [])) / len(pois),
    )
    for (metric_name, unit), value in zip(names, values):
        _write_metric(
            conn,
            area_id=node.area_id,
            metric_name=metric_name,
            value=value,
            unit=unit,
            period_label="current",
            method_version=CONFLATION_METHOD_VERSION,
            snapshot_ids=poi_snapshot_ids,
        )


def compute_area(conn: sqlite3.Connection, node: LocationInput) -> dict:
    pois = _load_pois(conn, node.area_id)
    population = _load_population(conn, node.area_id)

    _compute_spatial(conn, node, pois)
    _compute_nearest_direct_format(conn, node, pois)
    _compute_source_coverage(conn, node, pois)
    _compute_population(conn, node, pois, population)
    _compute_opening_hours(conn, node, pois)
    _compute_peak_competitors(conn, node, pois)
    _compute_business_mix(conn, node)
    _compute_catchment_population(conn, node, pois)
    _compute_visitors(conn, node)

    return {
        "area_id": node.area_id,
        "competitor_count": None if pois is None else len(pois),
        "population": population.get("population") if population else None,
    }


def run(db_path: Path = DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    results = []
    try:
        for node in CANDIDATE_NODES:
            result = compute_area(conn, node)
            print(
                f"[{result['area_id']}] {result['competitor_count']} competitors, "
                f"population={result['population']}"
            )
            results.append(result)
        conn.commit()
    finally:
        conn.close()
    return results


if __name__ == "__main__":
    run()
