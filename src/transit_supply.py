"""Transit-supply proxy for foot traffic: hourly departures x vehicle
capacity x assumed occupancy x assumed turnover, 08:00-24:00, per
candidate node and day type.

Only the departure counts are measured data (VBB GTFS). Capacity,
occupancy profile and turnover are explicit planning assumptions, kept
in the constants below so they can be changed and re-run. The same
occupancy profile and turnover are applied to all three nodes, so the
cross-node comparison is driven by timetable supply alone -- it cannot
see node-specific demand (nightlife, tourism) or pass-through trains.

Run directly (reads the GTFS zip snapshot in db/raw/vbb_gtfs/):
    python3 transit_supply.py
"""

from __future__ import annotations

import csv
import io
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from geo import haversine_m
from models import CANDIDATE_NODES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GTFS_ZIP = PROJECT_ROOT / "db" / "raw" / "vbb_gtfs" / "vbb_gtfs_20260924.zip"
# stops.txt only, byte-identical to the member of GTFS_ZIP. The full feed
# (~75MB) is not committed; station lookups need nothing else, so the
# pipeline and tests run from this extract. See db/raw/vbb_gtfs/README.md.
GTFS_STOPS_ZIP = PROJECT_ROOT / "db" / "raw" / "vbb_gtfs" / "vbb_gtfs_20260924_stops.zip"
REPORTS_DIR = PROJECT_ROOT / "reports"

HOURS = range(8, 24)

# Representative service days inside the feed's validity (24.09.-12.12.2026).
# Weekday = per-route/hour median over Tue-Thu, which damps one-off
# construction days (e.g. S41/S42 thinned on 01.10.). Weekend days were
# picked to avoid 03.10. (holiday, S3 suspended) and 17./18.10. (S9
# diverted via Warschauer Str., doubling its count). Berlin runs the
# Sunday timetable on public holidays, so Sunday doubles as the holiday case.
DAY_TYPES: dict[str, list[str]] = {
    "weekday": ["20260929", "20260930", "20261006", "20261007", "20261008"],
    "saturday": ["20261010"],
    "sunday_holiday": ["20261011"],
}

# Vehicle capacity per departure (seated + standing, rounded). Sources:
# BVG "Alles eine Frage des Profils" (largest Kleinprofil train IK = 660,
# largest Grossprofil train H = 750); S-Bahn Berlin press release on
# S1/S2 going from 6 to 8 cars = "+~300 places", i.e. ~150/car -> 6-car
# ~900, 8-car ~1,200. Tram/bus values are assumptions.
CAPACITY: dict[str, int] = {
    "s_bahn_8car": 1200,  # S1, S2 (8 cars per the S-Bahn release)
    "s_bahn": 900,  # other lines, assumed 6-car average
    "u_kleinprofil": 600,  # U1-U4, below the 660 maximum (not all 8-car)
    "u_grossprofil": 750,  # U5
    "tram": 200,  # Flexity-class, assumption
    "bus_double_deck": 120,  # 100, 300, assumption
    "bus": 80,  # other lines, assumption
}

# Assumed average occupancy (share of capacity) of a vehicle arriving at
# the node, by hour. Generic urban shape -- weekday commuter peaks,
# weekend late-morning build-up and a later evening. NOT measured.
OCCUPANCY: dict[str, dict[int, float]] = {
    "weekday": {8: .45, 9: .35, 10: .25, 11: .25, 12: .28, 13: .30, 14: .32, 15: .38,
                16: .45, 17: .45, 18: .38, 19: .28, 20: .22, 21: .18, 22: .15, 23: .12},
    "saturday": {8: .12, 9: .16, 10: .22, 11: .28, 12: .32, 13: .34, 14: .35, 15: .35,
                 16: .35, 17: .33, 18: .30, 19: .27, 20: .24, 21: .22, 22: .22, 23: .22},
    "sunday_holiday": {8: .08, 9: .10, 10: .15, 11: .20, 12: .25, 13: .28, 14: .30, 15: .30,
                       16: .28, 17: .26, 18: .22, 19: .18, 20: .15, 21: .13, 22: .12, 23: .10},
}

# Share of on-board passengers who board, alight or transfer at the node
# (low / mid / high). Unknown per station, so reported as a range.
TURNOVER = {"low": 0.20, "mid": 0.30, "high": 0.40}

# Published anchors used only as order-of-magnitude checks.
CHECK_S_TUNNEL_SECTION_LOAD = 120_000  # Friedrichstr.-Brandenburger Tor, per day, 2018 (Berliner Zeitung, 02.04.2019)
CHECK_WARSCHAUER_DAILY = 85_000  # >85,000/day, 2012 (punkt 3, 1/2013, via de.wikipedia)
# Share of a day's riders travelling 08-24 (assumption), used to scale the
# tunnel-section anchor down to the modelled window.
SHARE_OF_DAY_IN_WINDOW = 0.90


def vehicle_class(route_type: str, name: str) -> str:
    if route_type == "109":
        return "s_bahn_8car" if name in ("S1", "S2") else "s_bahn"
    if route_type == "400":
        return "u_grossprofil" if name in ("U5", "U55", "U6", "U7", "U8", "U9") else "u_kleinprofil"
    if route_type == "900":
        return "tram"
    if route_type == "700":
        return "bus_double_deck" if name in ("100", "300") else "bus"
    return "other"


def _rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))


def _active_services(zf: zipfile.ZipFile, dates: set[str]) -> dict[str, set[str]]:
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    active: dict[str, set[str]] = {d: set() for d in dates}
    for r in _rows(zf, "calendar.txt"):
        for d in dates:
            wd = weekdays[datetime.strptime(d, "%Y%m%d").weekday()]
            if r["start_date"] <= d <= r["end_date"] and r[wd] == "1":
                active[d].add(r["service_id"])
    for r in _rows(zf, "calendar_dates.txt"):
        if r["date"] in active:
            if r["exception_type"] == "1":
                active[r["date"]].add(r["service_id"])
            elif r["exception_type"] == "2":
                active[r["date"]].discard(r["service_id"])
    return active


def extract_departures(zip_path: Path = GTFS_ZIP) -> dict:
    """Return {node: {date: Counter[(route, vehicle_class, hour)]}} of trip
    visits within each node's radius. A trip touching several platforms
    inside one radius counts once (its first touch)."""
    all_dates = {d for ds in DAY_TYPES.values() for d in ds}
    with zipfile.ZipFile(zip_path) as zf:
        stop_node: dict[str, str] = {}
        for r in _rows(zf, "stops.txt"):
            if r["location_type"] not in ("0", ""):
                continue
            for node in CANDIDATE_NODES:
                if haversine_m(node.lat, node.lon, float(r["stop_lat"]), float(r["stop_lon"])) <= node.radius_m:
                    stop_node[r["stop_id"]] = node.name
        routes = {r["route_id"]: (r["route_short_name"], r["route_type"]) for r in _rows(zf, "routes.txt")}
        trips = {r["trip_id"]: (r["route_id"], r["service_id"]) for r in _rows(zf, "trips.txt")}
        active = _active_services(zf, all_dates)

        first_touch: dict[tuple[str, str], int] = {}
        for r in _rows(zf, "stop_times.txt"):
            node = stop_node.get(r["stop_id"])
            if node is None:
                continue
            hour = int((r["departure_time"] or r["arrival_time"]).split(":")[0])
            key = (r["trip_id"], node)
            if key not in first_touch or hour < first_touch[key]:
                first_touch[key] = hour

    out: dict = defaultdict(lambda: defaultdict(Counter))
    for (trip_id, node), hour in first_touch.items():
        route_id, service_id = trips[trip_id]
        name, rtype = routes[route_id]
        for d in all_dates:
            if service_id in active[d]:
                out[node][d][(name, vehicle_class(rtype, name), hour)] += 1
    return out


def _day_type_counts(per_date: dict, dates: list[str]) -> Counter:
    """Per (route, class, hour) median across the day type's dates."""
    keys = {k for d in dates for k in per_date.get(d, {})}
    return Counter({k: statistics.median(per_date.get(d, Counter())[k] for d in dates) for k in keys})


def estimate(departures: dict, scale: float) -> dict:
    result: dict = {}
    for node in (n.name for n in CANDIDATE_NODES):
        result[node] = {}
        for day_type, dates in DAY_TYPES.items():
            counts = _day_type_counts(departures[node], dates)
            hourly = []
            for h in HOURS:
                deps = sum(v for (r, c, hh), v in counts.items() if hh == h)
                cap = sum(v * CAPACITY[c] for (r, c, hh), v in counts.items() if hh == h)
                onboard = cap * OCCUPANCY[day_type][h] * scale
                hourly.append({
                    "hour": h,
                    "departures": round(deps, 1),
                    "capacity": round(cap),
                    **{f"movements_{k}": round(onboard * t) for k, t in TURNOVER.items()},
                })
            by_line = Counter()
            for (r, c, hh), v in counts.items():
                if hh in HOURS:
                    by_line[r] += v
            result[node][day_type] = {
                "hourly": hourly,
                "departures_by_line_8_24": dict(sorted(by_line.items())),
                "total_departures_8_24": round(sum(x["departures"] for x in hourly)),
                "total_capacity_8_24": sum(x["capacity"] for x in hourly),
                **{f"total_movements_{k}_8_24": sum(x[f"movements_{k}"] for x in hourly) for k in TURNOVER},
            }
    return result


def calibrate(departures: dict) -> dict:
    """Scale the occupancy profile so modelled weekday S-Bahn on-board load
    at Brandenburger Tor (08-24) matches the published tunnel-section load
    (Friedrichstr.-Brandenburger Tor, 120,000/day, 2018). One real
    measurement fixes the absolute level; the hourly shape stays assumed."""
    bt = _day_type_counts(departures["Brandenburger Tor"], DAY_TYPES["weekday"])
    s_trains = sum(v for (r, c, h), v in bt.items() if c.startswith("s_bahn"))
    modelled = sum(v * CAPACITY[c] * OCCUPANCY["weekday"][h]
                   for (r, c, h), v in bt.items() if c.startswith("s_bahn") and h in HOURS)
    target = CHECK_S_TUNNEL_SECTION_LOAD * SHARE_OF_DAY_IN_WINDOW
    scale = target / modelled
    return {
        "bt_s_bahn_trains_full_day": round(s_trains),
        "anchor_section_load_per_day": CHECK_S_TUNNEL_SECTION_LOAD,
        "target_load_8_24": round(target),
        "uncalibrated_model_load_8_24": round(modelled),
        "occupancy_scale": round(scale, 3),
        "calibrated_weekday_mean_occupancy_8_24": round(statistics.mean(OCCUPANCY["weekday"].values()) * scale, 3),
        "warschauer_published_daily_2012": CHECK_WARSCHAUER_DAILY,
    }


def run() -> dict:
    departures = extract_departures()
    cal = calibrate(departures)
    now = datetime.now(timezone.utc)
    payload = {
        "computed_at": now.isoformat(),
        "source": {"gtfs": GTFS_ZIP.name, "publisher": "VBB", "feed_valid": "20260924-20261212",
                   "license": "CC BY 4.0 (VBB GTFS open data)"},
        "assumptions": {"capacity": CAPACITY, "occupancy": OCCUPANCY, "turnover": TURNOVER,
                        "share_of_day_in_window": SHARE_OF_DAY_IN_WINDOW,
                        "day_types": DAY_TYPES, "radius_m": CANDIDATE_NODES[0].radius_m},
        "calibration": cal,
        "nodes": estimate(departures, cal["occupancy_scale"]),
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"transit_supply_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return payload


if __name__ == "__main__":
    p = run()
    print(json.dumps(p["calibration"], indent=2))
    for node, dts in p["nodes"].items():
        for dtp, v in dts.items():
            print(f"{node:18} {dtp:15} deps={v['total_departures_8_24']:5} cap={v['total_capacity_8_24']:8,} "
                  f"mov(low/mid/high)={v['total_movements_low_8_24']:,}/{v['total_movements_mid_8_24']:,}/{v['total_movements_high_8_24']:,}")
