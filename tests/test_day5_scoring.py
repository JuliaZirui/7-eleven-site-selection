"""Supplementary tests for the Day 5 layer: catchment-population
apportionment, scoring math, sensitivity analysis, and the report's
last-line output guards. Not one of the plan's four fixed categories,
but the same rules (never guess, never fill 0, every figure traces).
"""

from __future__ import annotations

import json
import math
import unittest
from datetime import date, datetime, timezone
from string import Template
from unittest import mock

from helpers import PipelineTestCase, make_node

import day_types
import demand_model
import metrics_pipeline
from opening_hours import parse_opening_hours
import report
import scoring
from models import TimeWindowLabel
from poi_taxonomy import EVENING_PEAK_CATEGORIES, MORNING_PEAK_CATEGORIES


def _rect(min_lon, min_lat, max_lon, max_lat):
    return {"type": "Polygon", "coordinates": [[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
                                                 [min_lon, max_lat], [min_lon, min_lat]]]}


class CatchmentApportionment(PipelineTestCase):
    nodes = (make_node("split_node", lat=52.52, lon=13.40, radius_m=400),)

    def _write_lor(self, populations: dict[str, int | None], anchor_plr: str = "W"):
        """Two big rectangles split exactly at the anchor's longitude:
        W (west half) and E (east half), each much larger than the circle."""
        lat, lon = 52.52, 13.40
        boundaries = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"plr_id": "W", "plr_name": "West"},
             "geometry": _rect(lon - 0.05, lat - 0.05, lon, lat + 0.05)},
            {"type": "Feature", "properties": {"plr_id": "E", "plr_name": "East"},
             "geometry": _rect(lon, lat - 0.05, lon + 0.05, lat + 0.05)},
        ]}
        mss = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"plr_id": pid, "plr_name": pid, "ew": pop, "zeit": 202412}, "geometry": None}
            for pid, pop in populations.items()
        ]}
        lor_dir = self.root / "db" / "raw" / "lor"
        lor_dir.mkdir(parents=True, exist_ok=True)
        (lor_dir / "b.geojson").write_text(json.dumps(boundaries))
        (lor_dir / "p.geojson").write_text(json.dumps(mss))
        self.add_snapshot("berlin_lor_boundaries", raw_file_ref="db/raw/lor/b.geojson")
        self.add_snapshot("berlin_mss_population", raw_file_ref="db/raw/lor/p.geojson")
        self.conn.execute("UPDATE areas SET lor_code = ? WHERE area_id = 'split_node'", (anchor_plr,))
        self.conn.commit()

    def _plr_area_m2(self):
        # Each rectangle: 0.05 deg lon x 0.10 deg lat, projected with the same local scale factors.
        kx = 111_320.0 * math.cos(math.radians(52.52))
        return (0.05 * kx) * (0.10 * 110_540.0)

    def test_population_apportioned_by_intersected_area(self):
        self._write_lor({"W": 100_000, "E": 50_000})
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        half_circle = math.pi * 400 ** 2 / 2
        expected = (100_000 + 50_000) * half_circle / self._plr_area_m2()
        est = self.metric("split_node", "catchment_population_estimate")
        self.assertAlmostEqual(est["value"], expected, delta=expected * 0.01)
        self.assertAlmostEqual(self.metric("split_node", "anchor_plr_catchment_share")["value"], 0.5, places=2)

    def test_unpopulated_planungsraum_makes_estimate_missing_not_lower(self):
        self._write_lor({"W": 100_000})  # E has no MSS figure -> half the circle unknown
        metrics_pipeline.compute_area(self.conn, self.nodes[0])
        est = self.metric("split_node", "catchment_population_estimate")
        self.assertTrue(est["is_missing"])
        self.assertIn("no MSS population figure", est["reason"])


class ScoringMath(unittest.TestCase):
    def test_normalize_clamps_and_respects_direction(self):
        higher_better = scoring.Criterion("x", "demand", "x", "count", lambda m: None, worst=0, best=100, rationale="")
        lower_better = scoring.Criterion("y", "competition", "y", "count", lambda m: None, worst=5, best=0, rationale="")
        self.assertEqual(higher_better.normalize(50), 50)
        self.assertEqual(higher_better.normalize(500), 100)
        self.assertEqual(higher_better.normalize(-5), 0)
        self.assertEqual(lower_better.normalize(0), 100)
        self.assertEqual(lower_better.normalize(5), 0)
        self.assertEqual(lower_better.normalize(9), 0)

    def test_log_scale_normalizes_on_orders_of_magnitude(self):
        c = scoring.Criterion("z", "demand", "z", "pageviews", lambda m: None, worst=1_000, best=1_000_000,
                              rationale="", log_scale=True)
        self.assertAlmostEqual(c.normalize(1_000), 0, places=6)
        self.assertAlmostEqual(c.normalize(1_000_000), 100, places=6)
        # Geometric midpoint scores ~50, where a linear scale would give ~3.
        self.assertAlmostEqual(c.normalize(31_623), 50, delta=0.1)
        self.assertEqual(c.normalize(0), 0)
        self.assertEqual(c.normalize(5_000_000), 100)

    def test_demand_is_one_criterion_in_people_per_day(self):
        demand = [c for c in scoring.BUSINESS_FIT_CRITERIA if c.pillar == "demand"]
        self.assertEqual([c.key for c in demand], ["daily_people", "daily_people_weekend"])
        self.assertEqual([c.day_type for c in demand], ["weekday", "weekend"])
        # Calendar weights: 5 weekdays : 2 weekend days in both pillars.
        for pillar in scoring.PILLARS:
            by_day = {}
            for c in scoring.BUSINESS_FIT_CRITERIA:
                if c.pillar == pillar:
                    by_day[c.day_type] = by_day.get(c.day_type, 0) + c.weight
            self.assertAlmostEqual(by_day["weekday"] / by_day["weekend"], 5 / 2, msg=pillar)
        self.assertFalse(demand[0].log_scale)

    def test_pillar_score_is_weighted_mean(self):
        crits = [{"pillar": "demand", "score": 90.0, "weight": 1.0},
                 {"pillar": "demand", "score": 30.0, "weight": 0.5}]
        self.assertAlmostEqual(scoring.pillar_scores(crits)["demand"], 70.0)

    def test_every_criterion_anchor_pair_is_non_degenerate(self):
        for c in scoring.BUSINESS_FIT_CRITERIA:
            self.assertNotEqual(c.worst, c.best, c.key)
            self.assertIn(c.pillar, scoring.PILLARS, c.key)

    def test_weights_sum_to_one(self):
        for name, w in scoring.SCENARIO_WEIGHTS.items():
            self.assertAlmostEqual(sum(w.values()), 1.0, msg=name)
            self.assertEqual(set(w), set(scoring.PILLARS), name)

    def test_rank_puts_unscorable_last_and_is_deterministic(self):
        self.assertEqual(scoring.rank({"b": 50.0, "a": 50.0, "c": None, "d": 70.0}), ["d", "a", "b", "c"])


class Sensitivity(unittest.TestCase):
    def _results(self, pillars_by_area):
        crits = {c.key: c for c in scoring.BUSINESS_FIT_CRITERIA}
        out = {}
        for a, pillars in pillars_by_area.items():
            # One criterion per pillar carrying the pillar score, enough for the min-max check.
            criteria = []
            for key in ("daily_people", "weekday_competition"):
                c = crits[key]
                score = pillars[c.pillar]
                raw = c.worst + (c.best - c.worst) * score / 100.0
                criteria.append({"key": key, "pillar": c.pillar, "raw_value": raw, "score": score})
            out[a] = {"pillars": pillars, "criteria": criteria}
        return out

    def test_dominant_area_is_robust_and_dominated_area_never_tops(self):
        res = self._results({
            "strong": {"demand": 80, "competition": 80},
            "weak": {"demand": 20, "competition": 20},
        })
        s = scoring.sensitivity_analysis(res)
        self.assertTrue(s["top_rank_robust"])
        self.assertEqual(s["monte_carlo"]["top_share"]["strong"], 1.0)
        self.assertEqual(s["monte_carlo"]["top_share"]["weak"], 0.0)

    def test_tradeoff_is_reported_as_not_robust(self):
        res = self._results({
            "demand_node": {"demand": 90, "competition": 20},
            "competition_node": {"demand": 20, "competition": 90},
        })
        s = scoring.sensitivity_analysis(res)
        self.assertFalse(s["top_rank_robust"])
        shares = s["monte_carlo"]["top_share"]
        self.assertGreater(shares["demand_node"], 0.2)
        self.assertGreater(shares["competition_node"], 0.2)

    def test_monte_carlo_is_reproducible(self):
        res = self._results({
            "a": {"demand": 60, "competition": 40},
            "b": {"demand": 40, "competition": 60},
        })
        self.assertEqual(scoring.sensitivity_analysis(res)["monte_carlo"], scoring.sensitivity_analysis(res)["monte_carlo"])


    @staticmethod
    def _structured(demand_score, comp_score, count):
        crits = [{"key": c.key, "pillar": c.pillar, "day_type": c.day_type, "weight": c.weight,
                  "score": demand_score if c.pillar == "demand" else comp_score,
                  "count": None if c.pillar == "demand" else count}
                 for c in scoring.BUSINESS_FIT_CRITERIA]
        return {"criteria": crits}

    def test_competition_structure_check_drops_the_demand_denominator(self):
        # "busy" looks uncontested per head only because its demand is large;
        # in absolute counts it is the more contested node.
        res = {
            "busy": self._structured(demand_score=90, comp_score=80, count=200),
            "quiet": self._structured(demand_score=40, comp_score=30, count=0),
        }
        out = scoring.competition_structure_check(res, scoring.DEFAULT_WEIGHTS)
        self.assertEqual(out["pillars"]["busy"]["competition"], 0.0)  # above every P90 anchor
        self.assertEqual(out["pillars"]["quiet"]["competition"], 100.0)  # below every P10 anchor
        self.assertEqual(out["pillars"]["busy"]["demand"], 90.0)  # demand untouched
        self.assertEqual(out["rank"], ["quiet", "busy"])

    def test_competition_structure_check_keeps_missing_counts_missing(self):
        res = {"a": self._structured(demand_score=50, comp_score=50, count=None)}
        out = scoring.competition_structure_check(res, scoring.DEFAULT_WEIGHTS)
        self.assertIsNone(out["pillars"]["a"]["competition"])
        self.assertEqual(out["fits"]["a"], 50.0)  # demand only, renormalized -- never scored as 0

    @staticmethod
    def _mapped(demand_score, count, people, relative):
        """A node whose competition scores follow from count / people, with
        a mapping-consistency entry (relative = its ratio / the best node's)."""
        crits = []
        for c in scoring.BUSINESS_FIT_CRITERIA:
            if c.per_demand:
                ratio = count / (people / scoring.PEOPLE_UNIT)
                crits.append({"key": c.key, "pillar": c.pillar, "day_type": c.day_type, "weight": c.weight,
                              "raw_value": ratio, "score": c.normalize(ratio), "count": count, "people": people})
            else:
                crits.append({"key": c.key, "pillar": c.pillar, "day_type": c.day_type, "weight": c.weight,
                              "raw_value": people, "score": demand_score})
        pillars = scoring.pillar_scores(crits)
        return {"criteria": crits, "pillars": pillars,
                "business_fit": scoring.business_fit(pillars, scoring.DEFAULT_WEIGHTS),
                "mapping_consistency": {"relative": relative}}

    def test_mapping_stress_scales_counts_by_the_mapping_gap(self):
        res = {
            "thin": self._mapped(demand_score=80, count=40, people=20_000, relative=0.5),
            "full": self._mapped(demand_score=60, count=120, people=20_000, relative=1.0),
        }
        out = scoring.competitor_mapping_stress(res, scoring.DEFAULT_WEIGHTS)
        self.assertEqual(out["factors"], {"thin": 2.0, "full": 1.0})
        # The best-mapped node is untouched; the thin one is scored on 80 competitors, not 40.
        self.assertAlmostEqual(out["fits"]["full"], res["full"]["business_fit"])
        self.assertAlmostEqual(out["fits"]["thin"], self._mapped(80, 80, 20_000, 0.5)["business_fit"])
        self.assertLess(out["fits"]["thin"], res["thin"]["business_fit"])
        self.assertEqual(out["pillars"]["thin"]["demand"], 80.0)  # demand untouched
        self.assertEqual(out["leader"], "thin")
        self.assertGreater(out["leader_break_even_factor"], 1.0)

    def test_mapping_stress_break_even_flips_the_leader(self):
        res = {
            "thin": self._mapped(demand_score=60, count=40, people=20_000, relative=0.5),
            "full": self._mapped(demand_score=60, count=80, people=20_000, relative=1.0),
        }
        out = scoring.competitor_mapping_stress(res, scoring.DEFAULT_WEIGHTS)
        # Equal demand: "thin" leads only while it reports fewer competitors than "full".
        self.assertEqual(out["leader"], "thin")
        self.assertAlmostEqual(out["leader_break_even_factor"], 2.0, places=3)
        self.assertEqual(out["rank"][0], "full")  # at the implied factor of 2 it ties, broken by area_id

    def test_mapping_stress_needs_mapping_consistency(self):
        res = {"a": self._structured(demand_score=50, comp_score=50, count=10)}
        self.assertIsNone(scoring.competitor_mapping_stress(res, scoring.DEFAULT_WEIGHTS))

class DemandModel(unittest.TestCase):
    def test_size_class_to_headcount(self):
        self.assertEqual(demand_model.employees_estimate("0 Beschäftigte"), 0.0)
        self.assertAlmostEqual(demand_model.employees_estimate("1 - 3 Beschäftigte"), 3 ** 0.5)
        self.assertAlmostEqual(demand_model.employees_estimate("5000 - 7499 Beschäftigte"), (5000 * 7499) ** 0.5)
        self.assertIsNone(demand_model.employees_estimate("unbekannt"))
        self.assertEqual(demand_model.workers_estimate(["unbekannt", "0 Beschäftigte", None]), (0.0, 2))

    def test_verified_large_registration_replaces_its_class_midpoint(self):
        regs = [
            {"opendata_id": "bank", "employees_range": "5000 - 7499 Beschäftigte"},
            {"opendata_id": "cafe", "employees_range": "10 - 19 Beschäftigte"},
        ]
        verified = {"bank": {"onsite_low": 300.0, "onsite_central": 450.0, "onsite_high": 600.0}}
        w = demand_model.workers_onsite(regs, verified)
        cafe = (10 * 19) ** 0.5
        self.assertAlmostEqual(w["central"], 450 + cafe)
        self.assertAlmostEqual(w["low"], 300 + cafe)
        self.assertAlmostEqual(w["high"], 600 + cafe)
        self.assertAlmostEqual(w["as_registered"], (5000 * 7499) ** 0.5 + cafe)
        self.assertEqual(w["large_unverified"], [])

    def test_workers_nearer_another_station_are_assigned_away(self):
        regs = [{"opendata_id": "a", "employees_range": "10 - 19 Beschäftigte", "lat": 1.0, "lon": 1.0},
                {"opendata_id": "b", "employees_range": "10 - 19 Beschäftigte", "lat": 2.0, "lon": 2.0},
                {"opendata_id": "bank", "employees_range": "5000 - 7499 Beschäftigte", "lat": 2.0, "lon": 2.0}]
        verified = {"bank": {"onsite_low": 300.0, "onsite_central": 450.0, "onsite_high": 600.0}}
        w = demand_model.workers_onsite(regs, verified, uses_this_station=lambda r: r["lat"] == 1.0)
        mid = (10 * 19) ** 0.5
        self.assertAlmostEqual(w["central"], mid)
        self.assertAlmostEqual(w["in_circle"], 2 * mid + 450)
        self.assertAlmostEqual(w["other_station"], mid + 450)
        self.assertAlmostEqual(w["low"], mid)  # an assigned-away employer leaves the ranges too

    def test_unregistered_employer_is_added_under_the_station_rule(self):
        amazon = {"lat": 1.0, "lon": 1.0, "onsite_low": 2500.0, "onsite_central": 3400.0, "onsite_high": 3400.0}
        elsewhere = {"lat": 2.0, "lon": 2.0, "onsite_low": 10.0, "onsite_central": 20.0, "onsite_high": 30.0}
        w = demand_model.workers_onsite([], {}, uses_this_station=lambda r: r["lat"] == 1.0,
                                        unregistered=[amazon, elsewhere])
        self.assertEqual((w["central"], w["low"], w["high"]), (3400.0, 2500.0, 3400.0))
        self.assertEqual(w["unregistered_added"], 3400.0)
        self.assertEqual(w["other_station"], 20.0)

    def test_unverified_large_registration_is_flagged_not_silently_trusted(self):
        regs = [{"opendata_id": "x", "employees_range": "500 - 999 Beschäftigte"},
                {"opendata_id": "y", "employees_range": "200 - 499 Beschäftigte"}]
        w = demand_model.workers_onsite(regs, {})
        self.assertEqual(w["large_unverified"], ["x"])  # 200-499 is below the 500 floor
        self.assertAlmostEqual(w["central"], w["as_registered"])

    def test_every_large_candidate_registration_is_verified(self):
        # The shipped table must cover each 500+ registration it names, with
        # a source and an ordered range.
        rows = demand_model.load_onsite_verifications()
        self.assertGreaterEqual(len(rows), 5)
        for oid, r in rows.items():
            self.assertLessEqual(r["onsite_low"], r["onsite_central"], oid)
            self.assertLessEqual(r["onsite_central"], r["onsite_high"], oid)
            self.assertTrue(r["sources"].startswith("http"), oid)

    def test_visitors_are_proportional_to_pageviews(self):
        fit = {"intercept": 1.0, "slope": 1.0}  # 10 visitors per pageview
        self.assertAlmostEqual(demand_model.annual_visitors(36_500, fit), 365_000)
        self.assertEqual(demand_model.annual_visitors(0, fit), 0.0)

    def test_known_attendance_beats_the_fame_conversion(self):
        fit = {"intercept": 1.0, "slope": 1.0}  # 10 visitors per pageview
        club = {"pageviews": 577_600, "share": 1 / 3, "kind": "nightclub", "wikipedia_titles": {"dewiki": "Berghain"}}
        museum = {"pageviews": 36_500, "share": 1.0, "kind": "museum", "wikipedia_titles": {"dewiki": "Anything"}}
        known, converted, skipped = demand_model.visitors_split([club, museum], fit, "weekend", {"Berghain": 115_000})
        self.assertAlmostEqual(known, 115_000 / 3 / 104)  # capacity-based, weekend nights only
        self.assertAlmostEqual(converted, 365_000 / 364)
        self.assertEqual(skipped, 0)
        self.assertEqual(demand_model.visitors_split([club], fit, "weekday", {"Berghain": 115_000})[0], 0.0)

    def test_shipped_attendance_table_caps_berghain_at_capacity(self):
        known = demand_model.load_known_attendance()
        self.assertLessEqual(known["Berghain"], 1500 * (34 / 11) * 52)  # capacity x turnover x weekends

    def test_visitors_per_day_applies_gateway_share_and_skips_unknown(self):
        fit = {"intercept": 1.0, "slope": 1.0}
        records = [{"pageviews": 36_500, "share": 0.5}, {"pageviews": None, "share": 1.0}]
        per_day, skipped = demand_model.visitors_per_day(records, fit)
        self.assertAlmostEqual(per_day, 500.0)
        self.assertEqual(skipped, 1)

    def test_segment_mix_follows_the_data(self):
        # The point of one unit: a visitor-heavy node is visitor-led without
        # any segment weight saying so.
        def crit(res, wrk, vis):
            m = {(n, "x"): {"metric_id": i, "value": v, "is_missing": False, "missing_reason": None}
                 for i, (n, v) in enumerate((("catchment_population_estimate", res), ("worker_headcount_estimate", wrk),
                                             ("visitor_weekday_estimate", vis)))}
            return next(c for c in scoring._criterion_results(m) if c["key"] == "daily_people")
        tourist, residential = crit(1_000, 5_000, 20_000), crit(10_000, 2_000, 500)
        self.assertGreater(tourist["breakdown"]["visitors"] / tourist["raw_value"], 0.7)
        self.assertLess(residential["breakdown"]["visitors"] / residential["raw_value"], 0.05)


class DayTypes(unittest.TestCase):
    @staticmethod
    def _poi(category, hours=None, in_station=False):
        return {"category": category, "opening_hours_raw": hours, "payload": {"in_station": in_station}}

    def _window(self, key):
        return next(w for w in day_types.COMPETITION_WINDOWS if w.key == key)

    def test_sunday_law_closes_unknown_hours_retail_outside_stations(self):
        w = self._window("weekend_day")
        self.assertFalse(day_types.poi_open(self._poi("supermarket"), day_types.SU, w))
        self.assertFalse(day_types.poi_open(self._poi("spati_kiosk"), day_types.SU, w))
        self.assertTrue(day_types.poi_open(self._poi("supermarket", in_station=True), day_types.SU, w))
        self.assertTrue(day_types.poi_open(self._poi("supermarket"), day_types.SA, w))
        self.assertTrue(day_types.poi_open(self._poi("cafe"), day_types.SU, w))
        # Recorded Sunday hours beat the legal default.
        self.assertTrue(day_types.poi_open(self._poi("spati_kiosk", "Mo-Su 08:00-23:00"), day_types.SU, w))

    def test_bakeries_trade_7_to_16_on_sunday(self):
        self.assertTrue(day_types.poi_open(self._poi("bakery"), day_types.SU, self._window("weekend_day")))
        night = day_types.CompetitionWindow("x", "weekend", 22, 2, ("bakery",), "x")
        self.assertFalse(day_types.poi_open(self._poi("bakery"), day_types.SU, night))

    def test_night_window_runs_past_midnight(self):
        night = self._window("weekend_night")
        late = parse_opening_hours("Mo-Su 23:00-03:00")
        early_tail_only = parse_opening_hours("Su 00:00-01:00")  # open 0-1 on Sunday only
        self.assertTrue(day_types.open_in_window(late, day_types.SA, 22, 2))
        self.assertTrue(day_types.open_in_window(early_tail_only, day_types.SA, 22, 2))  # Sa night runs into Su
        self.assertFalse(day_types.open_in_window(early_tail_only, day_types.SU, 22, 2))
        self.assertEqual(night.end_hour, 2)

    def test_weekend_counts_average_saturday_and_sunday(self):
        pois = [self._poi("supermarket"), self._poi("cafe")]
        count, closed = day_types.count_window_competitors(pois, self._window("weekend_day"))
        self.assertEqual((count, closed), (1.5, 0.0))  # supermarket open Sa only, cafe both

    def test_all_day_basket_merges_both_peaks_and_drops_only_shops_closed_all_day(self):
        w = self._window("weekday_all_day")
        self.assertEqual(set(w.categories),
                         set(day_types.MORNING_PEAK_CATEGORIES) | set(day_types.EVENING_PEAK_CATEGORIES))
        pois = [self._poi("bakery", "Mo-Fr 06:00-10:00"),  # open in the morning only: still competes that day
                self._poi("spati_kiosk", "Mo-Su 18:00-02:00"),
                self._poi("cafe", "Sa-Su 09:00-17:00"),  # closed all Wednesday: excluded
                self._poi("fast_food"),  # unknown hours: open
                self._poi("chemist")]  # not in the basket
        self.assertEqual(day_types.count_window_competitors(pois, w), (3.0, 1.0))

    def test_only_all_day_windows_are_scored(self):
        scored = {c.key for c in scoring.BUSINESS_FIT_CRITERIA if c.pillar == "competition"}
        self.assertEqual(scored, {"weekday_competition", "weekend_competition"})
        m = {(f"{w.key}_competitor_count", day_types.PERIOD[w.day_type]): {
                "metric_id": i, "value": 1.0, "is_missing": False, "missing_reason": None}
             for i, w in enumerate(day_types.COMPETITION_WINDOWS)}
        read = {c.derive(m).metric_ids for c in scoring.BUSINESS_FIT_CRITERIA if c.pillar == "competition"}
        keys = [w.key for w in day_types.COMPETITION_WINDOWS]
        self.assertEqual({keys[i] for (i,) in read}, {"weekday_all_day", "weekend_all_day"})
        self.assertEqual(set(scoring.DIAGNOSTIC_WINDOW_QUARTILES),
                         {w.key for w in day_types.DIAGNOSTIC_COMPETITION_WINDOWS})

    def test_club_without_hours_counts_only_at_the_weekend(self):
        days = day_types.venue_open_days("nightclub", None)
        self.assertEqual(days, (day_types.SA, day_types.SU))
        self.assertEqual(day_types.visitors_on_day_type(104_000, days, "weekday"), 0.0)
        self.assertAlmostEqual(day_types.visitors_on_day_type(104_000, days, "weekend"), 1_000.0)
        museum = day_types.venue_open_days("museum", None)
        self.assertEqual(len(museum), 7)
        self.assertAlmostEqual(day_types.visitors_on_day_type(36_400, museum, "weekday"), 100.0)
        # Recorded hours win: a club tagged open every night counts midweek too.
        self.assertEqual(len(day_types.venue_open_days("nightclub", "Mo-Su 22:00-07:00")), 7)

    def test_weekend_worker_factor_by_sector(self):
        self.assertAlmostEqual(day_types.weekend_worker_factor("56"), 0.70 * 0.5)  # gastronomy
        self.assertAlmostEqual(day_types.weekend_worker_factor("62"), 0.10 * 0.5)  # IT office
        self.assertAlmostEqual(day_types.weekend_worker_factor("41"), 0.27 * 0.5)  # all-sector default
        self.assertAlmostEqual(day_types.weekend_worker_factor("47", override=0.10), 0.05)  # Zalando's offices

    def test_day_type_check_isolates_each_day_type(self):
        crit = [
            {"pillar": "demand", "day_type": "weekday", "weight": 5.0, "score": 100.0},
            {"pillar": "demand", "day_type": "weekend", "weight": 2.0, "score": 0.0},
        ]
        out = scoring.day_type_check({"a": {"criteria": crit}}, {"demand": 1.0, "competition": 0.0})
        self.assertAlmostEqual(out["weekday_only"]["fits"]["a"], 100.0)
        self.assertAlmostEqual(out["weekend_only"]["fits"]["a"], 0.0)
        self.assertAlmostEqual(out["equal"]["fits"]["a"], 50.0)


class PeakCompetitorCounts(unittest.TestCase):
    @staticmethod
    def _poi(category, hours=None):
        return {"category": category, "opening_hours_raw": hours}

    def test_each_peak_counts_only_its_own_formats(self):
        pois = [self._poi(c) for c in ("bakery", "cafe", "supermarket", "gas_station_convenience",
                                       "spati_kiosk", "fast_food", "chemist", "beverages", "other_fresh_food")]
        morning = metrics_pipeline.count_peak_competitors(pois, MORNING_PEAK_CATEGORIES, TimeWindowLabel.MORNING_PEAK)
        evening = metrics_pipeline.count_peak_competitors(pois, EVENING_PEAK_CATEGORIES, TimeWindowLabel.EVENING_PEAK)
        self.assertEqual(morning, (4, 0))  # bakery, cafe, supermarket, fuel shop
        self.assertEqual(evening, (4, 0))  # späti, fast food, supermarket, fuel shop

    def test_known_closed_is_excluded_and_unknown_hours_count(self):
        pois = [
            self._poi("bakery", "Mo-Fr 06:00-14:00"),  # open 7-9
            self._poi("cafe", "Mo-Su 10:00-18:00"),  # known closed 7-9
            self._poi("cafe"),  # unknown hours -> counted
            self._poi("bakery", "Mo-Fr 08:30-12:00"),  # opens inside the window -> counted
        ]
        self.assertEqual(
            metrics_pipeline.count_peak_competitors(pois, MORNING_PEAK_CATEGORIES, TimeWindowLabel.MORNING_PEAK), (3, 1))

    def test_overnight_spati_counts_in_the_evening(self):
        pois = [self._poi("spati_kiosk", "Mo-Su 15:00-03:00"), self._poi("fast_food", "Mo-Su 20:00-04:00")]
        self.assertEqual(
            metrics_pipeline.count_peak_competitors(pois, EVENING_PEAK_CATEGORIES, TimeWindowLabel.EVENING_PEAK), (1, 1))


class DataAgeFreshness(unittest.TestCase):
    TODAY = date(2026, 10, 1)

    def test_old_population_vintage_is_within_its_cadence(self):
        ok, _ = scoring.snapshot_freshness({"source_name": "berlin_mss_population", "data_as_of_date": "2024-12-31"}, self.TODAY)
        self.assertTrue(ok)

    def test_live_source_ages_on_data_date_not_fetch_date(self):
        ok, detail = scoring.snapshot_freshness(
            {"source_name": "osm_overpass", "data_as_of_date": "2026-06-01", "fetched_at": "2026-09-30T00:00:00+00:00"},
            self.TODAY)
        self.assertFalse(ok)
        self.assertIn("limit 90d", detail)
        ok, _ = scoring.snapshot_freshness({"source_name": "osm_overpass", "data_as_of_date": "2026-09-01"}, self.TODAY)
        self.assertTrue(ok)

    def test_versioned_boundaries_are_not_aged(self):
        ok, _ = scoring.snapshot_freshness({"source_name": "berlin_lor_boundaries", "data_as_of_date": "2021-01-01"}, self.TODAY)
        self.assertIsNone(ok)

    def test_unparseable_date_is_stale_not_fresh(self):
        ok, _ = scoring.snapshot_freshness({"source_name": "ihk_gewerbedaten", "data_as_of_date": "n/a"}, self.TODAY)
        self.assertFalse(ok)


class FreshnessIsStableOverShortSpans(PipelineTestCase):
    def test_evidence_quality_does_not_decay_two_weeks_after_a_fetch(self):
        # scoring_v3 judged fetch age against 14 days, so the same data
        # dropped a tier on day 15 with nothing changed.
        node = self.nodes[0]
        snap = self.add_snapshot("osm_overpass", fetched_at="2026-09-28T12:00:00+00:00", data_as_of="2026-09-28")
        self.add_poi(snap, node.area_id, "node/1", "Cafe", "cafe", 52.5201, 13.4001, None)
        metrics_pipeline.compute_area(self.conn, node)
        scores = [
            scoring.score_all(self.conn, [node.area_id], now=datetime(2026, m, d, tzinfo=timezone.utc))[node.area_id]
            ["evidence_quality"]["components"]["freshness"]["value"]
            for m, d in ((9, 29), (10, 14), (12, 1))
        ]
        self.assertEqual(scores, [1.0, 1.0, 1.0])
        late = scoring.score_all(self.conn, [node.area_id], now=datetime(2027, 1, 15, tzinfo=timezone.utc))
        self.assertEqual(late[node.area_id]["evidence_quality"]["components"]["freshness"]["value"], 0.0)


class DemandWeightCrossover(unittest.TestCase):
    def test_leader_of_both_pillars_has_no_crossover(self):
        self.assertIsNone(scoring.demand_weight_crossover({"demand": 80, "competition": 90},
                                                          {"demand": 60, "competition": 30}))

    def test_trade_off_crosses_once(self):
        # A: 54.8 / 63.3, B: 60.8 / 25.7 -> equal at w = 37.6 / 43.6.
        w = scoring.demand_weight_crossover({"demand": 54.8, "competition": 63.3},
                                            {"demand": 60.8, "competition": 25.7})
        self.assertAlmostEqual(w, 37.6 / 43.6)


class ReportGuards(unittest.TestCase):
    def test_template_placeholders_all_supplied(self):
        # substitute() raises KeyError on any placeholder the renderer forgot.
        placeholders = set(Template.pattern.findall(report.TEMPLATE_PATH.read_text()))
        names = {g[1] or g[2] for g in placeholders if g[1] or g[2]}
        source = report.Path(report.__file__).read_text()
        for name in names:
            self.assertIn(f"{name}=", source, f"template placeholder ${name} is never passed by render()")

    def test_store_hypothesis_is_parsed_from_the_document(self):
        h = report._store_hypothesis()
        self.assertIn("Products & services", h["assumptions"])
        self.assertIn("Store format", h["assumptions"])
        self.assertEqual([w["label"] for w in h["time_windows"]], ["morning_peak", "evening_peak", "midday_offpeak"])


if __name__ == "__main__":
    unittest.main()


class DifferentiationRules(unittest.TestCase):
    """§5 rules on a synthetic node: report._pois is patched, so no database."""

    NODE = make_node("n", 52.52, 13.40)

    @staticmethod
    def _m(value):
        return {"metric_id": 1, "value": value, "is_missing": False, "missing_reason": None}

    def _r(self, weekday, weekend):
        """weekday / weekend: (residents, workers, visitors) people per day."""
        metrics = {(name, "current"): self._m(v) for name, v in (
            ("competitor_count_total", 20.0), ("competitor_count_fast_food", 2.0),
            ("competitor_count_bakery", 2.0), ("competitor_count_cafe", 2.0))}
        crits = []
        for key, parts in (("daily_people", weekday), ("daily_people_weekend", weekend)):
            crits.append({"key": key, "raw_value": float(sum(parts)),
                          "breakdown": dict(zip(("residents", "workers", "visitors"), map(float, parts)))})
        return {"metrics": metrics, "criteria": crits}

    def _rules(self, r, pois=()):
        with mock.patch.object(report, "_pois", return_value=list(pois)):
            return report._differentiation(None, self.NODE, r)

    def test_weekday_heavy_is_part_of_the_office_rule(self):
        # Workers >= 40% and weekday >= 1.2x weekend: one office entry with the weekday-heavy sentence.
        office = self._rules(self._r(weekday=(3_000, 14_000, 6_000), weekend=(3_000, 1_000, 6_000)))
        hits = [h for h in office if "Office-worker base" in h]
        self.assertEqual(len(hits), 1)
        self.assertIn("Weekday-heavy", hits[0])
        self.assertFalse(any("Weekday-led" in h for h in office))
        # Office-led but flat across the week: no weekday-heavy sentence.
        flat = self._rules(self._r(weekday=(3_000, 6_000, 1_000), weekend=(3_000, 5_000, 1_000)))
        self.assertNotIn("Weekday-heavy", next(h for h in flat if "Office-worker base" in h))
        # Weekday-heavy but visitor- or resident-led: never called weekday-heavy.
        for weekday, weekend in (((1_300, 9_400, 14_200), (1_300, 700, 14_200)),
                                 ((10_700, 3_200, 500), (10_700, 500, 500))):
            rules = self._rules(self._r(weekday, weekend))
            self.assertFalse(any("weekday-heavy" in h.lower() for h in rules), weekday)

    def test_24_7_competitors_near_the_anchor_are_one_entry(self):
        near = [{"name": n, "category": "spati_kiosk", "lat": 52.5210, "lon": 13.4000 + i * 1e-4, "hours": "24/7"}
                for i, n in enumerate(("25 h shop", "REWE To Go"))]
        far = {"name": "Far", "category": "spati_kiosk", "lat": 52.53, "lon": 13.40, "hours": "24/7"}
        hits = [h for h in self._rules(self._r((5_000, 5_000, 500), (5_000, 1_000, 500)), near + [far]) if "24/7" in h]
        self.assertEqual(len(hits), 1)
        self.assertIn("2 24/7 direct-format competitors", hits[0])
        self.assertIn("25 h shop", hits[0])
        self.assertIn("REWE To Go", hits[0])
        self.assertNotIn("Far", hits[0])
