"""Day 5: scoring layer + weight-sensitivity analysis.

Two scores per area, deliberately never combined into one number:

  - business_fit (0-100): how well the public data match the store
    hypothesis (store_hypothesis.md) -- demand, and competition per
    unit of that demand in each commute peak.
  - evidence_quality (0-100): how much the business_fit number can be
    trusted -- completeness, freshness, and sample size of what it was
    computed from.

A high fit on thin evidence is a "verify first" candidate, not a
winner; folding the two into one weighted total would hide exactly the
distinction the report needs to make.

Everything here is Program work (project_plan_v3.md, "Division of
Responsibilities"): reads the latest area_metrics rows, no API calls,
no LLM judgment. Every criterion value carries the metric_ids it was
derived from so the report can cite and re-verify each figure.

Scales are *anchored* (fixed worst/best reference values), not min-max
across the three candidates: with n=3, min-max always hands one node
0 and one node 100 on every criterion, exaggerating small gaps and
making scores shift whenever the candidate set changes. Min-max is
still run once, in sensitivity_analysis(), as a method-robustness
check.
"""

from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Callable

from demand_model import latest_visitor_fit
from metrics_pipeline import latest_area_snapshots, latest_snapshot_id

SCORING_MODEL_VERSION = "scoring_v7"

WEEKDAY = "weekday_We"
WEEKEND = "weekend_SaSu"

# Pillar weights for business_fit: demand (people per day) and
# competition per 10,000 of those people, in each commute peak. Equal
# weights: demand also enters the competition pillar as its denominator,
# so a demand lead is not under-weighted at 50/50.
DEFAULT_WEIGHTS: dict[str, float] = {
    "demand": 0.50,
    "competition": 0.50,
}

# Named alternative weightings for the scenario part of the
# sensitivity analysis -- each one a plausible stakeholder emphasis,
# not a tuned optimum.
SCENARIO_WEIGHTS: dict[str, dict[str, float]] = {
    "baseline": DEFAULT_WEIGHTS,
    "demand_led": {"demand": 0.65, "competition": 0.35},
    "competition_led": {"demand": 0.35, "competition": 0.65},
    "demand_heavy": {"demand": 0.80, "competition": 0.20},
    "competition_heavy": {"demand": 0.20, "competition": 0.80},
}

# Evidence-quality tiers used by the report's decision matrix.
EQ_ADEQUATE = 85.0
EQ_VERIFY_FIRST = 70.0

# Competition per unit demand: a peak's competitor count per this many
# people per day in the catchment (residents + workers + visitors).
PEOPLE_UNIT = 10_000

# The three demand segments per day type, summed in one unit
# (demand_model.py, day_types.py). A segment that is missing makes the
# total missing -- never summed as 0. Residents count fully on both day
# types; workers and visitors differ (commuters mostly absent at the
# weekend, clubs open only Friday-Sunday nights).
DEMAND_SEGMENTS_BY_DAY: dict[str, tuple[tuple[str, str], ...]] = {
    "weekday": (
        ("residents", "catchment_population_estimate"),
        ("workers", "worker_headcount_estimate"),
        ("visitors", "visitor_weekday_estimate"),
    ),
    "weekend": (
        ("residents", "catchment_population_estimate"),
        ("workers", "worker_headcount_weekend"),
        ("visitors", "visitor_weekend_estimate"),
    ),
}
DEMAND_SEGMENTS = DEMAND_SEGMENTS_BY_DAY["weekday"]

# Freshness is judged on how old the *data* are (data_as_of_date), not
# on when they were fetched: a 2024 population figure fetched yesterday
# is not fresh, and a monthly-refreshed register fetched three weeks ago
# is not stale. Max age per source, in days:
#   - live / monthly sources (OSM, Overture, FSQ, IHK, and the conflated
#     set derived from them): 90 -- one quarter of drift;
#   - attraction gateways: 365 -- 12-month pageview window, sights change slowly;
#   - MSS population: 1095 -- a biennial edition published ~11 months
#     after its reference date, so the newest available figure is
#     routinely up to ~3 years old.
# LOR boundaries are a versioned reference geometry (current until
# replaced), so they are not aged at all.
FRESHNESS_MAX_DATA_AGE_DAYS: dict[str, int] = {
    "osm_overpass": 90,
    "overture_places": 90,
    "fsq_os_places": 90,
    "poi_conflated": 90,
    "ihk_gewerbedaten": 90,
    "osm_attractions_gateway": 365,
    "berlin_mss_population": 1095,
}
DEFAULT_MAX_DATA_AGE_DAYS = 90
VERSIONED_REFERENCE_SOURCES = frozenset({"berlin_lor_boundaries"})

# Cross-source consistency check: OSM competitors per IHK-registered
# Gastronomie business in the same 400m. Not a coverage *rate* (IHK
# Gastronomie also holds restaurants and bars outside the competitor
# taxonomy, and never sums with OSM) -- only compared across nodes, so
# it is the one evidence component scored relative to the best node.
IHK_GASTRO_BRANCH = "Gastronomie"
UNDER_MAPPING_RATIO = 0.5

MONTE_CARLO_DRAWS = 10_000
MONTE_CARLO_SEED = 7


# --------------------------------------------------------------------------
# Metric access
# --------------------------------------------------------------------------


def latest_metrics(conn: sqlite3.Connection, area_id: str) -> dict[tuple[str, str], dict]:
    """(metric_name, period_label) -> latest row. area_metrics is
    append-only, so older runs are ignored here, not deleted."""
    rows = conn.execute(
        """
        SELECT metric_id, metric_name, period_label, metric_value, is_missing,
               missing_reason, unit, computation_method_version, source_snapshot_ids_json, computed_at
        FROM area_metrics
        WHERE area_id = ?
        ORDER BY computed_at ASC, metric_id ASC
        """,
        (area_id,),
    ).fetchall()
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        latest[(r[1], r[2])] = {
            "metric_id": r[0],
            "metric_name": r[1],
            "period_label": r[2],
            "value": r[3],
            "is_missing": bool(r[4]),
            "missing_reason": r[5],
            "unit": r[6],
            "method_version": r[7],
            "source_snapshot_ids_json": r[8],
            "computed_at": r[9],
        }
    return latest


def _get(metrics: dict, name: str, period: str | None = None) -> dict | None:
    """Fetch one metric by name, matching any period_label if none given
    (e.g. population's period is its data vintage, '202412')."""
    if period is not None:
        return metrics.get((name, period))
    matches = [m for (n, _), m in metrics.items() if n == name]
    return max(matches, key=lambda m: m["metric_id"]) if matches else None


@dataclass(frozen=True)
class Derived:
    value: float | None
    metric_ids: tuple[int, ...]
    missing_reason: str | None = None


def _single(name: str, period: str | None = None) -> Callable[[dict], Derived]:
    def derive(metrics: dict) -> Derived:
        m = _get(metrics, name, period)
        if m is None:
            return Derived(None, (), f"{name} not computed")
        if m["is_missing"]:
            return Derived(None, (m["metric_id"],), m["missing_reason"])
        return Derived(m["value"], (m["metric_id"],))

    return derive


def _daily_people(metrics: dict, day_type: str = "weekday") -> Derived:
    """Residents + workers + visitors per day of a day type, all in persons."""
    segments = DEMAND_SEGMENTS_BY_DAY[day_type]
    rows = [_get(metrics, name) for _, name in segments]
    ids = tuple(r["metric_id"] for r in rows if r)
    for (segment, name), r in zip(segments, rows):
        if r is None:
            return Derived(None, ids, f"{segment} ({name}) not computed")
        if r["is_missing"]:
            return Derived(None, ids, f"{segment}: {r['missing_reason']}")
    return Derived(sum(r["value"] for r in rows), ids)


def demand_breakdown(metrics: dict, day_type: str = "weekday") -> dict[str, float | None]:
    """{segment: people per day} for the report; None where missing."""
    out = {}
    for segment, name in DEMAND_SEGMENTS_BY_DAY[day_type]:
        r = _get(metrics, name)
        out[segment] = None if r is None or r["is_missing"] else r["value"]
    return out


@dataclass(frozen=True)
class Criterion:
    key: str
    pillar: str
    label: str
    unit: str
    derive: Callable[[dict], Derived]
    worst: float  # raw value that maps to 0
    best: float  # raw value that maps to 100
    rationale: str
    # Normalize on log10(1 + x) instead of x -- for heavy-tailed signals
    # where the three nodes differ by orders of magnitude.
    log_scale: bool = False
    # Relative weight inside its pillar (weighted mean over available criteria).
    weight: float = 1.0
    # Competition per unit demand: the derived count is divided by the
    # node's people per day (in PEOPLE_UNITs) before normalizing.
    per_demand: bool = False
    # Which day type's people this criterion measures or is divided by.
    day_type: str = "weekday"

    def transform(self, value: float) -> float:
        return math.log10(1.0 + max(value, 0.0)) if self.log_scale else value

    def normalize(self, value: float) -> float:
        lo, hi = self.transform(self.worst), self.transform(self.best)
        return max(0.0, min(100.0, (self.transform(value) - lo) / (hi - lo) * 100.0))


# Both scales are measured, not judged. calibrate_anchors.py records each
# inner-Berlin S/U reference station's morning- and evening-peak
# competitor counts; calibrate_demand.py converts the same stations'
# residents, workers and visitors into people per day with the
# candidates' own method. Each criterion maps P10 / P90 of that
# reference distribution to 0 / 100 (or 100 / 0 where fewer is better),
# so a score reads as "busier / less contested than roughly X% of
# inner-city transit nodes".
# scoring_v7: competition anchors re-run on the same 139 stations and the
# same Overture release with conflation_v2 (near-duplicate merge, name /
# category check, verified corrections); demand anchors unchanged.
# scoring_v6 run: all 139 stations complete for all six criteria (OSM
# cache 2026-09-28/10-01, Overture 2026-09-23.1, IHK with NACE divisions
# fetched 2026-10-02, visitor fit visitor_fit_20261001T130205Z, pageviews
# 2025-09..2026-09; known attendance -- published counts and
# venue_attendance_verified.csv -- used instead of the fame conversion).
COMPETITION_CALIBRATION_REF = "db/raw/calibration/competition_anchors_20261003T153748Z.json"
DEMAND_CALIBRATION_REF = "db/raw/calibration/demand_anchors_20261003T153754Z.json"
_PEOPLE_RATIONALE = (
    "Residents: area-weighted MSS 2025 population of the 400m circle (both day types). Workers: IHK registrations "
    "whose nearest S/U station is this node, size classes at their geometric midpoint, large employers at their "
    "verified on-site headcount; at the weekend x the sector's weekend-work share (Destatis 2023) x 0.5. Visitors: "
    "~10.3 visitors per annual Wikipedia pageview of every attraction this station is a gateway for, x its 1/k "
    "gateway share, spread over the venue's open days (clubs without OSM hours: Friday/Saturday nights only). "
)
BUSINESS_FIT_CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "daily_people", "demand", "People per weekday in the catchment (residents + workers + visitors)",
        "persons/day", lambda m: _daily_people(m, "weekday"), worst=7_200, best=28_200,
        rationale=_PEOPLE_RATIONALE + "Anchors: P10 / P90 of 139 inner-Berlin S/U stations (7,192 / 28,181 people "
        "per weekday).",
        weight=5.0, day_type="weekday",
    ),
    Criterion(
        "daily_people_weekend", "demand", "People per weekend/holiday day in the catchment", "persons/day",
        lambda m: _daily_people(m, "weekend"), worst=5_400, best=13_300,
        rationale="Same three segments on an average Saturday/Sunday. Anchors: P10 / P90 of 139 inner-Berlin S/U "
        "stations (5,392 / 13,299 people per weekend day).",
        weight=2.0, day_type="weekend",
    ),
    Criterion(
        "morning_peak_competition", "competition",
        "Weekday 7–9 competitors per 10,000 weekday people (bakery, café, supermarket, fuel shop)", "per 10k",
        _single("morning_peak_competitor_count", WEEKDAY), worst=33.4, best=5.9,
        rationale="Breakfast / coffee-to-go set a commuter passes 7-9. Shops whose OSM hours show them closed are "
        "excluded; unknown hours count as open. Anchors: P90 / P10 of 139 inner-Berlin S/U stations (33.4 / 5.9).",
        per_demand=True, weight=2.5, day_type="weekday",
    ),
    Criterion(
        "evening_peak_competition", "competition",
        "Weekday 16–19 competitors per 10,000 weekday people (späti, fast food, supermarket, fuel shop)", "per 10k",
        _single("evening_peak_competitor_count", WEEKDAY), worst=35.4, best=5.7,
        rationale="After-work top-up / snack set 16-19, same rules. Anchors: P90 / P10 of 139 inner-Berlin S/U "
        "stations (35.4 / 5.7).",
        per_demand=True, weight=2.5, day_type="weekday",
    ),
    Criterion(
        "weekend_day_competition", "competition",
        "Weekend 10–19 competitors per 10,000 weekend people (bakery, café, supermarket, späti, fast food, fuel shop)",
        "per 10k", _single("weekend_day_competitor_count", WEEKEND), worst=90.5, best=14.3,
        rationale="Average of Saturday and Sunday. Unknown-hours retail follows Berlin's Sunday law: supermarkets, "
        "spätis, beverage and chemist shops closed on Sunday unless inside a station; bakeries 7-16. "
        "Anchors: P90 / P10 of 139 inner-Berlin S/U stations (90.5 / 14.3).",
        per_demand=True, weight=1.0, day_type="weekend",
    ),
    Criterion(
        "weekend_night_competition", "competition",
        "Weekend 22–02 competitors per 10,000 weekend people (späti, fast food, fuel shop)", "per 10k",
        _single("weekend_night_competitor_count", WEEKEND), worst=31, best=3.5,
        rationale="Late-night basket after clubs and events, Saturday and Sunday nights averaged, same Sunday rule. "
        "Unknown-hours fast food counts as open, so the night count is an upper bound. "
        "Anchors: P90 / P10 of 139 inner-Berlin S/U stations (31.2 / 3.5).",
        per_demand=True, weight=1.0, day_type="weekend",
    ),
)

PILLARS: tuple[str, ...] = ("demand", "competition")

# Structural check: the same four competitor counts scored as they are
# (competitors in the 400m circle), with no demand denominator. Anchors
# are P90 / P10 of the raw counts at the same 139 reference stations
# (COMPETITION_CALIBRATION_REF). Used only by
# competition_structure_check(); the baseline score never reads them.
ABSOLUTE_COMPETITION_ANCHORS: dict[str, tuple[float, float]] = {
    "morning_peak_competition": (53.2, 8.0),
    "evening_peak_competition": (57.2, 7.0),
    "weekend_day_competition": (96.0, 12.3),
    "weekend_night_competition": (35.6, 2.9),
}

# Demand dimensions in store_hypothesis.md that no v1 source measures.
# Listed so the report states them as unscored, rather than letting an
# absent criterion read as a zero.
UNSCORED_DIMENSIONS: tuple[str, ...] = (
    "Measured visitor footfall -- visitors are estimated from Wikipedia fame through a fit on ticketed "
    "attractions; free open-air landmarks (Brandenburger Tor) have no count and are likely under-estimated.",
    "Pass-through passengers -- people changing trains without leaving the station are in no segment, "
    "and station passenger counts by time window are not published per node.",
    "Rent, storefront availability, operating restrictions, supply chain -- outside v1 scope.",
)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _result(c: Criterion, value: float | None, metric_ids, missing_reason: str | None, **extra) -> dict:
    return {
        "key": c.key,
        "pillar": c.pillar,
        "day_type": c.day_type,
        "label": c.label,
        "unit": c.unit,
        "raw_value": value,
        "score": None if value is None else c.normalize(value),
        "weight": c.weight,
        "metric_ids": list(metric_ids),
        "missing_reason": missing_reason,
        **extra,
    }


def _criterion_results(metrics: dict, visitor_factor: float = 1.0, workers: float | None = None,
                       workers_weekend: float | None = None) -> list[dict]:
    """Demand first; then competition per PEOPLE_UNIT of the matching day
    type's people. Output keeps BUSINESS_FIT_CRITERIA order.
    visitor_factor rescales visitors on both day types for the
    visitor-conversion check (the visitor model is proportional, so a
    different visitors-per-pageview ratio is exactly a rescaling).
    workers / workers_weekend replace the worker segment for the on-site
    verification check."""
    people: dict[str, Derived] = {}
    breakdowns: dict[str, dict] = {}
    for day_type, override in (("weekday", workers), ("weekend", workers_weekend)):
        p = _daily_people(metrics, day_type)
        b = demand_breakdown(metrics, day_type)
        if p.value is not None:
            # Only the pageview-converted part of visitors moves with the
            # conversion ratio; known attendance (published / capacity) does not.
            converted = _get(metrics, f"visitor_{day_type}_from_pageviews")
            convertible = converted["value"] if converted and not converted["is_missing"] else b["visitors"]
            adjust = convertible * (visitor_factor - 1.0)
            if override is not None:
                adjust += override - b["workers"]
            p = Derived(p.value + adjust, p.metric_ids)
        people[day_type], breakdowns[day_type] = p, b
    by_key: dict[str, dict] = {}
    for c in BUSINESS_FIT_CRITERIA:
        pp = people[c.day_type]
        if c.per_demand:
            d = c.derive(metrics)
            if d.value is None:
                by_key[c.key] = _result(c, None, d.metric_ids, d.missing_reason, count=None, people=pp.value)
            elif not pp.value:
                reason = pp.missing_reason or "0 people per day"
                by_key[c.key] = _result(c, None, d.metric_ids, f"competitors per unit demand undefined ({reason})",
                                        count=d.value, people=pp.value)
            else:
                ratio = d.value / (pp.value / PEOPLE_UNIT)
                by_key[c.key] = _result(c, ratio, tuple(d.metric_ids) + pp.metric_ids, None,
                                        count=d.value, people=pp.value)
        elif c.pillar == "demand":
            by_key[c.key] = _result(c, pp.value, pp.metric_ids, pp.missing_reason, breakdown=breakdowns[c.day_type])
        else:
            d = c.derive(metrics)
            by_key[c.key] = _result(c, d.value, d.metric_ids, d.missing_reason)
    return [by_key[c.key] for c in BUSINESS_FIT_CRITERIA]


def pillar_scores(criteria: list[dict]) -> dict[str, float | None]:
    """Weighted mean of available criteria per pillar (Criterion.weight;
    equal weights unless set). A missing criterion is dropped and the
    remaining weights renormalized (flagged by the caller), never scored 0."""
    out: dict[str, float | None] = {}
    for pillar in PILLARS:
        avail = [(c["score"], c.get("weight", 1.0)) for c in criteria if c["pillar"] == pillar and c["score"] is not None]
        total_w = sum(w for _, w in avail)
        out[pillar] = sum(s * w for s, w in avail) / total_w if avail else None
    return out


def business_fit(pillars: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted mean over pillars that have a score, renormalizing the
    weights. Returns None if no pillar is scorable at all."""
    available = {p: s for p, s in pillars.items() if s is not None}
    total_w = sum(weights[p] for p in available)
    if total_w == 0:
        return None
    return sum(weights[p] * s for p, s in available.items()) / total_w


def snapshot_freshness(snap: dict, today: date) -> tuple[bool | None, str]:
    """(is_fresh, detail) for one backing snapshot, judged on data age.
    is_fresh is None for versioned reference sources, which don't age."""
    source = snap["source_name"]
    if source in VERSIONED_REFERENCE_SOURCES:
        return None, f"{source}: versioned reference, not aged"
    limit = FRESHNESS_MAX_DATA_AGE_DAYS.get(source, DEFAULT_MAX_DATA_AGE_DAYS)
    try:
        as_of = date.fromisoformat(str(snap["data_as_of_date"])[:10])
    except ValueError:
        return False, f"{source}: data-as-of date {snap['data_as_of_date']!r} unparseable"
    age = (today - as_of).days
    return age <= limit, f"{source}: data as of {as_of.isoformat()} ({age}d, limit {limit}d)"


def evidence_quality(conn: sqlite3.Connection, area_id: str, metrics: dict, criteria: list[dict], now: datetime) -> dict:
    """Equally weighted components, each 0-1. Kept separate from
    business_fit on purpose -- see module docstring. A component whose
    input is missing scores 0, not dropped: dropping it would *raise*
    the mean exactly when the evidence is thinnest."""
    components: dict[str, dict] = {}

    # The competition pillar rests on competitor counts; how many of them
    # at least two independent providers confirm is how far to trust them.
    corroborated = _get(metrics, "competitor_count_corroborated", "current")
    total = _get(metrics, "competitor_count_total", "current")
    if corroborated and total and not corroborated["is_missing"] and not total["is_missing"] and total["value"] > 0:
        components["competitor_corroboration"] = {
            "value": corroborated["value"] / total["value"],
            "detail": f"{corroborated['value']:.0f}/{total['value']:.0f} competitors seen by >= 2 independent providers",
        }
    else:
        reason = (total or {}).get("missing_reason") or (corroborated or {}).get("missing_reason") or "not computed"
        components["competitor_corroboration"] = {"value": 0.0, "detail": f"unknown -- {reason}"}

    scored = sum(1 for c in criteria if c["score"] is not None)
    components["input_completeness"] = {
        "value": scored / len(criteria),
        "detail": f"{scored}/{len(criteria)} business-fit inputs available",
    }

    today = now.date()
    checks = [snapshot_freshness(s, today) for s in latest_area_snapshots(conn, area_id)]
    aged = [(ok, detail) for ok, detail in checks if ok is not None]
    if aged:
        fresh = sum(1 for ok, _ in aged if ok)
        stale = [detail for ok, detail in aged if not ok]
        components["freshness"] = {
            "value": fresh / len(aged),
            "detail": f"{fresh}/{len(aged)} backing snapshots within their source's data-age limit"
            + (f"; stale: {'; '.join(stale)}" if stale else ""),
        }
    else:
        components["freshness"] = {"value": 0.0, "detail": "no dated backing snapshots for this area"}

    return _finalize_evidence(components)


def _finalize_evidence(components: dict[str, dict]) -> dict:
    score = 100.0 * sum(c["value"] for c in components.values()) / len(components)
    if score >= EQ_ADEQUATE:
        tier = "adequate"
    elif score >= EQ_VERIFY_FIRST:
        tier = "verify first"
    else:
        tier = "insufficient"
    return {"score": score, "tier": tier, "components": components}


def mapping_consistency(conn: sqlite3.Connection, results: dict[str, dict]) -> dict[str, dict]:
    """area_id -> {ratio, osm, ihk_gastro, relative, flagged}. Areas with
    no IHK Gastronomie observations are left out (unknown, not 0)."""
    raw = {}
    for area_id, r in results.items():
        gastro = conn.execute(
            "SELECT COUNT(*) FROM observations "
            "WHERE area_id = ? AND entity_type = 'business' AND category = ? AND snapshot_id = ?",
            (area_id, IHK_GASTRO_BRANCH, latest_snapshot_id(conn, area_id, "business")),
        ).fetchone()[0]
        total = _get(r["metrics"], "competitor_count_total", "current")
        if gastro and total and not total["is_missing"]:
            raw[area_id] = (total["value"] / gastro, total["value"], gastro)
    if not raw:
        return {}
    best = max(v[0] for v in raw.values())
    return {
        a: {
            "ratio": ratio,
            "osm": osm,
            "ihk_gastro": gastro,
            "best_ratio": best,
            "relative": ratio / best if best else 1.0,
            "flagged": ratio < UNDER_MAPPING_RATIO * best,
        }
        for a, (ratio, osm, gastro) in raw.items()
    }


def score_all(conn: sqlite3.Connection, area_ids: list[str], weights: dict[str, float] = DEFAULT_WEIGHTS,
              now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)
    results = {}
    for area_id in area_ids:
        metrics = latest_metrics(conn, area_id)
        criteria = _criterion_results(metrics)
        pillars = pillar_scores(criteria)
        results[area_id] = {
            "area_id": area_id,
            "criteria": criteria,
            "pillars": pillars,
            "business_fit": business_fit(pillars, weights),
            "evidence_quality": evidence_quality(conn, area_id, metrics, criteria, now),
            "metrics": metrics,
        }

    # Needs every area scored first: the only cross-node component.
    consistency = mapping_consistency(conn, results)
    for area_id, c in consistency.items():
        components = dict(results[area_id]["evidence_quality"]["components"])
        components["osm_mapping_consistency"] = {
            "value": min(1.0, c["relative"]),
            "detail": (
                f"{c['osm']:.0f} mapped competitors per {c['ihk_gastro']} IHK Gastronomie registrations "
                f"({c['ratio']:.1%} vs. best node {c['best_ratio']:.1%})"
            ),
        }
        results[area_id]["evidence_quality"] = _finalize_evidence(components)
        results[area_id]["mapping_consistency"] = c
    return results


def rank(fit_by_area: dict[str, float | None]) -> list[str]:
    """Descending by fit; unscorable areas sort last. Ties broken by
    area_id only so output is deterministic -- a tie is reported as such."""
    return sorted(fit_by_area, key=lambda a: (fit_by_area[a] is None, -(fit_by_area[a] or 0.0), a))


# --------------------------------------------------------------------------
# Sensitivity analysis
# --------------------------------------------------------------------------


def _normalize_weights(w: dict[str, float]) -> dict[str, float]:
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def _fits(pillars_by_area: dict[str, dict], weights: dict[str, float]) -> dict[str, float | None]:
    return {a: business_fit(p, weights) for a, p in pillars_by_area.items()}


def _minmax_pillars(results: dict[str, dict]) -> dict[str, dict]:
    """Re-normalize every criterion min-max across the candidate set
    (best raw -> 100, worst -> 0), preserving each criterion's direction."""
    defs = {c.key: c for c in BUSINESS_FIT_CRITERIA}
    by_key: dict[str, dict[str, float]] = {}
    for area_id, r in results.items():
        for c in r["criteria"]:
            if c["raw_value"] is not None:
                # Log-scale criteria spread on the same scale they are scored on.
                by_key.setdefault(c["key"], {})[area_id] = defs[c["key"]].transform(c["raw_value"])
    directions = {k: (1 if c.best > c.worst else -1) for k, c in defs.items()}

    out = {}
    for area_id, r in results.items():
        crits = []
        for c in r["criteria"]:
            vals = by_key.get(c["key"], {})
            if area_id not in vals:
                crits.append({**c, "score": None})
                continue
            lo, hi = min(vals.values()), max(vals.values())
            if hi == lo:
                s = 50.0  # no spread -> criterion doesn't discriminate
            else:
                s = (vals[area_id] - lo) / (hi - lo) * 100.0
                if directions[c["key"]] < 0:
                    s = 100.0 - s
            crits.append({**c, "score": s})
        out[area_id] = pillar_scores(crits)
    return out


def worker_onsite_check(results: dict[str, dict], weights: dict[str, float] = DEFAULT_WEIGHTS) -> dict:
    """Re-score with every node's workers at the low and high end of the
    verified on-site ranges (worker_headcount_low / _high), and for
    comparison with the whole circle (no nearest-station assignment) and
    as registered (neither correction)."""
    out = {}
    for label, metric in (("low", "worker_headcount_low"), ("high", "worker_headcount_high"),
                          ("in_circle", "worker_headcount_in_circle"),
                          ("as_registered", "worker_headcount_as_registered")):
        fits, values = {}, {}
        for area_id, r in results.items():
            m = _get(r["metrics"], metric, "current")
            if m is None or m["is_missing"]:
                fits[area_id] = values[area_id] = None
                continue
            values[area_id] = m["value"]
            # Weekend workers move in proportion to the weekday headcount.
            base = _get(r["metrics"], "worker_headcount_estimate", "current")
            wk = _get(r["metrics"], "worker_headcount_weekend", "current")
            weekend = (wk["value"] * m["value"] / base["value"]
                       if base and wk and not base["is_missing"] and not wk["is_missing"] and base["value"] else None)
            fits[area_id] = business_fit(pillar_scores(
                _criterion_results(r["metrics"], workers=m["value"], workers_weekend=weekend)), weights)
        out[label] = {"workers": values, "fits": fits, "rank": rank(fits)}
    return out


def day_type_check(results: dict[str, dict], weights: dict[str, float] = DEFAULT_WEIGHTS) -> dict:
    """Re-score with weekday and weekend weighted equally (instead of the
    calendar's 5:2) and with each day type alone: is the ranking a
    weekday result or a weekend result?"""
    scenarios = {"equal": {"weekday": 0.5, "weekend": 0.5}, "weekday_only": {"weekday": 1.0, "weekend": 0.0},
                 "weekend_only": {"weekday": 0.0, "weekend": 1.0}}
    day_total = {}
    for c in BUSINESS_FIT_CRITERIA:
        day_total[(c.pillar, c.day_type)] = day_total.get((c.pillar, c.day_type), 0.0) + c.weight
    out = {}
    for name, share in scenarios.items():
        fits = {}
        for area_id, r in results.items():
            crits = [{**c, "weight": share[c["day_type"]] * c["weight"] / day_total[(c["pillar"], c["day_type"])]}
                     for c in r["criteria"] if share[c["day_type"]] > 0]
            fits[area_id] = business_fit(pillar_scores(crits), weights)
        out[name] = {"shares": share, "fits": fits, "rank": rank(fits)}
    return out


def visitor_conversion_check(results: dict[str, dict], weights: dict[str, float] = DEFAULT_WEIGHTS) -> dict | None:
    """Re-score with the visitors-per-pageview ratio at the fit's 25th and
    75th percentile instead of its mean: does the ranking survive the
    weakest link in the demand estimate? None if no fit is on record."""
    loaded = latest_visitor_fit()
    if loaded is None:
        return None
    fit, path = loaded
    base_k = fit["visitors_per_pageview"]
    out = {"fit": path.name, "base_k": base_k}
    for label, k in (("low", fit["visitors_per_pageview_p25"]), ("high", fit["visitors_per_pageview_p75"])):
        fits = {}
        for area_id, r in results.items():
            crits = _criterion_results(r["metrics"], visitor_factor=k / base_k)
            fits[area_id] = business_fit(pillar_scores(crits), weights)
        out[label] = {"k": k, "fits": fits, "rank": rank(fits)}
    return out


def _monte_carlo(pillars_by_area: dict[str, dict]) -> dict:
    rng = random.Random(MONTE_CARLO_SEED)
    first_counts = {a: 0 for a in pillars_by_area}
    rank_sums = {a: 0 for a in pillars_by_area}
    for _ in range(MONTE_CARLO_DRAWS):
        w = _normalize_weights({p: rng.gammavariate(1.0, 1.0) for p in PILLARS})
        r = rank(_fits(pillars_by_area, w))
        first_counts[r[0]] += 1
        for i, a in enumerate(r):
            rank_sums[a] += i + 1
    return {
        "draws": MONTE_CARLO_DRAWS,
        "seed": MONTE_CARLO_SEED,
        "top_share": {a: n / MONTE_CARLO_DRAWS for a, n in first_counts.items()},
        "mean_rank": {a: s / MONTE_CARLO_DRAWS for a, s in rank_sums.items()},
    }


def competition_structure_check(results: dict[str, dict], weights: dict[str, float] = DEFAULT_WEIGHTS) -> dict:
    """Re-score with competition as absolute competitor counts in the
    400m circle instead of competitors per 10,000 people. The baseline
    divides by demand, so demand enters business_fit twice; this asks
    whether the ranking is an artifact of that structure. Demand scores
    are unchanged; random weightings use the same seed as the baseline."""
    defs = {c.key: c for c in BUSINESS_FIT_CRITERIA}
    pillars_by_area, criteria_by_area = {}, {}
    for area_id, r in results.items():
        crits = []
        for c in r["criteria"]:
            if c["key"] in ABSOLUTE_COMPETITION_ANCHORS:
                worst, best = ABSOLUTE_COMPETITION_ANCHORS[c["key"]]
                count = c.get("count")
                score = None if count is None else replace(defs[c["key"]], worst=worst, best=best).normalize(count)
                c = {**c, "raw_value": count, "score": score}
            crits.append(c)
        criteria_by_area[area_id] = crits
        pillars_by_area[area_id] = pillar_scores(crits)
    fits = _fits(pillars_by_area, weights)
    scenario_tops = {name: rank(_fits(pillars_by_area, w))[0] for name, w in SCENARIO_WEIGHTS.items()}
    return {
        "anchors": ABSOLUTE_COMPETITION_ANCHORS,
        "criteria": criteria_by_area,
        "pillars": pillars_by_area,
        "fits": fits,
        "rank": rank(fits),
        "scenario_tops": scenario_tops,
        "monte_carlo": _monte_carlo(pillars_by_area),
    }


def sensitivity_analysis(results: dict[str, dict], base_weights: dict[str, float] = DEFAULT_WEIGHTS) -> dict:
    pillars_by_area = {a: r["pillars"] for a, r in results.items()}
    base_fits = _fits(pillars_by_area, base_weights)
    base_rank = rank(base_fits)

    scenarios = []
    for name, w in SCENARIO_WEIGHTS.items():
        fits = _fits(pillars_by_area, w)
        scenarios.append({"name": name, "weights": w, "fits": fits, "rank": rank(fits)})

    one_at_a_time = []
    for pillar in PILLARS:
        for factor in (0.5, 1.5):
            w = _normalize_weights({**base_weights, pillar: base_weights[pillar] * factor})
            fits = _fits(pillars_by_area, w)
            r = rank(fits)
            one_at_a_time.append(
                {"pillar": pillar, "factor": factor, "weights": w, "fits": fits, "rank": r, "top_changed": r[0] != base_rank[0]}
            )

    # Uniform over the weight simplex (Dirichlet(1,1,1)): "if we had no
    # prior on the weights at all, how often does each node come out on top?"
    monte_carlo = _monte_carlo(pillars_by_area)

    minmax_fits = _fits(_minmax_pillars(results), base_weights)

    scored = [base_fits[a] for a in base_rank if base_fits[a] is not None]
    spread = (scored[0] - scored[-1]) if len(scored) > 1 else None
    top_stable = (
        all(s["rank"][0] == base_rank[0] for s in scenarios)
        and not any(o["top_changed"] for o in one_at_a_time)
        and monte_carlo["top_share"][base_rank[0]] >= 0.8
    )

    return {
        "base_weights": base_weights,
        "base_fits": base_fits,
        "base_rank": base_rank,
        "fit_spread": spread,
        "scenarios": scenarios,
        "one_at_a_time": one_at_a_time,
        "monte_carlo": monte_carlo,
        "minmax_check": {"fits": minmax_fits, "rank": rank(minmax_fits)},
        "top_rank_robust": top_stable,
    }
