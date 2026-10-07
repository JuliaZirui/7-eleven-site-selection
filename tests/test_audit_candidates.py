"""Data-plausibility audit, Phase 1 (plan_llm_data_audit.md): the
corrections-off switch and the deterministic candidate generator. No LLM."""

from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sets sys.path for src/, blocks the network)

import audit_candidates as ac
import audit_eval
import demand_model
import metrics_pipeline
from audit_candidates import NodeContext

LAT, LON = 52.52, 13.40
FIT = {"intercept": 0.0, "slope": 1.0}  # visitors = pageviews


def _offset(dx_m: float, dy_m: float) -> tuple[float, float]:
    """(lat, lon) dx_m east and dy_m north of the node."""
    return LAT + dy_m / 110_540.0, LON + dx_m / (111_320.0 * math.cos(math.radians(LAT)))


def _reg(rid: str, employees: str, dx: float = 0.0, dy: float = 0.0, **extra) -> dict:
    lat, lon = _offset(dx, dy)
    return {"opendata_id": rid, "employees_range": employees, "lat": lat, "lon": lon, **extra}


def _square(eid: int, dx: float, dy: float, half: float, **tags) -> dict:
    """A closed way: a square of side 2*half metres centred dx/dy from the node."""
    corners = [(-half, -half), (half, -half), (half, half), (-half, half), (-half, -half)]
    geom = [dict(zip(("lat", "lon"), _offset(dx + x, dy + y))) for x, y in corners]
    return {"type": "way", "id": eid, "geometry": geom, "tags": tags}


def _node(eid: int, dx: float, dy: float, **tags) -> dict:
    lat, lon = _offset(dx, dy)
    return {"type": "node", "id": eid, "lat": lat, "lon": lon, "tags": tags}


def _competitor(ref: str, name: str, category: str, dx: float, dy: float, source: str = "overture_places") -> dict:
    lat, lon = _offset(dx, dy)
    return {"external_ref": ref, "name": name, "category": category, "lat": lat, "lon": lon,
            "payload": {"providers": ["meta"], "members": [{"source": source, "ref": ref, "name": name,
                                                             "category": category}]}}


def _ctx(registrations=(), visitors=(), competitors=(), osm=(), **kw) -> NodeContext:
    return NodeContext(area_id="test", name="Test", lat=LAT, lon=LON, radius_m=400,
                       registrations=list(registrations), visitor_records=list(visitors),
                       competitors=list(competitors), osm_elements=list(osm), **kw)


def _ids(wl: dict, module: str) -> set[str]:
    return {c["candidate_id"] for c in wl["candidates"] if c["module"] == module}


class CorrectionsSwitchTest(helpers.PipelineTestCase):
    """corrections=False reproduces the pre-review state."""

    def test_loaders_return_nothing_with_corrections_off(self):
        self.assertTrue(demand_model.load_onsite_verifications())
        self.assertEqual(demand_model.load_onsite_verifications(corrections=False), {})
        self.assertTrue(demand_model.load_unregistered_employers())
        self.assertEqual(demand_model.load_unregistered_employers(corrections=False), [])
        fit, _ = demand_model.latest_visitor_fit()
        self.assertTrue(demand_model.load_known_attendance(fit))
        self.assertEqual(demand_model.load_known_attendance(fit, corrections=False), {})

    def test_answer_key_registration_shows_its_raw_value(self):
        # The key's 5,000-7,499 registration: verified ~450 on site, 6,123 raw.
        reg = [{"opendata_id": "17303106124", "employees_range": "5000 - 7499 Beschäftigte"}]
        on = demand_model.workers_onsite(reg, demand_model.load_onsite_verifications())
        off = demand_model.workers_onsite(reg, demand_model.load_onsite_verifications(corrections=False))
        self.assertEqual(on["central"], 450)
        self.assertEqual(round(off["central"]), 6123)
        self.assertEqual(off["large_unverified"], ["17303106124"])

    def test_metrics_written_with_corrections_off_are_labelled(self):
        node = self.nodes[0]
        snap = self.add_snapshot("ihk_gewerbedaten")
        payload = {"opendata_id": "17303106124", "employees_range": "5000 - 7499 Beschäftigte",
                   "branch_top_level_id": "64"}
        self.conn.execute(
            "INSERT INTO observations (snapshot_id, area_id, entity_type, external_ref, name, category, lat, lon, "
            "opening_hours_raw, payload_json) VALUES (?, ?, 'business', 'x', NULL, 'bank', ?, ?, NULL, ?)",
            (snap, node.area_id, node.lat, node.lon, json.dumps(payload)))
        metrics_pipeline._compute_business_mix(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "worker_headcount_estimate")["value"], 450)
        metrics_pipeline._compute_business_mix(self.conn, node, corrections=False)
        self.assertEqual(round(self.metric(node.area_id, "worker_headcount_estimate")["value"]), 6123)
        version = self.conn.execute("SELECT computation_method_version FROM area_metrics ORDER BY metric_id DESC "
                                    "LIMIT 1").fetchone()[0]
        self.assertTrue(version.endswith(metrics_pipeline.CORRECTIONS_OFF_SUFFIX))

    def test_corrections_off_refuses_the_real_database(self):
        with self.assertRaises(ValueError):
            metrics_pipeline.run(metrics_pipeline.DB_PATH, corrections=False)


class CandidateModulesTest(unittest.TestCase):
    def test_concentration_by_share_and_by_absolute_size(self):
        regs = [_reg("big", "500 - 999 Beschäftigte"), _reg("s1", "10 - 19 Beschäftigte"),
                _reg("s2", "10 - 19 Beschäftigte")]
        visitors = [{"qid": "Q1", "name": "Club", "pageviews": 365_000 * 3, "share": 1 / 3},  # 1,000 / day
                    {"qid": "Q2", "name": "Statue", "pageviews": 36_500, "share": 1.0}]  # 100 / day, < 25%
        wl = ac.build_worklist(_ctx(regs, visitors), FIT)
        self.assertEqual(_ids(wl, "concentration"), {"concentration:ihk:big", "concentration:wikidata:Q1"})
        club = next(c for c in wl["candidates"] if c["candidate_id"] == "concentration:wikidata:Q1")
        self.assertAlmostEqual(club["value_people_per_day"], 1000.0)

    def test_concentration_respects_the_station_rule(self):
        regs = [_reg("big", "500 - 999 Beschäftigte", other=True), _reg("s1", "10 - 19 Beschäftigte")]
        wl = ac.build_worklist(_ctx(regs, uses_this_station=lambda r: not r.get("other")), FIT)
        self.assertNotIn("concentration:ihk:big", _ids(wl, "concentration"))
        self.assertIn("large_registration:ihk:big", _ids(wl, "large_registration"))  # still audited

    def test_large_registration_carries_osm_names_within_40m(self):
        regs = [_reg("big", "500 - 999 Beschäftigte"), _reg("small", "250 - 499 Beschäftigte")]
        osm = [_square(1, 20, 0, 10, building="office", name="Near Tower", **{"building:levels": "8"}),
               _node(2, 80, 0, office="company", name="Far Office")]
        wl = ac.build_worklist(_ctx(regs, osm=osm), FIT)
        self.assertEqual(_ids(wl, "large_registration"), {"large_registration:ihk:big"})
        hints = wl["candidates"][[c["candidate_id"] for c in wl["candidates"]].index(
            "large_registration:ihk:big")]["osm_names_within_40m"]
        self.assertEqual([h.get("name") for h in hints], ["Near Tower"])

    def test_missing_entity_rules(self):
        osm = [
            # workplace tag, no registration within 40 m -> candidate
            _node(1, 200, 0, diplomatic="embassy", name="Embassy"),
            # workplace tag, a registration 10 m away -> not a candidate
            _node(2, -200, 0, office="company", name="Registered Office"),
            # 30x30 m, 10 levels = 9,000 m²; 2 small firms registered at its foot -> candidate (v2 rule)
            _square(3, 0, 200, 15, building="commercial", name="Tower", **{"building:levels": "10"}),
            # same size, residential -> never a workplace candidate
            _square(4, 0, -200, 15, building="apartments", **{"building:levels": "10"}),
            # same size, well registered (1 per 100 m² or more) -> not a candidate
            _square(5, 200, 200, 15, building="office", **{"building:levels": "10"}),
            # outside the circle
            _node(6, 500, 0, government="ministry", name="Outside"),
        ]
        regs = [_reg("r2", "1 - 3 Beschäftigte", -190, 0), _reg("r3a", "1 - 3 Beschäftigte", 0, 220),
                _reg("r3b", "4 - 6 Beschäftigte", 0, 220), _reg("r5", "250 - 499 Beschäftigte", 200, 200)]
        wl = ac.build_worklist(_ctx(regs, osm=osm), FIT)
        self.assertEqual(_ids(wl, "missing_entity"), {"missing_entity:osm:node/1", "missing_entity:osm:way/3"})
        tower = next(c for c in wl["candidates"] if c["candidate_id"] == "missing_entity:osm:way/3")
        self.assertEqual(tower["floor_area_m2"], 9000)
        self.assertEqual(tower["ihk_within_40m"]["registrations"], 2)

    def test_tagged_node_inside_a_candidate_building_is_folded_into_it(self):
        osm = [_square(1, 0, 100, 30, building="yes", name="Block", **{"building:levels": "6"}),
               _node(2, 0, 100, office="government", name="Agency")]
        wl = ac.build_worklist(_ctx(osm=osm), FIT)
        self.assertEqual(_ids(wl, "missing_entity"), {"missing_entity:osm:way/1"})
        self.assertEqual(wl["candidates"][0]["tagged_objects_inside"][0]["name"], "Agency")

    def test_missing_entity_skipped_without_osm_context(self):
        ctx = _ctx()
        ctx.osm_elements = None
        wl = ac.build_worklist(ctx, FIT)
        self.assertEqual(wl["counts"]["missing_entity"], 0)
        self.assertTrue(wl["modules_skipped"])

    def test_poi_category_by_name_word_and_by_same_name_osm_object(self):
        comps = [_competitor("o:1", "Grill Steakhouse", "cafe", 0, 0),
                 _competitor("o:2", "Lindenhof", "cafe", 100, 0),  # same-name office building in OSM
                 _competitor("o:3", "Kaffee Bar", "cafe", -100, 0),  # nothing wrong
                 _competitor("n:4", "Lindenhof", "cafe", 0, 100, source="osm_overpass")]  # OSM's own record
        osm = [_square(10, 100, 0, 12, building="office", name="Lindenhof"),
               _square(11, 0, 100, 12, building="office", name="Lindenhof")]
        wl = ac.build_worklist(_ctx(competitors=comps, osm=osm), FIT)
        self.assertEqual(_ids(wl, "poi_category"), {"poi_category:o:1", "poi_category:o:2"})

    def test_non_food_words_match_whole_words_only(self):
        wl = ac.build_worklist(_ctx(competitors=[_competitor("o:1", "Hofbäckerei", "bakery", 0, 0)]), FIT)
        self.assertEqual(_ids(wl, "poi_category"), set())

    def test_poi_duplicate_same_category_within_15m(self):
        comps = [_competitor("a", "Bäckerei Eins", "bakery", 0, 0), _competitor("b", "Backshop Zwei", "bakery", 8, 0),
                 _competitor("c", "Späti", "spati_kiosk", 4, 0), _competitor("d", "Bäckerei Drei", "bakery", 40, 0)]
        wl = ac.build_worklist(_ctx(competitors=comps), FIT)
        self.assertEqual(_ids(wl, "poi_duplicate"), {"poi_duplicate:a|b"})

    def test_worklist_is_deterministic(self):
        regs = [_reg("big", "1000 - 2499 Beschäftigte")]
        osm = [_square(1, 0, 100, 30, building="yes", **{"building:levels": "6"})]
        comps = [_competitor("a", "X", "bakery", 0, 0), _competitor("b", "Y", "bakery", 5, 0)]
        one = ac.build_worklist(_ctx(regs, competitors=comps, osm=osm), FIT)
        two = ac.build_worklist(_ctx(regs, competitors=list(reversed(comps)), osm=list(reversed(osm))), FIT)
        self.assertEqual(sorted(c["candidate_id"] for c in one["candidates"]),
                         sorted(c["candidate_id"] for c in two["candidates"]))


class AuditSetsTest(unittest.TestCase):
    def test_generalisation_draw_reproduces_the_key(self):
        self.assertEqual(audit_eval.redraw_generalisation_stations(), list(audit_eval.AUDIT_SETS["generalisation"]))

    def test_every_answer_key_item_has_a_matcher(self):
        key = audit_eval.ANSWER_KEY.read_text(encoding="utf-8")
        ids = {line.split("|")[1].strip().split()[0] for line in key.splitlines() if re.match(r"^\| [DH]\d", line)}
        self.assertEqual(ids, {i["id"] for i in audit_eval.answer_key_items()})

    def test_osm_context_query_covers_the_margin(self):
        q = ac.osm_context_query(LAT, LON, 400 + ac.OSM_CONTEXT_MARGIN_M)
        self.assertIn("around:450,", q)
        self.assertIn('way(around:450', q)


if __name__ == "__main__":
    unittest.main()
