"""Core data structures for the site-selection pipeline.

See ../site_candidates.md for the business rationale behind the time
windows (dual-peak model) and the 400m search radius.
"""

from dataclasses import dataclass
from enum import Enum


class TimeWindowLabel(str, Enum):
    MORNING_PEAK = "morning_peak"
    EVENING_PEAK = "evening_peak"
    MIDDAY_OFFPEAK = "midday_offpeak"


@dataclass(frozen=True)
class TimeWindow:
    label: TimeWindowLabel
    start_hour: int  # 0-23, local time, inclusive
    end_hour: int  # 0-23, local time, exclusive


# Matches store_hypothesis.md's dual-peak model: morning/evening commute
# peaks as the core evaluation windows, midday as the off-peak baseline.
DEFAULT_TIME_WINDOWS: tuple[TimeWindow, ...] = (
    TimeWindow(TimeWindowLabel.MORNING_PEAK, start_hour=7, end_hour=9),
    TimeWindow(TimeWindowLabel.EVENING_PEAK, start_hour=16, end_hour=19),
    TimeWindow(TimeWindowLabel.MIDDAY_OFFPEAK, start_hour=10, end_hour=15),
)


@dataclass(frozen=True)
class LocationInput:
    area_id: str  # stable slug, matches areas.area_id in the SQLite schema
    name: str
    lat: float
    lon: float
    radius_m: int
    time_windows: tuple[TimeWindow, ...] = DEFAULT_TIME_WINDOWS


# Final candidate set — see site_candidates.md for the Preisklasse
# verification and customer-profile rationale behind each node, and the
# evidence-gap note behind the 400m radius.
CANDIDATE_NODES: tuple[LocationInput, ...] = (
    LocationInput(
        area_id="brandenburger_tor",
        name="Brandenburger Tor",
        lat=52.51653594770922,
        lon=13.381516508773798,
        radius_m=400,
    ),
    LocationInput(
        area_id="warschauer_strasse",
        name="Warschauer Straße",
        lat=52.50533737805442,
        lon=13.449070864605977,
        radius_m=400,
    ),
    LocationInput(
        area_id="schoenhauser_allee",
        name="Schönhauser Allee",
        lat=52.549317383699474,
        lon=13.413628569194154,
        radius_m=400,
    ),
)
