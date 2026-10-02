"""Supplementary suite: multi-source POI coverage (v2).

Covers the pieces added to fix OSM under-counting: the widened
taxonomy and station-only shop types, the in-station test, Overture /
Foursquare quality gates, and the cross-source conflation that turns
three sources into one competitor set without double-counting.
Everything runs offline on hand-built records.
"""

from __future__ import annotations

import json
import unittest
from datetime import date

from helpers import PipelineTestCase, make_node

import metrics_pipeline
import poi_conflation
import poi_pipeline
from poi_taxonomy import POICategory, classify, classify_fsq, classify_overture

NODE = make_node(lat=52.52, lon=13.40)


def _rec(source, ref, name, lat=52.52, lon=13.40, category="cafe", hours=None, **payload):
    return {"source": source, "ref": ref, "name": name, "category": category, "lat": lat, "lon": lon,
            "opening_hours_raw": hours, "payload": payload}


class Taxonomy(unittest.TestCase):
    def test_new_categories(self):
        self.assertEqual(classify({"shop": "beverages"}), POICategory.BEVERAGES)
        self.assertEqual(classify({"shop": "chemist", "name": "dm"}), POICategory.CHEMIST)
        self.assertEqual(classify({"shop": "newsagent"}), POICategory.SPATI_KIOSK)
        self.assertIsNone(classify({"amenity": "pharmacy"}))  # Apotheke is not a Drogerie

    def test_station_only_shop_types(self):
        self.assertIsNone(classify({"shop": "tobacco"}))
        self.assertEqual(classify({"shop": "tobacco"}, in_station=True), POICategory.SPATI_KIOSK)
        self.assertEqual(classify({"shop": "confectionery"}, in_station=True), POICategory.OTHER_FRESH_FOOD)

    def test_overture_most_specific_level_wins(self):
        hierarchy = ["food_and_drink", "restaurant", "middle_eastern_restaurant", "turkish_restaurant",
                     "doner_kebab_restaurant"]
        self.assertEqual(classify_overture(hierarchy), POICategory.FAST_FOOD)
        self.assertIsNone(classify_overture(["food_and_drink", "restaurant", "italian_restaurant"]))
        self.assertEqual(classify_overture(["shopping", "specialty_store", "pharmacy_and_drug_store", "drugstore"]),
                         POICategory.CHEMIST)

    def test_fsq_leaf_labels_accent_insensitive(self):
        self.assertEqual(classify_fsq(["Dining and Drinking > Cafe, Coffee, and Tea House > Café"]), POICategory.CAFE)
        self.assertEqual(classify_fsq(["Retail > Convenience Store"]), POICategory.SPATI_KIOSK)
        self.assertIsNone(classify_fsq(["Retail > Clothing Store"]))


class InStationFlag(unittest.TestCase):
    def _elements(self, shop_lat):
        platform = {"type": "way", "id": 10, "tags": {"railway": "platform"},
                    "geometry": [{"lat": 52.5200, "lon": 13.4000}, {"lat": 52.5200, "lon": 13.4020}]}
        shop = {"type": "node", "id": 1, "lat": shop_lat, "lon": 13.4010, "tags": {"shop": "tobacco", "name": "Tabak"}}
        return [platform, shop, {"type": "count", "id": 0, "tags": {"ways": "100"}}]

    def test_shop_next_to_platform_line_is_in_station(self):
        # ~20m from the middle of the platform line, ~70m from either end node.
        kept, _ = poi_pipeline.clean_and_classify(self._elements(52.52018))
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["in_station"])
        self.assertEqual(kept[0]["category"], "spati_kiosk")

    def test_station_only_type_off_station_is_dropped(self):
        kept, _ = poi_pipeline.clean_and_classify(self._elements(52.5210))  # ~110m away
        self.assertEqual(kept, [])

    def test_station_anchors_and_canary_are_never_competitors(self):
        kept, _ = poi_pipeline.clean_and_classify(self._elements(52.52018))
        self.assertEqual([k["external_ref"] for k in kept], ["node/1"])


class SourceQualityGates(unittest.TestCase):
    def test_overture_drops_closed_and_low_confidence(self):
        base = {"lat": 52.52, "lon": 13.40, "category_hierarchy": ["food_and_drink", "casual_eatery", "cafe"],
                "category_primary": "cafe", "upstream_datasets": ["meta"]}
        rows = [
            {**base, "id": "a", "name": "Open", "confidence": 0.9, "operating_status": "open"},
            {**base, "id": "b", "name": "Closed", "confidence": 0.9, "operating_status": "permanently_closed"},
            {**base, "id": "c", "name": "Register row", "confidence": 0.5, "operating_status": "open"},
        ]
        records, dropped = poi_pipeline.normalize_overture(rows)
        self.assertEqual([r["external_ref"] for r in records], ["overture:a"])
        self.assertEqual((dropped["closed"], dropped["low_confidence"]), (1, 1))

    def test_fsq_drops_closed_and_stale_and_reports_unmapped(self):
        rows = [
            {"fsq_place_id": "1", "name": "Fresh", "lat": 52.52, "lon": 13.4, "date_refreshed": "2026-05-01",
             "date_closed": None, "fsq_category_labels": ["Retail > Convenience Store"]},
            {"fsq_place_id": "2", "name": "Stale", "lat": 52.52, "lon": 13.4, "date_refreshed": "2021-01-01",
             "date_closed": None, "fsq_category_labels": ["Retail > Convenience Store"]},
            {"fsq_place_id": "3", "name": "Gone", "lat": 52.52, "lon": 13.4, "date_refreshed": "2026-05-01",
             "date_closed": "2026-06-01", "fsq_category_labels": ["Dining and Drinking > Bakery"]},
            {"fsq_place_id": "4", "name": "Shoes", "lat": 52.52, "lon": 13.4, "date_refreshed": "2026-05-01",
             "date_closed": None, "fsq_category_labels": ["Retail > Shoe Store"]},
        ]
        records, dropped, unmapped = poi_pipeline.normalize_fsq(rows, date(2026, 9, 28))
        self.assertEqual([r["external_ref"] for r in records], ["fsq:1"])
        self.assertEqual((dropped["stale"], dropped["closed"], dropped["out_of_scope"]), (1, 1, 1))
        self.assertEqual(unmapped["shoe store"], 1)


class NameMatching(unittest.TestCase):
    def test_generic_words_are_ignored(self):
        self.assertTrue(poi_conflation.names_match("ama", "ama Café"))
        self.assertTrue(poi_conflation.names_match("Cafe Einstein", "Berlin Einshtein Kaffee"))

    def test_generic_only_names_do_not_match_each_other(self):
        self.assertFalse(poi_conflation.names_match("Späti", "Kiosk"))
        self.assertFalse(poi_conflation.names_match("Curry 36", "Curry Wolf"))


class Conflation(unittest.TestCase):
    def _build(self, *records, station_paths=None):
        by_source: dict = {}
        for r in records:
            by_source.setdefault(r["source"], []).append(r)
        return poi_conflation.build_competitor_set(NODE, by_source, station_paths)

    def test_same_shop_across_three_sources_is_one_competitor(self):
        competitors, stats = self._build(
            _rec("osm_overpass", "node/1", "Wiedemann", hours="Mo-Su 07:00-20:00", in_station=False),
            _rec("overture_places", "overture:a", "Bäcker Wiedemann – Bäckerei Brandenburger Tor", lat=52.52020,
                 upstream_datasets=["meta"], confidence=0.97),
            _rec("fsq_os_places", "fsq:x", "Bäcker Wiedemann", lat=52.52010),
        )
        self.assertEqual(len(competitors), 1)
        c = competitors[0]
        self.assertEqual((c["external_ref"], c["opening_hours_raw"]), ("node/1", "Mo-Su 07:00-20:00"))
        self.assertEqual(c["payload"]["providers"], ["foursquare", "meta", "openstreetmap"])
        self.assertEqual(stats["corroborated"], 1)

    def test_overture_via_foursquare_and_fsq_count_as_one_provider(self):
        competitors, _ = self._build(
            _rec("overture_places", "overture:a", "Curry Fritze", upstream_datasets=["Foursquare"], confidence=0.77),
            _rec("fsq_os_places", "fsq:x", "Curry Fritze", lat=52.52005),
        )
        self.assertEqual(competitors[0]["payload"]["n_providers"], 1)

    def test_non_osm_only_shop_is_added(self):
        competitors, stats = self._build(
            _rec("osm_overpass", "node/1", "Wiedemann"),
            _rec("overture_places", "overture:b", "Starbucks", lat=52.5210, upstream_datasets=["meta"], confidence=1.0),
        )
        self.assertEqual(len(competitors), 2)
        self.assertEqual(stats["with_osm"], 1)

    def test_two_osm_branches_never_merge_through_a_third_source(self):
        # Two OSM Starbucks 70m apart, one Overture Starbucks between them.
        competitors, _ = self._build(
            _rec("osm_overpass", "node/1", "Starbucks", lat=52.52000),
            _rec("osm_overpass", "node/2", "Starbucks", lat=52.52063),
            _rec("overture_places", "overture:s", "Starbucks", lat=52.52030, upstream_datasets=["meta"], confidence=1.0),
        )
        self.assertEqual(sorted(c["external_ref"] for c in competitors), ["node/1", "node/2"])

    def test_unnamed_and_distant_records_stay_separate(self):
        competitors, _ = self._build(
            _rec("osm_overpass", "node/1", None, category="spati_kiosk"),
            _rec("overture_places", "overture:k", None, category="spati_kiosk", upstream_datasets=["meta"], confidence=0.9),
            _rec("fsq_os_places", "fsq:far", "Späti", lat=52.5215),  # ~170m from the others: no partner
        )
        self.assertEqual(len(competitors), 3)

    def test_records_outside_the_radius_are_cut_for_every_source(self):
        competitors, stats = self._build(
            _rec("osm_overpass", "way/9", "Edge", lat=52.5240),  # ~445m: way matched by `around` via one vertex
            _rec("overture_places", "overture:o", "Far", lat=52.5250, upstream_datasets=["meta"], confidence=0.9),
        )
        self.assertEqual((competitors, stats["outside_radius"]), ([], 2))

    def test_non_osm_record_gets_in_station_from_osm_anchors(self):
        station = [[(52.5200, 13.4000)]]
        competitors, _ = self._build(
            _rec("overture_places", "overture:k", "Kiosk", lat=52.52020, upstream_datasets=["meta"], confidence=0.9),
            station_paths=station,
        )
        self.assertTrue(competitors[0]["payload"]["in_station"])


class ConflatedSnapshotFeedsMetrics(PipelineTestCase):
    def _source_snapshot(self, source_name, records):
        snap = self.add_snapshot(source_name)
        for r in records:
            self.conn.execute(
                "INSERT INTO observations (snapshot_id, area_id, entity_type, external_ref, name, category, lat, lon, "
                "opening_hours_raw, payload_json) VALUES (?, ?, 'poi_source', ?, ?, ?, ?, ?, ?, ?)",
                (snap, self.nodes[0].area_id, r["ref"], r["name"], r["category"], r["lat"], r["lon"],
                 r["opening_hours_raw"], json.dumps(r["payload"])),
            )
        self.conn.commit()

    def test_metrics_read_conflated_set_and_its_provenance(self):
        area = self.nodes[0].area_id
        self._source_snapshot("osm_overpass", [_rec("osm_overpass", "node/1", "Wiedemann", in_station=True)])
        self._source_snapshot("overture_places", [
            _rec("overture_places", "overture:a", "Wiedemann", lat=52.52010, upstream_datasets=["meta"], confidence=0.9),
            _rec("overture_places", "overture:b", "Starbucks", lat=52.5210, upstream_datasets=["meta"], confidence=1.0),
        ])
        result = poi_conflation.conflate_area(self.conn, self.nodes[0])
        self.assertEqual((result["competitors"], result["missing_sources"]), (2, ["fsq_os_places"]))

        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        self.assertEqual(self.metric(area, "competitor_count_total")["value"], 2.0)  # not 3: never summed
        self.assertEqual(self.metric(area, "competitor_count_in_station")["value"], 1.0)
        self.assertEqual(self.metric(area, "competitor_count_corroborated")["value"], 1.0)
        self.assertEqual(self.metric(area, "osm_coverage_share")["value"], 0.5)

    def test_conflation_requires_an_osm_snapshot(self):
        self._source_snapshot("overture_places", [
            _rec("overture_places", "overture:a", "X", upstream_datasets=["meta"], confidence=0.9)])
        with self.assertRaises(poi_conflation.ConflationError):
            poi_conflation.conflate_area(self.conn, self.nodes[0])

    def test_legacy_single_source_set_leaves_provenance_metrics_missing_not_zero(self):
        area = self.nodes[0].area_id
        snap = self.add_snapshot("osm_overpass")
        self.add_poi(snap, area, "node/1", "Cafe", "cafe", 52.5201, 13.4001, None)
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        m = self.metric(area, "osm_coverage_share")
        self.assertEqual((m["value"], m["is_missing"]), (None, 1))


if __name__ == "__main__":
    unittest.main()
