"""Site input: the candidate sites a run scores, as a JSON file.

Default: examples/berlin_three_nodes.json (the Berlin case study).
Another file: set SITE_INPUT, or pass --sites to src/cli.py. models.py
reads it at import, so CANDIDATE_NODES is the same for every module in a
run. Kept free of project imports so the CLI can validate a file before
anything loads it.

    {"sites": [{"area_id": "alexanderplatz", "name": "Alexanderplatz",
                "lat": 52.5215, "lon": 13.4113, "radius_m": 400,
                "gtfs_stations": ["S+U Alexanderplatz Bhf (Berlin)"]}]}

gtfs_stations: the node's VBB parent-station name(s), primary first --
`python3 src/cli.py validate` checks them against the GTFS snapshot.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SITE_INPUT_ENV = "SITE_INPUT"
DEFAULT_SITE_INPUT = PROJECT_ROOT / "examples" / "berlin_three_nodes.json"

# Every data source in the pipeline (LOR, MSS, IHK, VBB) covers Berlin
# only, and the anchors are calibrated on inner-Berlin stations.
BERLIN_BBOX = (52.33, 13.08, 52.68, 13.77)  # south, west, north, east
# The scoring anchors are percentiles of 400m circles around 139
# reference stations (calibrate_anchors.RADIUS_M); a site with another
# radius would be scored on the wrong scale.
CALIBRATED_RADIUS_M = 400
REQUIRED = ("area_id", "name", "lat", "lon", "radius_m", "gtfs_stations")
_AREA_ID = re.compile(r"^[a-z0-9_]+$")


class SiteInputError(ValueError):
    """The site input file is missing, malformed, or out of scope."""


def site_input_path() -> Path:
    return Path(os.environ.get(SITE_INPUT_ENV) or DEFAULT_SITE_INPUT)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_sites(path: Path) -> list[dict]:
    """Validated sites as plain dicts. Raises SiteInputError with a
    message naming the file, the site and the field."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SiteInputError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise SiteInputError(f"{path}: not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})") from None
    sites = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(sites, list) or not sites:
        raise SiteInputError(f'{path}: expected an object with a non-empty "sites" list')

    out, seen = [], set()
    for i, site in enumerate(sites):
        where = f"{path}: sites[{i}]"
        if not isinstance(site, dict):
            raise SiteInputError(f"{where}: expected an object")
        missing = [k for k in REQUIRED if k not in site]
        if missing:
            raise SiteInputError(f"{where}: missing {', '.join(missing)}")
        area_id = site["area_id"]
        if not isinstance(area_id, str) or not _AREA_ID.match(area_id):
            raise SiteInputError(f"{where}: area_id must be lowercase letters, digits and underscores, got {area_id!r}")
        if area_id in seen:
            raise SiteInputError(f"{where}: duplicate area_id {area_id!r}")
        seen.add(area_id)
        where = f"{where} ({area_id})"
        lat, lon, radius = site["lat"], site["lon"], site["radius_m"]
        if not all(_number(v) for v in (lat, lon, radius)):
            raise SiteInputError(f"{where}: lat, lon and radius_m must be numbers")
        s, w, n, e = BERLIN_BBOX
        if not (s <= lat <= n and w <= lon <= e):
            raise SiteInputError(f"{where}: ({lat}, {lon}) is outside Berlin; every data source covers Berlin only")
        if radius != CALIBRATED_RADIUS_M:
            raise SiteInputError(f"{where}: radius_m must be {CALIBRATED_RADIUS_M}, got {radius}: the scoring anchors "
                                 f"are calibrated on {CALIBRATED_RADIUS_M}m circles (another radius needs "
                                 f"src/calibrate_anchors.py and calibrate_demand.py re-run at that radius)")
        gtfs = site["gtfs_stations"]
        if not isinstance(gtfs, list) or not gtfs or not all(isinstance(g, str) and g for g in gtfs):
            raise SiteInputError(f"{where}: gtfs_stations must be a non-empty list of VBB stop names")
        out.append({"area_id": area_id, "name": str(site["name"]), "lat": float(lat), "lon": float(lon),
                    "radius_m": int(radius), "gtfs_stations": tuple(gtfs)})
    return out
