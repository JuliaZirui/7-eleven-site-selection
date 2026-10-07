"""Core data structures for the site-selection pipeline.

See ../site_candidates.md for the business rationale behind the time
windows (dual-peak model) and the 400m search radius.
"""

from dataclasses import dataclass
from enum import Enum

from site_input import parse_sites, site_input_path


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


# Candidate sites are input, not code: read from a JSON file (default:
# the Berlin case study in examples/), or from the file named in the
# SITE_INPUT environment variable -- see site_input.py. See
# site_candidates.md for the Preisklasse verification and customer-
# profile rationale behind the default nodes, and the evidence-gap note
# behind the 400m radius.
def _load_candidates() -> tuple[tuple[LocationInput, ...], dict[str, tuple[str, ...]]]:
    sites = parse_sites(site_input_path())
    nodes = tuple(LocationInput(area_id=x["area_id"], name=x["name"], lat=x["lat"], lon=x["lon"],
                                radius_m=x["radius_m"]) for x in sites)
    return nodes, {x["area_id"]: x["gtfs_stations"] for x in sites}


# NODE_STATIONS: the node's own S/U station(s) in the VBB GTFS snapshot,
# primary name first (attraction gateways, worker station assignment).
CANDIDATE_NODES, NODE_STATIONS = _load_candidates()
