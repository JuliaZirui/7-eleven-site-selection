"""Fixed test case 4/4: opening hours that span midnight.

Rule under test: a closing time at or before the opening time means
"past midnight", and the tail belongs to the *next* day -- including
across the week boundary (Sunday night -> Monday morning). Anything
outside the supported syntax subset is "unknown", never guessed.
"""

from __future__ import annotations

import unittest

from helpers import PipelineTestCase

import metrics_pipeline
from opening_hours import WEEKDAY_TOKENS, is_open_during, parse_opening_hours

MO, TU, WE, TH, FR, SA, SU = range(7)
MIDNIGHT = 1440


def _hm(h, m=0):
    return h * 60 + m


class OvernightSplitting(unittest.TestCase):
    def test_simple_overnight_span_splits_across_two_days(self):
        p = parse_opening_hours("Mo 09:00-01:00")
        self.assertEqual(p.schedule[MO], ((_hm(9), MIDNIGHT),))
        self.assertEqual(p.schedule[TU], ((0, _hm(1)),))

    def test_sunday_night_wraps_into_monday(self):
        p = parse_opening_hours("Su 13:00-01:00")
        self.assertEqual(p.schedule[SU], ((_hm(13), MIDNIGHT),))
        self.assertEqual(p.schedule[MO], ((0, _hm(1)),))

    def test_real_star_mini_market_string(self):
        # Schönhauser Allee, verified Day 3 case.
        p = parse_opening_hours("Mo-Th 09:00-01:00, Fr,Sa 09:00-04:00, Su 13:00-01:00")
        self.assertTrue(is_open_during(p, SA, 2, 3))   # Friday night tail
        self.assertTrue(is_open_during(p, SU, 3, 4))   # Saturday night tail
        self.assertFalse(is_open_during(p, SU, 5, 12))  # closed Sunday morning until 13:00
        self.assertTrue(is_open_during(p, MO, 0, 1))   # Sunday night tail into Monday
        self.assertFalse(is_open_during(p, TU, 2, 8))  # Monday tail ends at 01:00

    def test_night_shop_counts_in_next_mornings_window(self):
        # Open 22:00-08:00: Wednesday's 7-9 window catches Tuesday night's tail.
        p = parse_opening_hours("Mo-Su 22:00-08:00")
        self.assertTrue(is_open_during(p, WE, 7, 9))
        self.assertFalse(is_open_during(p, WE, 10, 15))
        self.assertTrue(is_open_during(p, WE, 16, 23))

    def test_closing_exactly_at_midnight_does_not_leak_into_next_day(self):
        p = parse_opening_hours("Mo-Su 10:00-00:00")
        self.assertTrue(is_open_during(p, WE, 22, 24))
        self.assertFalse(is_open_during(p, TH, 0, 1))  # zero-length tail must not count as open

    def test_wrapping_day_range(self):
        p = parse_opening_hours("Fr-Mo 18:00-02:00")
        for d in (FR, SA, SU, MO):
            self.assertIn((_hm(18), MIDNIGHT), p.schedule[d], WEEKDAY_TOKENS[d])
        self.assertIn((0, _hm(2)), p.schedule[TU])  # Monday night tail
        self.assertNotIn((_hm(18), MIDNIGHT), p.schedule.get(TU, ()))

    def test_24_7(self):
        p = parse_opening_hours("24/7")
        self.assertTrue(all(is_open_during(p, d, 0, 24) for d in range(7)))


class RuleOverrides(unittest.TestCase):
    """OSM semantics: a later rule for the same day replaces the earlier
    rule for that day -- it does not add to it. The previous day's
    overnight tail is a separate interval and survives."""

    def test_later_narrower_rule_replaces_earlier_hours(self):
        p = parse_opening_hours("Mo-Fr 08:00-18:00; Fr 08:00-12:00")
        self.assertFalse(is_open_during(p, FR, 16, 19))
        self.assertTrue(is_open_during(p, TH, 16, 19))

    def test_off_closes_the_day(self):
        p = parse_opening_hours("Mo-Fr 07:30-18:00; Sa-Su off")
        self.assertFalse(is_open_during(p, SA, 0, 24))
        self.assertTrue(is_open_during(p, WE, 7, 9))

    def test_override_keeps_previous_nights_tail(self):
        # Thursday 09:00-02:00 spills into Friday; Friday's own rule
        # replaces Friday's daytime hours but not Thursday's tail.
        p = parse_opening_hours("Mo-Fr 09:00-02:00; Fr 10:00-16:00")
        self.assertTrue(is_open_during(p, FR, 1, 2))
        self.assertFalse(is_open_during(p, FR, 17, 23))
        self.assertFalse(is_open_during(p, SA, 1, 2))  # Friday's replaced rule no longer spills

    def test_off_day_keeps_previous_nights_tail(self):
        p = parse_opening_hours("Mo-Su 18:00-02:00; Su off")
        self.assertTrue(is_open_during(p, SU, 1, 2))   # Saturday night tail
        self.assertFalse(is_open_during(p, SU, 18, 23))
        self.assertFalse(is_open_during(p, MO, 1, 2))  # nothing spills from a closed Sunday


class NeverGuess(unittest.TestCase):
    def test_unsupported_syntax_is_unknown(self):
        for raw in (
            "sunrise-sunset",
            "Mo-Fr 07:00-12:00,13:00-18:00; Sa 09:00-13:00",  # split shifts: not in the supported subset
            "Jan-Mar Mo-Fr 08:00-16:00",
            "by appointment",
        ):
            self.assertIsNone(parse_opening_hours(raw), raw)

    def test_missing_and_empty_are_unknown(self):
        self.assertIsNone(parse_opening_hours(None))
        self.assertIsNone(parse_opening_hours("   "))
        self.assertFalse(is_open_during(None, WE, 7, 9))

    def test_public_holiday_only_rule_adds_no_weekday_hours(self):
        p = parse_opening_hours("Mo-Fr 08:00-18:00; PH 10:00-14:00")
        self.assertNotIn(SA, p.schedule)
        self.assertNotIn(SU, p.schedule)


class OvernightThroughMetrics(PipelineTestCase):
    def test_overnight_poi_is_counted_in_the_right_windows(self):
        node = self.nodes[0]
        snap = self.add_snapshot("osm_overpass")
        # Night kiosk only: open 20:00-08:00 every day.
        self.add_poi(snap, node.area_id, "node/1", "Night", "spati_kiosk", 52.5201, 13.4001, "Mo-Su 20:00-08:00")
        metrics_pipeline.compute_area(self.conn, node)
        self.assertEqual(self.metric(node.area_id, "competitors_open_morning_peak", "weekday_We")["value"], 1.0)
        self.assertEqual(self.metric(node.area_id, "competitors_open_midday_offpeak", "weekday_We")["value"], 0.0)
        self.assertEqual(self.metric(node.area_id, "competitors_open_evening_peak", "weekday_We")["value"], 0.0)
        self.assertEqual(self.metric(node.area_id, "competitors_open_morning_peak", "weekend_Sa")["value"], 1.0)


if __name__ == "__main__":
    unittest.main()
