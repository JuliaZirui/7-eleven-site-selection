"""Fixed test case 1/4: missing data.

Rule under test (project key rule): unknown values are
never filled with 0 -- missing is explicitly flagged, and every layer
downstream (metrics, Agent gap detection, scoring, report) carries the
flag forward instead of papering over it.
"""

from __future__ import annotations

import sqlite3
import unittest

from helpers import PipelineTestCase, make_node

import agent_followup
import metrics_pipeline
import scoring


class MetricsMissingData(PipelineTestCase):
    def test_no_poi_snapshot_is_unknown_competition_not_zero(self):
        # No POI snapshot at all (e.g. the first fetch failed) is unknown
        # competition. Written as 0 it would score as an empty field and
        # push the node to the top of the ranking.
        node = self.nodes[0]
        metrics_pipeline.compute_area(self.conn, node)
        for name, period in (
            ("competitor_count_total", None),
            ("competitor_count_supermarket", None),
            ("competitor_density_per_km2", None),
            ("nearest_competitor_distance_m", None),
            ("competitor_hours_known_count", None),
            ("competitors_open_morning_peak", "weekday_We"),
            ("morning_peak_competitor_count", "weekday_We"),
            ("evening_peak_competitor_count", "weekday_We"),
            ("weekday_all_day_competitor_count", "weekday_We"),
            ("weekend_all_day_competitor_count", "weekend_SaSu"),
            ("competitor_count_corroborated", None),
        ):
            m = self.metric(node.area_id, name, period)
            self.assertTrue(m["is_missing"], name)
            self.assertIsNone(m["value"], name)
            self.assertEqual(m["reason"], metrics_pipeline.NO_POI_SNAPSHOT, name)

    def test_category_absent_from_an_observed_set_is_a_real_zero(self):
        # With a snapshot on record, a format that simply isn't there is a
        # genuine 0 -- a chemist alone serves neither commute peak.
        node = self.nodes[0]
        snap = self.add_snapshot("osm_overpass")
        self.add_poi(snap, node.area_id, "node/1", "Drogerie", "chemist", 52.5201, 13.4001, None)
        metrics_pipeline.compute_area(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "competitor_count_total")["value"], 1.0)
        self.assertEqual(self.metric(node.area_id, "competitor_count_bakery")["value"], 0.0)
        for name in ("morning_peak_competitor_count", "evening_peak_competitor_count", "weekday_all_day_competitor_count"):
            self.assertEqual(self.metric(node.area_id, name, "weekday_We")["value"], 0.0, name)

    def test_missing_population_is_flagged_not_zero(self):
        node = self.nodes[0]
        snap = self.add_snapshot("berlin_mss_population")
        self.add_population(snap, node.area_id, population=None)
        metrics_pipeline.compute_area(self.conn, node)
        for name in ("resident_population", "competitor_density_per_1000_residents"):
            m = self.metric(node.area_id, name)
            self.assertTrue(m["is_missing"], name)
            self.assertIsNone(m["value"], name)

    def test_zero_population_does_not_divide(self):
        node = self.nodes[0]
        snap = self.add_snapshot("berlin_mss_population")
        self.add_population(snap, node.area_id, population=0)
        metrics_pipeline.compute_area(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "resident_population")["value"], 0.0)
        density = self.metric(node.area_id, "competitor_density_per_1000_residents")
        self.assertTrue(density["is_missing"])
        self.assertIn("undefined", density["reason"])

    def test_missing_ihk_and_lor_sources_flag_day5_inputs(self):
        node = self.nodes[0]
        metrics_pipeline.compute_area(self.conn, node)
        for name in ("registered_business_count", "businesses_10plus_employees_count", "office_business_share",
                     "catchment_population_estimate", "anchor_plr_catchment_share"):
            m = self.metric(node.area_id, name)
            self.assertTrue(m["is_missing"], name)
            self.assertTrue(m["reason"], name)

    def test_unknown_employee_size_is_not_counted_as_small_or_large(self):
        self.assertIsNone(metrics_pipeline._employees_lower_bound("unbekannt"))
        self.assertIsNone(metrics_pipeline._employees_lower_bound(None))
        self.assertEqual(metrics_pipeline._employees_lower_bound("0 Beschäftigte"), 0)
        self.assertEqual(metrics_pipeline._employees_lower_bound("10 - 19 Beschäftigte"), 10)
        node = self.nodes[0]
        snap = self.add_snapshot("ihk_gewerbedaten")
        self.add_business(snap, node.area_id, "b/1", "IT", "unbekannt")
        self.add_business(snap, node.area_id, "b/2", "IT", "20 - 49 Beschäftigte")
        metrics_pipeline.compute_area(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "registered_business_count")["value"], 2.0)
        self.assertEqual(self.metric(node.area_id, "businesses_10plus_employees_count")["value"], 1.0)

    def test_unknown_hours_are_tracked_separately_not_counted_closed_or_open(self):
        node = self.nodes[0]
        snap = self.add_snapshot("osm_overpass")
        self.add_poi(snap, node.area_id, "node/1", "Known", "cafe", 52.5201, 13.4001, "Mo-Su 07:00-20:00")
        self.add_poi(snap, node.area_id, "node/2", "Unknown", "cafe", 52.5202, 13.4002, None)
        metrics_pipeline.compute_area(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "competitor_hours_known_count")["value"], 1.0)
        self.assertEqual(self.metric(node.area_id, "competitor_hours_unknown_count")["value"], 1.0)
        self.assertEqual(self.metric(node.area_id, "competitors_open_morning_peak", "weekday_We")["value"], 1.0)

    def test_schema_rejects_a_missing_value_disguised_as_present(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO area_metrics (area_id, metric_name, metric_value, is_missing, unit, period_label, "
                "computation_method_version, computed_at) VALUES (?, 'x', NULL, 0, 'count', 'current', 'v', 'now')",
                (self.nodes[0].area_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO area_metrics (area_id, metric_name, metric_value, is_missing, unit, period_label, "
                "computation_method_version, computed_at) VALUES (?, 'x', 0, 1, 'count', 'current', 'v', 'now')",
                (self.nodes[0].area_id,),
            )

    def test_writer_refuses_missing_value_without_a_reason(self):
        with self.assertRaises(ValueError):
            metrics_pipeline._write_metric(
                self.conn, area_id=self.nodes[0].area_id, metric_name="x", value=None, unit="count",
                period_label="current", method_version="v", snapshot_ids=[],
            )


class AgentMissingData(PipelineTestCase):
    def test_missing_metric_and_missing_hours_become_gaps_with_the_right_tool(self):
        node = self.nodes[0]
        snap = self.add_snapshot("osm_overpass")
        self.add_poi(snap, node.area_id, "node/1", "NoHours", "fast_food", 52.5201, 13.4001, None)
        metrics_pipeline.compute_area(self.conn, node)  # no population -> missing metrics
        gaps = agent_followup.detect_gaps(self.conn, node.area_id)
        types = {g.gap_type for g in gaps}
        self.assertIn(agent_followup.GapType.MISSING_METRIC, types)
        self.assertIn(agent_followup.GapType.MISSING_POI_HOURS, types)
        pop_gap = next(g for g in gaps if g.extra.get("metric_name") == "resident_population")
        self.assertEqual(agent_followup.select_tool(pop_gap), agent_followup.TOOL_REFETCH_LOR)
        hours_gap = next(g for g in gaps if g.gap_type == agent_followup.GapType.MISSING_POI_HOURS)
        # Fresh snapshot: a same-day re-query can't add hours -> field research, no live call.
        self.assertEqual(agent_followup.select_tool(hours_gap), agent_followup.TOOL_FLAG_FIELD_RESEARCH)


PV = "pageviews 2025-09-01..2026-09-01 (end exclusive)"


class ScoringMissingData(PipelineTestCase):
    def _metrics_with(self, overrides: dict | None = None):
        """Synthetic latest_metrics() dict: every scoring input present
        unless overridden to None (= flagged missing). People per day =
        6,000 residents + 5,000 workers + 4,000 visitors = 15,000."""
        base = {
            ("catchment_population_estimate", "202412"): 6000.0,
            ("worker_headcount_estimate", "current"): 5000.0,
            ("visitor_weekday_estimate", PV): 4000.0,
            ("worker_headcount_weekend", "current"): 500.0,
            ("visitor_weekend_estimate", PV): 9000.0,
            ("weekday_all_day_competitor_count", "weekday_We"): 30.0,
            ("weekend_all_day_competitor_count", "weekend_SaSu"): 40.0,
            ("competitor_count_corroborated", "current"): 10.0,
            ("competitor_count_total", "current"): 20.0,
        }
        base.update(overrides or {})
        out = {}
        for i, ((name, period), value) in enumerate(base.items()):
            out[(name, period)] = {
                "metric_id": i + 1, "metric_name": name, "period_label": period, "value": value,
                "is_missing": value is None, "missing_reason": "test: forced missing" if value is None else None,
            }
        return out

    def test_demand_sums_segments_in_one_unit(self):
        crits = {c["key"]: c for c in scoring._criterion_results(self._metrics_with())}
        self.assertAlmostEqual(crits["daily_people"]["raw_value"], 15_000.0)
        self.assertEqual(crits["daily_people"]["breakdown"], {"residents": 6000.0, "workers": 5000.0, "visitors": 4000.0})
        self.assertEqual(len(crits["daily_people"]["metric_ids"]), 3)  # every segment cited

    def test_missing_segment_makes_demand_missing_not_a_smaller_sum(self):
        # Summing the other two would read a data gap as fewer people.
        crits = {c["key"]: c for c in scoring._criterion_results(
            self._metrics_with({("visitor_weekday_estimate", PV): None}))}
        self.assertIsNone(crits["daily_people"]["raw_value"])
        self.assertIn("visitors", crits["daily_people"]["missing_reason"])

    def test_missing_competition_pillar_renormalizes_weights(self):
        missing = {("weekday_all_day_competitor_count", "weekday_We"): None,
                   ("weekend_all_day_competitor_count", "weekend_SaSu"): None}
        pillars = scoring.pillar_scores(scoring._criterion_results(self._metrics_with(missing)))
        self.assertIsNone(pillars["competition"])
        self.assertAlmostEqual(scoring.business_fit(pillars, scoring.DEFAULT_WEIGHTS), pillars["demand"])
        self.assertIsNone(scoring.business_fit({p: None for p in scoring.PILLARS}, scoring.DEFAULT_WEIGHTS))

    def test_missing_demand_makes_competition_per_demand_missing_too(self):
        # Competitors per unit demand has no denominator without demand:
        # it must go missing, not fall back to the raw count.
        crits = {c["key"]: c for c in scoring._criterion_results(
            self._metrics_with({("catchment_population_estimate", "202412"): None}))}
        for key in ("weekday_competition", "weekend_competition"):
            self.assertIsNone(crits[key]["score"], key)
            self.assertIn("competitors per unit demand undefined", crits[key]["missing_reason"])
        self.assertIsNone(scoring.business_fit(scoring.pillar_scores(list(crits.values())), scoring.DEFAULT_WEIGHTS))

    def test_competition_is_per_10k_people(self):
        crits = {c["key"]: c for c in scoring._criterion_results(self._metrics_with())}
        weekday = crits["weekday_competition"]
        self.assertAlmostEqual(weekday["raw_value"], 30.0 / 1.5)
        # Same competitors, more visitors: fewer competitors per person.
        busier = {c["key"]: c for c in scoring._criterion_results(
            self._metrics_with({("visitor_weekday_estimate", PV): 24_000.0}))}
        self.assertAlmostEqual(busier["weekday_competition"]["raw_value"], 30.0 / 3.5)
        self.assertGreaterEqual(busier["weekday_competition"]["score"], weekday["score"])

    def test_visitor_factor_rescales_only_visitors(self):
        crits = {c["key"]: c for c in scoring._criterion_results(self._metrics_with(), visitor_factor=2.0)}
        self.assertAlmostEqual(crits["daily_people"]["raw_value"], 6000 + 5000 + 8000)
        self.assertAlmostEqual(crits["daily_people_weekend"]["raw_value"], 6000 + 500 + 18000)

    def test_no_poi_snapshot_never_scores_as_an_empty_field(self):
        node = self.nodes[0]
        ihk = self.add_snapshot("ihk_gewerbedaten")
        self.add_business(ihk, node.area_id, "b/1", "IT", "50 - 99 Beschäftigte")
        metrics_pipeline.compute_area(self.conn, node)
        r = scoring.score_all(self.conn, [node.area_id])[node.area_id]
        self.assertIsNone(r["pillars"]["competition"])
        self.assertEqual(r["evidence_quality"]["components"]["competitor_corroboration"]["value"], 0.0)

    def test_missing_input_lowers_evidence_quality(self):
        node = self.nodes[0]
        full = self._metrics_with()
        partial = self._metrics_with({("weekday_all_day_competitor_count", "weekday_We"): None})
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        eq_full = scoring.evidence_quality(self.conn, node.area_id, full, scoring._criterion_results(full), now)
        eq_part = scoring.evidence_quality(self.conn, node.area_id, partial, scoring._criterion_results(partial), now)
        self.assertLess(eq_part["score"], eq_full["score"])
        n = len(scoring.BUSINESS_FIT_CRITERIA)
        self.assertEqual(eq_part["components"]["input_completeness"]["value"], (n - 1) / n)


class DemandSegmentsMissingData(PipelineTestCase):
    def test_unknown_employee_size_is_counted_not_imputed(self):
        node = self.nodes[0]
        snap = self.add_snapshot("ihk_gewerbedaten")
        self.add_business(snap, node.area_id, "b/1", "IT", "unbekannt")
        self.add_business(snap, node.area_id, "b/2", "IT", "10 - 19 Beschäftigte")
        self.add_business(snap, node.area_id, "b/3", "IT", "0 Beschäftigte")
        metrics_pipeline.compute_area(self.conn, node)
        self.assertAlmostEqual(self.metric(node.area_id, "worker_headcount_estimate")["value"], (10 * 19) ** 0.5)
        self.assertEqual(self.metric(node.area_id, "worker_size_unknown_count")["value"], 1.0)

    def test_no_attraction_snapshot_makes_visitors_missing(self):
        node = self.nodes[0]
        metrics_pipeline.compute_area(self.conn, node)
        m = self.metric(node.area_id, "visitor_weekend_estimate")
        self.assertTrue(m["is_missing"])
        self.assertIn("attraction", m["reason"])


if __name__ == "__main__":
    unittest.main()
