"""Shared geometry helpers. Kept tiny and dependency-free (shapely is
used only where actual polygon geometry is needed, in lor_pipeline.py).
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def distance_to_path_m(lat: float, lon: float, path: list[tuple[float, float]]) -> float:
    """Shortest distance in meters from a point to a polyline of
    (lat, lon) vertices (a single vertex = a point). Uses a local
    equirectangular projection around the query point -- exact enough
    for the tens-of-meters station-proximity test it serves."""
    if not path:
        return math.inf
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    pts = [((plon - lon) * kx, (plat - lat) * ky) for plat, plon in path]
    if len(pts) == 1:
        return math.hypot(*pts[0])
    best = math.inf
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg_len2 = dx * dx + dy * dy
        t = 0.0 if seg_len2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg_len2))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best
