"""Weekday vs. weekend/holiday: one place for every day-type rule (scoring_v6).

Demand and competition are not the same on a Wednesday and a Saturday:
commuters mostly stay home at the weekend, clubs such as Berghain open
only from Friday night to Monday morning, and Berlin's Sunday trading law
closes most shops. Every layer (metrics, reference calibration, scoring,
report) reads its day-type rules from here so they stay identical.

Representative days: Wednesday for the weekday, Saturday and Sunday
averaged for the weekend (public holidays trade like Sundays in Berlin).

Sources for the stated rules:
  - Weekend work by sector: Destatis Mikrozensus 2023 (press release
    2025-04-28): employees who worked at least occasionally at the
    weekend in the last four weeks -- all 27%, Saturday 25%, Sunday 15%;
    hospitality 70%, arts/entertainment/recreation 55%, trade 47%,
    health/social 46%.
  - Sunday trading: Berliner Ladenöffnungsgesetz as summarised by the
    Senate (berlin.de, "Ladenöffnungszeiten in Berlin"): shops closed on
    Sundays and holidays; bakery goods, flowers, newspapers 7-16;
    pharmacies, petrol stations and shops in stations, airports and bus
    terminals without time limit.
"""

from __future__ import annotations

from dataclasses import dataclass

from opening_hours import WEEKDAY_TOKENS, ParsedHours, parse_opening_hours
from poi_taxonomy import EVENING_PEAK_CATEGORIES, MORNING_PEAK_CATEGORIES, POICategory

WE, SA, SU = (WEEKDAY_TOKENS.index(d) for d in ("We", "Sa", "Su"))

DAY_TYPES: dict[str, tuple[int, ...]] = {"weekday": (WE,), "weekend": (SA, SU)}
# Calendar share of each day type, the baseline weight in business fit.
DAY_TYPE_WEIGHTS: dict[str, float] = {"weekday": 5 / 7, "weekend": 2 / 7}
# Period labels written on the day-type metrics.
PERIOD = {"weekday": "weekday_We", "weekend": "weekend_SaSu"}

# ---------------------------------------------------------------------------
# Competition windows
# ---------------------------------------------------------------------------

WEEKEND_DAY_CATEGORIES: tuple[str, ...] = (
    POICategory.BAKERY.value,
    POICategory.CAFE.value,
    POICategory.SUPERMARKET.value,
    POICategory.SPATI_KIOSK.value,
    POICategory.FAST_FOOD.value,
    POICategory.GAS_STATION_CONVENIENCE.value,
)
# Late-night basket: the formats that trade after 22:00. Supermarkets are
# left out by format; one with OSM hours past 22:00 is still out of this set
# (Berlin supermarkets close by midnight and none in the three circles is
# tagged open later).
WEEKEND_NIGHT_CATEGORIES: tuple[str, ...] = (
    POICategory.SPATI_KIOSK.value,
    POICategory.FAST_FOOD.value,
    POICategory.GAS_STATION_CONVENIENCE.value,
)


@dataclass(frozen=True)
class CompetitionWindow:
    key: str  # metric prefix: <key>_competitor_count
    day_type: str
    start_hour: int
    end_hour: int  # <= start_hour means the window runs past midnight
    categories: tuple[str, ...]
    label: str


COMPETITION_WINDOWS: tuple[CompetitionWindow, ...] = (
    CompetitionWindow("morning_peak", "weekday", 7, 9, MORNING_PEAK_CATEGORIES, "weekday 7–9"),
    CompetitionWindow("evening_peak", "weekday", 16, 19, EVENING_PEAK_CATEGORIES, "weekday 16–19"),
    CompetitionWindow("weekend_day", "weekend", 10, 19, WEEKEND_DAY_CATEGORIES, "weekend 10–19"),
    CompetitionWindow("weekend_night", "weekend", 22, 2, WEEKEND_NIGHT_CATEGORIES, "weekend 22–02"),
)

# Retail formats Berlin closes on Sundays and holidays unless the shop is
# inside a station (or OSM records Sunday hours, which then decide).
SUNDAY_CLOSED_RETAIL = frozenset({
    POICategory.SPATI_KIOSK.value,
    POICategory.SUPERMARKET.value,
    POICategory.BEVERAGES.value,
    POICategory.CHEMIST.value,
})
SUNDAY_BAKERY_HOURS = (7, 16)


def _overlaps(parsed: ParsedHours, day: int, start_min: int, end_min: int) -> bool:
    return any(s < end_min and e > start_min for s, e in parsed.schedule.get(day, ()))


def open_in_window(parsed: ParsedHours, day: int, start_hour: int, end_hour: int) -> bool:
    """Any open interval overlapping the window on `day`; a window with
    end <= start runs into the next day (22-02 = 22-24 on day, 0-2 on day+1)."""
    if end_hour > start_hour:
        return _overlaps(parsed, day, start_hour * 60, end_hour * 60)
    return _overlaps(parsed, day, start_hour * 60, 1440) or _overlaps(parsed, (day + 1) % 7, 0, end_hour * 60)


def _in_station(poi: dict) -> bool:
    if "in_station" in poi:
        return bool(poi["in_station"])
    return bool((poi.get("payload") or {}).get("in_station"))


def poi_open(poi: dict, day: int, window: CompetitionWindow) -> bool | None:
    """True / False for a POI of the window's categories, None when it is
    known closed by its own OSM hours (counted separately as excluded).

    Unknown hours: the format is assumed to trade in its window, except
    where Berlin's Sunday law says otherwise -- Sunday-closed retail
    outside a station is closed, bakeries only trade 7-16."""
    parsed = parse_opening_hours(poi.get("opening_hours_raw"))
    if parsed is not None:
        return True if open_in_window(parsed, day, window.start_hour, window.end_hour) else None
    if day == SU and not _in_station(poi):
        if poi["category"] in SUNDAY_CLOSED_RETAIL:
            return False
        if poi["category"] == POICategory.BAKERY.value:
            lo, hi = SUNDAY_BAKERY_HOURS
            end = window.end_hour if window.end_hour > window.start_hour else 24
            return window.start_hour < hi and end > lo
    return True


def count_window_competitors(pois: list[dict], window: CompetitionWindow) -> tuple[float, float]:
    """(competitors open, known-closed excluded), averaged over the window's
    representative days (Sa and Su for the weekend)."""
    days = DAY_TYPES[window.day_type]
    open_total = closed_total = 0
    for day in days:
        for p in pois:
            if p["category"] not in window.categories:
                continue
            state = poi_open(p, day, window)
            if state is None:
                closed_total += 1
            elif state:
                open_total += 1
    return open_total / len(days), closed_total / len(days)


# ---------------------------------------------------------------------------
# Workers at the weekend
# ---------------------------------------------------------------------------

# Share of a sector's employees who work weekends at least occasionally
# (Destatis 2023), by NACE division. Office sections (information, finance,
# real estate, professional, administrative services, public admin,
# education) have no published figure: 0.10 is a stated assumption, well
# below the all-sector 27%. Anything else gets the all-sector 27%.
_SECTOR_WEEKEND_SHARE: list[tuple[range, float]] = [
    (range(55, 57), 0.70),  # accommodation, food & beverage service
    (range(90, 94), 0.55),  # arts, entertainment, recreation
    (range(45, 48), 0.47),  # trade
    (range(86, 89), 0.46),  # health, social work
    (range(58, 76), 0.10),  # J-M: information, finance, real estate, professional (assumption)
    (range(77, 80), 0.10),  # N office services (assumption)
    (range(81, 83), 0.10),
    (range(84, 86), 0.10),  # public administration, education (assumption)
]
ALL_SECTOR_WEEKEND_SHARE = 0.27
# Someone who works weekends "at least occasionally" is not on site every
# weekend day: assumption that they are on about half of them.
WEEKEND_DAY_PRESENCE = 0.5


def weekend_share_for_division(division: str | None) -> float:
    try:
        d = int(division or "")
    except ValueError:
        return ALL_SECTOR_WEEKEND_SHARE
    for r, share in _SECTOR_WEEKEND_SHARE:
        if d in r:
            return share
    return ALL_SECTOR_WEEKEND_SHARE


def weekend_worker_factor(division: str | None, override: float | None = None) -> float:
    """Fraction of a registration's weekday on-site headcount present on a
    representative weekend day. A verified employer's own weekend share
    (e.g. Zalando's offices, registered under retail) overrides its NACE
    division."""
    share = override if override is not None else weekend_share_for_division(division)
    return share * WEEKEND_DAY_PRESENCE


# ---------------------------------------------------------------------------
# Visitors by day type
# ---------------------------------------------------------------------------

# A club with no usable OSM hours trades Friday and Saturday nights (Berlin
# club nights run into Saturday and Sunday mornings) -- Berghain opens
# Friday midnight to Monday morning. Counted on Sa and Su, never midweek.
CLUB_DEFAULT_DAYS = (SA, SU)
CLUB_KINDS = frozenset({"nightclub"})


def venue_open_days(kind: str | None, opening_hours_raw: str | None) -> tuple[int, ...]:
    """Weekdays (0=Mo..6=Su) a venue receives visitors on. OSM hours when
    parseable (a day counts if any interval falls on it); else clubs on
    CLUB_DEFAULT_DAYS and everything else every day."""
    parsed = parse_opening_hours(opening_hours_raw)
    if parsed is not None:
        days = tuple(d for d in range(7) if parsed.schedule.get(d))
        if days:
            return days
    if kind in CLUB_KINDS:
        return CLUB_DEFAULT_DAYS
    return tuple(range(7))


def visitors_on_day_type(annual: float, open_days: tuple[int, ...], day_type: str) -> float:
    """Average visitors per day of a day type: the annual total spread over
    the venue's open days, then averaged over the day type's
    representative days (0 on days it is closed)."""
    if not open_days:
        return 0.0
    per_open_day = annual / (52.0 * len(open_days))
    days = DAY_TYPES[day_type]
    return sum(per_open_day for d in days if d in open_days) / len(days)
