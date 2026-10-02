"""Gateway rule for the visitor-draw signal (attraction_pipeline.py):
which stations count as a main public-transport entrance to an
attraction, and how its weight is shared between them.

Pure geometry -- no database, no network. Coordinates are laid out on
a line of latitude near Berlin; 0.001 deg lon ~ 68m there.
"""

from __future__ import annotations

import unittest

from helpers import SRC  # noqa: F401  (puts src/ on sys.path)

from attraction_pipeline import attraction_kind, gateway_share

LAT = 52.52
M_PER_DEG_LON = 67.9


def at(metres_east: float) -> tuple[float, float]:
    return (LAT, 13.40 + metres_east / 1000 / M_PER_DEG_LON)


class GatewayShare(unittest.TestCase):
    def test_sole_nearest_station_takes_full_share(self):
        stations = {"Node": [at(0)], "Far": [at(1200)]}
        distance, share, co = gateway_share([at(300)], "Node", stations)
        self.assertAlmostEqual(distance, 300, delta=5)
        self.assertEqual(share, 1.0)
        self.assertEqual(co, [])

    def test_station_within_band_of_nearest_shares_weight(self):
        # Other station 200m from the site, node 350m: within the 250m band.
        stations = {"Node": [at(0)], "Other": [at(550)]}
        _, share, co = gateway_share([at(350)], "Node", stations)
        self.assertEqual(share, 0.5)
        self.assertEqual(co, ["Other"])

    def test_station_beyond_band_and_edge_is_not_a_gateway(self):
        # Other station 50m away, node 700m: outside band and edge.
        stations = {"Node": [at(0)], "Other": [at(750)]}
        self.assertIsNone(gateway_share([at(700)], "Node", stations))

    def test_long_site_counts_at_both_ends(self):
        # A 1.2km linear site with a station at each end: the node at the
        # far end (350m) is outside the band of the 20m station but
        # within the edge distance, so both qualify.
        stations = {"Node": [at(0)], "Other": [at(1570)]}
        site = [at(350 + i * 100) for i in range(13)]
        _, share, co = gateway_share(site, "Node", stations)
        self.assertEqual(share, 0.5)
        self.assertEqual(co, ["Other"])

    def test_beyond_max_gateway_distance_is_dropped(self):
        stations = {"Node": [at(0)]}
        self.assertIsNone(gateway_share([at(1700)], "Node", stations))


class AttractionKind(unittest.TestCase):
    def test_amenity_wins_over_tourism(self):
        # A club also tagged tourism=attraction is a night venue.
        self.assertEqual(attraction_kind({"amenity": "nightclub", "tourism": "attraction"}), "nightclub")

    def test_untyped_element_is_none(self):
        self.assertIsNone(attraction_kind({"name": "x", "wikidata": "Q1"}))


if __name__ == "__main__":
    unittest.main()
