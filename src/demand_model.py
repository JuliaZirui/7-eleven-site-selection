"""Demand in one unit: people per day in a node's catchment (scoring_v5).

v1-v4 scored commuters, residents and visitors on three separate 0-100
scales and averaged them with fixed weights (1 : 1 : 1/3 + 1/3), so the
segment mix was the same judgment call at every node. Here each segment
is converted to people per day and the three are summed, so the mix is
whatever the data say it is at each node:

  - residents: catchment_population_estimate (area-weighted MSS 2025);
  - workers:   IHK registrations within 400m, each size class converted
               to a headcount at its geometric midpoint;
  - visitors:  annual visitors of every attraction the node's station is
               a gateway for, from Wikipedia pageviews through a log-log
               fit on Berlin attractions with published visitor counts
               (calibrate_visitors.py), times the station's 1/k gateway
               share, divided by 365.

Shared by metrics_pipeline.py (candidates) and calibrate_demand.py
(reference stations) so both are converted identically.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path
from typing import Callable

from day_types import venue_open_days, visitors_on_day_type, weekend_worker_factor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION_DIR = PROJECT_ROOT / "db" / "raw" / "calibration"
VISITOR_FIT_GLOB = "visitor_fit_*.json"
ONSITE_VERIFIED_CSV = CALIBRATION_DIR / "worker_onsite_verified.csv"
# Large employers verified on site but absent from IHK Gewerbedaten (their
# German entity files its headcount at another address, e.g. Amazon in
# the EDGE East Side Tower). Added with coordinates and sources.
UNREGISTERED_CSV = CALIBRATION_DIR / "worker_onsite_unregistered.csv"
# Venues whose annual visitors are known from capacity or published
# attendance rather than fame (e.g. Berghain: 1,500 capacity, ~115,000 a
# year, where the pageview conversion gave ~5.9 million).
VENUE_ATTENDANCE_CSV = CALIBRATION_DIR / "venue_attendance_verified.csv"

# Registrations at or above this size-class floor are checked by hand
# before their headcount is counted as on-site: the IHK size class is the
# *company's*, filed at whichever Berlin address it registered, so a
# head-office seat or a branch can carry a whole firm's headcount (one
# 5,000-7,499 bank registration at Pariser Platz is a ~450-desk office).
LARGE_REGISTRATION_MIN_EMPLOYEES = 500

# The 12-month window the candidates' attraction snapshots (#30-32) were
# weighted with. The visitor fit and the reference-station calibration
# use the same window, so pageviews are comparable across all three.
PAGEVIEW_WINDOW = (date(2025, 9, 1), date(2026, 9, 1))


def employees_estimate(employees_range: str | None) -> float | None:
    """IHK size class -> headcount at the class's geometric midpoint.

    '0 Beschäftigte' -> 0, '10 - 19 Beschäftigte' -> sqrt(10*19) = 13.8,
    'unbekannt' / None -> None. The geometric midpoint suits classes
    whose width grows with size (1-3 ... 5000-7499). A registration is a
    registered seat, not a verified on-site headcount -- the stated
    caveat that travels with every worker figure.
    """
    if not employees_range:
        return None
    parts = employees_range.replace("Beschäftigte", "").split("-")
    try:
        low = int(parts[0].strip())
        high = int(parts[1].strip()) if len(parts) > 1 else low
    except ValueError:
        return None
    if high == 0:
        return 0.0
    return math.sqrt(max(low, 1) * high)


def workers_estimate(employees_ranges: list[str | None]) -> tuple[float, int]:
    """(summed headcount, registrations with unknown size). Unknown sizes
    are left out of the sum and counted, never imputed."""
    total, unknown = 0.0, 0
    for r in employees_ranges:
        e = employees_estimate(r)
        if e is None:
            unknown += 1
        else:
            total += e
    return total, unknown


def _class_floor(employees_range: str | None) -> int | None:
    try:
        return int((employees_range or "").split("-")[0].replace("Beschäftigte", "").strip())
    except ValueError:
        return None


def load_onsite_verifications(path: Path = ONSITE_VERIFIED_CSV, corrections: bool = True) -> dict[str, dict]:
    """opendata_id -> {onsite_low, onsite_central, onsite_high, identified_as, ...}
    from the hand-checked table (sources per row). corrections=False
    returns {}: every registration at its class midpoint, the state
    before review (the data-plausibility audit runs on it)."""
    if not corrections or not path.exists():
        return {}
    out = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        for k in ("onsite_low", "onsite_central", "onsite_high"):
            row[k] = float(row[k])
        row["weekend_share"] = float(row["weekend_share"]) if row.get("weekend_share") else None
        out[row["opendata_id"]] = row
    return out


def load_unregistered_employers(path: Path = UNREGISTERED_CSV, corrections: bool = True) -> list[dict]:
    """Hand-added employers; [] with corrections=False (pre-review state)."""
    if not corrections or not path.exists():
        return []
    rows = []
    for row in csv.DictReader(path.open(encoding="utf-8")):
        for k in ("lat", "lon", "onsite_low", "onsite_central", "onsite_high"):
            row[k] = float(row[k])
        row["weekend_share"] = float(row["weekend_share"]) if row.get("weekend_share") else None
        rows.append(row)
    return rows


def workers_onsite(
    registrations: list[dict],
    verified: dict[str, dict],
    uses_this_station: Callable[[dict], bool] | None = None,
    unregistered: list[dict] | None = None,
) -> dict:
    """People working in the circle who commute through this node's
    station, with large registrations replaced by their verified on-site
    headcount. registrations: IHK payloads (need opendata_id and
    employees_range; lat/lon when uses_this_station is given).

    uses_this_station(reg) -> False drops a registration whose nearest
    S/U station is another one: inside a 400m circle, but its commuters
    get off elsewhere (Brandenburger Tor's eastern edge belongs to
    Unter den Linden). None counts the whole circle.

    unregistered: verified employers missing from IHK, already cut to the
    circle by the caller; added under the same station rule.

    Weekend: every counted headcount is also multiplied by its weekend
    factor (day_types.weekend_worker_factor: the sector's Destatis weekend
    share, or a verified employer's own, x on-site share of weekend days)
    into weekend_central / weekend_low / weekend_high.

    Returns central / low / high, in_circle (verified, before the station
    assignment), as_registered (neither correction), other_station (the
    central headcount assigned away), unknown-size count, and large
    registrations left unverified (counted at their class midpoint and
    flagged -- never silently trusted)."""
    central = low = high = as_registered = in_circle = other_station = 0.0
    wk_central = wk_low = wk_high = 0.0
    unknown = 0
    verified_ids, unverified = [], []
    for reg in registrations:
        midpoint = employees_estimate(reg.get("employees_range"))
        if midpoint is None:
            unknown += 1
            continue
        as_registered += midpoint
        v = verified.get(str(reg.get("opendata_id")))
        mine = uses_this_station is None or uses_this_station(reg)
        if v is not None:
            in_circle += v["onsite_central"]
            verified_ids.append(str(reg["opendata_id"]))
            if not mine:
                other_station += v["onsite_central"]
                continue
            central += v["onsite_central"]
            low += v["onsite_low"]
            high += v["onsite_high"]
            f = weekend_worker_factor(reg.get("branch_top_level_id"), v.get("weekend_share"))
            wk_central += v["onsite_central"] * f
            wk_low += v["onsite_low"] * f
            wk_high += v["onsite_high"] * f
            continue
        if (_class_floor(reg.get("employees_range")) or 0) >= LARGE_REGISTRATION_MIN_EMPLOYEES:
            unverified.append(str(reg.get("opendata_id")))
        in_circle += midpoint
        if not mine:
            other_station += midpoint
            continue
        central += midpoint
        low += midpoint
        high += midpoint
        f = weekend_worker_factor(reg.get("branch_top_level_id"))
        wk_central += midpoint * f
        wk_low += midpoint * f
        wk_high += midpoint * f
    added = 0.0
    for emp in unregistered or []:
        in_circle += emp["onsite_central"]
        if uses_this_station is not None and not uses_this_station(emp):
            other_station += emp["onsite_central"]
            continue
        added += emp["onsite_central"]
        central += emp["onsite_central"]
        low += emp["onsite_low"]
        high += emp["onsite_high"]
        f = weekend_worker_factor(None, emp.get("weekend_share"))
        wk_central += emp["onsite_central"] * f
        wk_low += emp["onsite_low"] * f
        wk_high += emp["onsite_high"] * f
    return {"central": central, "low": low, "high": high, "as_registered": as_registered, "in_circle": in_circle,
            "other_station": other_station, "unregistered_added": added, "unknown_size": unknown,
            "weekend_central": wk_central, "weekend_low": wk_low, "weekend_high": wk_high,
            "verified": verified_ids, "large_unverified": unverified}


def latest_visitor_fit(calibration_dir: Path = CALIBRATION_DIR) -> tuple[dict, Path] | None:
    fits = sorted(calibration_dir.glob(VISITOR_FIT_GLOB))
    if not fits:
        return None
    return json.loads(fits[-1].read_text()), fits[-1]


def load_known_attendance(fit: dict | None = None, path: Path = VENUE_ATTENDANCE_CSV,
                          corrections: bool = True) -> dict[str, float]:
    """German Wikipedia title -> known annual visitors: the published
    counts the visitor fit was trained on (fit["data"]) plus
    VENUE_ATTENDANCE_CSV (central values). A known figure always beats the
    fame conversion -- fame overstates visits wherever it outruns
    capacity (clubs) and understates it for family attractions.

    corrections=False returns {}: every venue from the fame conversion,
    the published counts included (the pre-review state)."""
    known: dict[str, float] = {}
    if not corrections:
        return known
    for d in (fit or {}).get("data", []):
        title = (d.get("titles") or {}).get("dewiki")
        if title:
            known[title] = float(d["annual_visitors"])
    if path.exists():
        for row in csv.DictReader(path.open(encoding="utf-8")):
            known[row["de_title"]] = float(row["annual_central"])
    return known


def annual_visitors(pageviews: float, fit: dict) -> float:
    """Annual visitors from 12-month de+en pageviews via the log-log fit
    log10(visitors) = intercept + slope * log10(pageviews)."""
    if pageviews <= 0:
        return 0.0
    return 10 ** (fit["intercept"] + fit["slope"] * math.log10(pageviews))


def visitors_split(records: list[dict], fit: dict, day_type: str | None = None,
                   known: dict[str, float] | None = None) -> tuple[float, float, int]:
    """(people per day from known attendance, from the pageview
    conversion, records with neither). Same day-type logic as
    visitors_per_day; a record's German Wikipedia title (in
    'wikipedia_titles') selects a known annual figure when one exists."""
    from_known = from_pageviews = 0.0
    skipped = 0
    for r in records:
        title = (r.get("wikipedia_titles") or {}).get("dewiki")
        if known and title in known:
            annual, bucket = known[title] * r["share"], "known"
        elif r.get("pageviews") is not None:
            annual, bucket = annual_visitors(r["pageviews"], fit) * r["share"], "pageviews"
        else:
            skipped += 1
            continue
        per_day = annual / 365.0 if day_type is None else visitors_on_day_type(
            annual, venue_open_days(r.get("kind"), r.get("opening_hours_raw")), day_type)
        if bucket == "known":
            from_known += per_day
        else:
            from_pageviews += per_day
    return from_known, from_pageviews, skipped


def visitors_per_day(records: list[dict], fit: dict, day_type: str | None = None) -> tuple[float, int]:
    """(people per day, records without a pageview figure). Each record
    needs 'pageviews' and 'share' (the node's 1/k gateway share).
    Records without a Wikipedia article are left out and counted.

    day_type None: the annual average day. 'weekday' / 'weekend': the
    venue's visitors spread over its open days (day_types.venue_open_days
    from 'kind' and 'opening_hours_raw' -- a club without hours counts on
    weekend days only) and averaged over that day type's days."""
    total, skipped = 0.0, 0
    for r in records:
        if r.get("pageviews") is None:
            skipped += 1
            continue
        annual = annual_visitors(r["pageviews"], fit) * r["share"]
        if day_type is None:
            total += annual / 365.0
        else:
            total += visitors_on_day_type(annual, venue_open_days(r.get("kind"), r.get("opening_hours_raw")), day_type)
    return total, skipped
