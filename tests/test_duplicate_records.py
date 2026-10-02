"""Fixed test case 2/4: duplicate records.

Three layers of duplicate protection are exercised:
  1. clean_and_classify(): the same physical shop mapped twice in OSM
     (e.g. an entrance node + a building way) within 25m collapses to one.
  2. The observations dedup index: the same external_ref can't be written
     twice into one snapshot.
  3. Snapshots are appended, never overwritten -- so a re-fetched area
     holds the same POI in two snapshots, and every consumer must read
     only the latest snapshot or it double-counts.
Plus the cross-source rule: records from different sources are never
summed (IHK businesses are not competitors).
"""

from __future__ import annotations

import sqlite3
import unittest

from helpers import PipelineTestCase

import agent_followup
import metrics_pipeline
import poi_pipeline
import report
import scoring


def _el(el_type, el_id, tags, lat=52.5200, lon=13.4000, center=False):
    e = {"type": el_type, "id": el_id, "tags": tags}
    if center:
        e["center"] = {"lat": lat, "lon": lon}
    else:
        e.update({"lat": lat, "lon": lon})
    return e


class CleanAndClassifyDedup(unittest.TestCase):
    def test_same_name_within_25m_is_one_poi(self):
        elements = [
            _el("node", 1, {"shop": "convenience", "name": "Späti Eck"}),
            # Same shop mapped as a building way; center ~11m away, name differs only in case/punctuation.
            _el("way", 2, {"shop": "convenience", "name": "SPÄTI-ECK"}, lat=52.5201, center=True),
        ]
        kept, dropped = poi_pipeline.clean_and_classify(elements)
        self.assertEqual((len(kept), dropped), (1, 1))
        self.assertEqual(kept[0]["external_ref"], "node/1")

    def test_same_name_far_apart_is_two_branches(self):
        elements = [
            _el("node", 1, {"amenity": "fast_food", "name": "Curry 36"}),
            _el("node", 2, {"amenity": "fast_food", "name": "Curry 36"}, lat=52.5206),  # ~67m
        ]
        kept, dropped = poi_pipeline.clean_and_classify(elements)
        self.assertEqual((len(kept), dropped), (2, 0))

    def test_unnamed_pois_are_never_merged_blind(self):
        elements = [
            _el("node", 1, {"shop": "kiosk"}),
            _el("node", 2, {"shop": "kiosk"}, lat=52.52001),
        ]
        kept, dropped = poi_pipeline.clean_and_classify(elements)
        self.assertEqual((len(kept), dropped), (2, 0))

    def test_different_names_same_spot_are_two_pois(self):
        elements = [
            _el("node", 1, {"amenity": "cafe", "name": "A"}),
            _el("node", 2, {"amenity": "cafe", "name": "B"}),
        ]
        self.assertEqual(len(poi_pipeline.clean_and_classify(elements)[0]), 2)

    def test_out_of_scope_and_coordinate_less_elements_are_dropped_not_counted(self):
        elements = [
            _el("node", 1, {"amenity": "fuel"}),  # fuel station without a shop
            _el("node", 2, {"shop": "clothes", "name": "X"}),
            {"type": "way", "id": 3, "tags": {"shop": "bakery", "name": "No coords"}},
        ]
        kept, dropped = poi_pipeline.clean_and_classify(elements)
        self.assertEqual((len(kept), dropped), (0, 0))


class ObservationDedupIndex(PipelineTestCase):
    def test_exact_duplicate_element_in_one_response_is_written_once(self):
        # Unnamed, so clean_and_classify keeps both -- the DB index is the backstop.
        payload = {"elements": [_el("node", 7, {"shop": "kiosk"}), _el("node", 7, {"shop": "kiosk"})]}
        self.mock_network("poi_pipeline.fetch_overpass_pois", return_value=(payload, "https://mock"))
        result = poi_pipeline.ingest_area(self.conn, self.nodes[0])
        self.assertEqual(result["kept"], 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM observations WHERE snapshot_id = ?", result["snapshot_id"]), 1)

    def test_index_rejects_plain_insert_of_same_ref_in_same_snapshot(self):
        snap = self.add_snapshot("osm_overpass")
        self.add_poi(snap, self.nodes[0].area_id, "node/1", "A", "cafe", 52.52, 13.40, None)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_poi(snap, self.nodes[0].area_id, "node/1", "A", "cafe", 52.52, 13.40, None)


class RefetchDoesNotDoubleCount(PipelineTestCase):
    """An Agent refetch (Day 4) appends a second snapshot of the same
    POIs. Metrics, scoring and the report must read the latest one."""

    def _two_osm_snapshots(self):
        area = self.nodes[0].area_id
        old = self.add_snapshot("osm_overpass", fetched_at="2026-09-01T10:00:00+00:00")
        for i in range(3):
            self.add_poi(old, area, f"node/{i}", f"Shop {i}", "spati_kiosk", 52.5201 + i * 1e-4, 13.4001, None)
        new = self.add_snapshot("osm_overpass", fetched_at="2026-09-20T10:00:00+00:00")
        for i in range(3):
            # Same shops, now with hours recorded; plus one new opening.
            self.add_poi(new, area, f"node/{i}", f"Shop {i}", "spati_kiosk", 52.5201 + i * 1e-4, 13.4001, "24/7")
        self.add_poi(new, area, "node/9", "New", "cafe", 52.5209, 13.4001, "Mo-Fr 08:00-18:00")
        return old, new

    def test_metrics_count_latest_snapshot_only(self):
        self._two_osm_snapshots()
        node = self.nodes[0]
        metrics_pipeline.compute_area(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "competitor_count_total")["value"], 4.0)
        self.assertEqual(self.metric(node.area_id, "competitor_count_spati_kiosk")["value"], 3.0)
        self.assertEqual(self.metric(node.area_id, "competitor_hours_unknown_count")["value"], 0.0)

    def test_agent_does_not_flag_hours_already_filled_by_a_newer_snapshot(self):
        self._two_osm_snapshots()
        gaps = agent_followup._detect_missing_poi_hours(self.conn, self.nodes[0].area_id,
                                                        agent_followup.datetime.now(agent_followup.timezone.utc))
        self.assertEqual(gaps, [])

    def test_superseded_old_snapshot_is_not_a_stale_gap(self):
        # The old snapshot is 20+ days old, but a refetch replaced it --
        # flagging it again would make the Agent refetch forever.
        self._two_osm_snapshots()
        now = agent_followup.datetime(2026, 9, 24, tzinfo=agent_followup.timezone.utc)
        self.assertEqual(agent_followup._detect_stale_snapshots(self.conn, self.nodes[0].area_id, now), [])

    def test_ihk_business_mix_counts_latest_snapshot_only(self):
        area = self.nodes[0].area_id
        for fetched in ("2026-09-01T10:00:00+00:00", "2026-09-20T10:00:00+00:00"):
            snap = self.add_snapshot("ihk_gewerbedaten", fetched_at=fetched)
            self.add_business(snap, area, "b/1", "Gastronomie", "10 - 19 Beschäftigte", branch_id="56")
            self.add_business(snap, area, "b/2", "IT", "1 - 3 Beschäftigte")
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        self.assertEqual(self.metric(area, "registered_business_count")["value"], 2.0)
        self.assertEqual(self.metric(area, "businesses_10plus_employees_count")["value"], 1.0)

    def test_population_reads_latest_snapshot(self):
        area = self.nodes[0].area_id
        old = self.add_snapshot("berlin_mss_population", fetched_at="2026-09-01T10:00:00+00:00")
        self.add_population(old, area, 1000)
        new = self.add_snapshot("berlin_mss_population", fetched_at="2026-09-20T10:00:00+00:00")
        self.add_population(new, area, 1200)
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        self.assertEqual(self.metric(area, "resident_population")["value"], 1200.0)

    def test_report_and_scoring_helpers_read_latest_snapshot(self):
        self._two_osm_snapshots()
        area = self.nodes[0].area_id
        self.assertEqual(len(report._pois(self.conn, area)), 4)
        for fetched in ("2026-09-01T10:00:00+00:00", "2026-09-20T10:00:00+00:00"):
            snap = self.add_snapshot("ihk_gewerbedaten", fetched_at=fetched)
            self.add_business(snap, area, "b/1", "Gastronomie", "1 - 3 Beschäftigte", branch_id="56")
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        results = {area: {"metrics": scoring.latest_metrics(self.conn, area)}}
        consistency = scoring.mapping_consistency(self.conn, results)
        self.assertEqual(consistency[area]["ihk_gastro"], 1)
        self.assertEqual(consistency[area]["osm"], 4)


class CrossSourceNeverSummed(PipelineTestCase):
    def test_ihk_businesses_are_not_competitors(self):
        area = self.nodes[0].area_id
        osm = self.add_snapshot("osm_overpass")
        self.add_poi(osm, area, "node/1", "Cafe", "cafe", 52.5201, 13.4001, None)
        ihk = self.add_snapshot("ihk_gewerbedaten")
        for i in range(5):
            self.add_business(ihk, area, f"b/{i}", "Gastronomie", "1 - 3 Beschäftigte", branch_id="56")
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        self.assertEqual(self.metric(area, "competitor_count_total")["value"], 1.0)
        self.assertEqual(self.metric(area, "registered_business_count")["value"], 5.0)


if __name__ == "__main__":
    unittest.main()
