"""Fixed test case 3/4: API failure.

Rule under test: a failed fetch leaves no partial or fabricated data --
the area gets no snapshot and no observations, the run continues for
other areas, the Agent falls back to a field-research flag instead of
retrying without limit, and a report is never written on top of a
figure that doesn't verify. All HTTP is mocked; nothing here touches a
live endpoint.
"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

from helpers import PipelineTestCase, make_node

import agent_followup
import fsq_client
import ihk_client
import ihk_pipeline
import lor_client
import osm_client
import overture_client
import poi_pipeline
import report
from models import CANDIDATE_NODES


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload or {}, request=httpx.Request("POST", "https://mock"))


def _overpass_ok(buildings: int = 100) -> dict:
    """A complete-looking Overpass answer: no POIs, but the building canary passes."""
    return {"elements": [{"type": "count", "id": 0, "tags": {"ways": str(buildings)}}]}


class OverpassClientRetries(PipelineTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(osm_client.time, "sleep")  # no real backoff waits
        self.sleep = p.start()
        self._patches.append(p)

    def test_timeouts_on_every_endpoint_raise_after_bounded_retries(self):
        post = self.mock_network("osm_client.httpx.post", side_effect=httpx.ReadTimeout("mock timeout"))
        with self.assertRaises(osm_client.OverpassError) as ctx:
            osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        expected = len(osm_client.OVERPASS_URLS) * osm_client.MAX_ATTEMPTS
        self.assertEqual(post.call_count, expected)
        self.assertEqual(str(ctx.exception).count("-> timeout"), expected)

    def test_http_error_statuses_are_retried_then_raised(self):
        post = self.mock_network("osm_client.httpx.post", return_value=_response(429))
        with self.assertRaises(osm_client.OverpassError):
            osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        self.assertEqual(post.call_count, len(osm_client.OVERPASS_URLS) * osm_client.MAX_ATTEMPTS)

    def test_falls_through_to_the_next_mirror(self):
        responses = iter([_response(503)] * osm_client.MAX_ATTEMPTS + [_response(200, _overpass_ok())])
        self.mock_network("osm_client.httpx.post", side_effect=responses)
        payload, url = osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        self.assertEqual(url, osm_client.OVERPASS_URLS[1])
        self.assertEqual(payload, _overpass_ok())

    def test_incomplete_mirror_is_skipped_without_retrying(self):
        # The overpass.atownsend.org.uk failure mode: HTTP 200, valid JSON,
        # but a partial database (1 building where a full mirror has 2,350).
        responses = iter([_response(200, _overpass_ok(buildings=1)), _response(200, _overpass_ok())])
        post = self.mock_network("osm_client.httpx.post", side_effect=responses)
        _, url = osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        self.assertEqual((url, post.call_count), (osm_client.OVERPASS_URLS[1], 2))

    def test_response_without_canary_counts_as_incomplete(self):
        post = self.mock_network("osm_client.httpx.post", return_value=_response(200, {"elements": []}))
        with self.assertRaises(osm_client.OverpassError) as ctx:
            osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        self.assertEqual(post.call_count, len(osm_client.OVERPASS_URLS))
        self.assertIn("incomplete dataset", str(ctx.exception))

    def test_requests_identify_the_client(self):
        # overpass-api.de answers 406 to httpx's default User-Agent; every
        # request must carry the project's identifying agent.
        post = self.mock_network("osm_client.httpx.post", return_value=_response(200, _overpass_ok()))
        osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        ua = post.call_args.kwargs["headers"]["User-Agent"]
        self.assertEqual(ua, osm_client.HTTP_HEADERS["User-Agent"])
        self.assertNotIn("httpx", ua.lower())

    def test_connection_error_recovers_on_retry(self):
        responses = iter([httpx.ConnectError("mock refused"), _response(200, _overpass_ok())])

        def next_or_raise(*a, **kw):
            r = next(responses)
            if isinstance(r, Exception):
                raise r
            return r

        post = self.mock_network("osm_client.httpx.post", side_effect=next_or_raise)
        _, url = osm_client.fetch_overpass_pois(52.52, 13.40, 400)
        self.assertEqual((url, post.call_count), (osm_client.OVERPASS_URLS[0], 2))


class PipelinesWriteNothingOnFailure(PipelineTestCase):
    nodes = (make_node("area_ok", lat=52.52), make_node("area_fail", lat=52.53))

    def _no_rows(self, area_id):
        self.assertEqual(self.count("SELECT COUNT(*) FROM observations WHERE area_id = ?", area_id), 0)

    def test_osm_failure_writes_no_snapshot_or_observations(self):
        self.mock_network("poi_pipeline.fetch_overpass_pois", side_effect=osm_client.OverpassError("all endpoints down"))
        with self.assertRaises(poi_pipeline.PoiPipelineError):
            poi_pipeline.ingest_area(self.conn, self.nodes[1])
        self.assertEqual(self.count("SELECT COUNT(*) FROM source_snapshots"), 0)
        self._no_rows("area_fail")

    def test_osm_run_continues_past_a_failed_area(self):
        def fetch(lat, lon, radius_m):
            if lat == 52.53:
                raise osm_client.OverpassError("mock outage for one area")
            return {"elements": [{"type": "node", "id": 1, "lat": lat, "lon": lon,
                                  "tags": {"amenity": "cafe", "name": "Ok"}}]}, "https://mock"

        self.mock_network("poi_pipeline.fetch_overpass_pois", side_effect=fetch)
        with mock.patch.object(poi_pipeline, "CANDIDATE_NODES", self.nodes):
            results = poi_pipeline.run(self.db_path, sources=("osm_overpass",))
        self.conn.execute("SELECT 1")  # refresh
        self.assertEqual([("error" in r) for r in results], [False, True])
        # One OSM source record + its conflated competitor record.
        self.assertEqual(self.count("SELECT COUNT(*) FROM observations WHERE area_id = 'area_ok' AND entity_type = 'poi_source'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM observations WHERE area_id = 'area_ok' AND entity_type = 'poi'"), 1)
        self._no_rows("area_fail")

    def test_secondary_source_failure_degrades_instead_of_skipping(self):
        # Overture/FSQ only add coverage: their outage must not cost the
        # area its competitor set, but the gap must be on record.
        self.mock_network("poi_pipeline.fetch_overpass_pois", return_value=(
            {"elements": [{"type": "node", "id": 1, "lat": 52.52, "lon": 13.40, "tags": {"amenity": "cafe", "name": "Ok"}}]},
            "https://mock",
        ))
        self.mock_network("poi_pipeline.fetch_overture_places", side_effect=overture_client.OvertureError("mock S3 down"))
        self.mock_network("poi_pipeline.fetch_fsq_places", side_effect=fsq_client.FsqError("HF_TOKEN is not set"))
        with mock.patch.object(poi_pipeline, "CANDIDATE_NODES", self.nodes[:1]):
            results = poi_pipeline.run(self.db_path)
        self.assertNotIn("error", results[0])
        self.assertIn("error", results[0]["sources"]["overture_places"])
        self.assertEqual(results[0]["conflated"]["missing_sources"], ["overture_places", "fsq_os_places"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM observations WHERE entity_type = 'poi'"), 1)

    def test_ihk_failure_writes_nothing(self):
        self.mock_network("ihk_pipeline.fetch_gewerbedaten_bbox", side_effect=ihk_client.IhkFetchError("mock 500"))
        with self.assertRaises(ihk_pipeline.IhkPipelineError):
            ihk_pipeline.ingest_area(self.conn, self.nodes[0])
        self.assertEqual(self.count("SELECT COUNT(*) FROM source_snapshots"), 0)

    def test_ihk_client_rejects_error_status_and_malformed_payload(self):
        self.mock_network("ihk_client.httpx.get", side_effect=iter([_response(500), _response(200, {"type": "Oops"})]))
        with self.assertRaises(ihk_client.IhkFetchError):
            ihk_client.fetch_gewerbedaten_bbox(52.52, 13.40, 400)
        with self.assertRaises(ihk_client.IhkFetchError):
            ihk_client.fetch_gewerbedaten_bbox(52.52, 13.40, 400)

    def test_lor_client_rejects_error_status_and_empty_collection(self):
        self.mock_network("lor_client.httpx.get", side_effect=iter([
            _response(502), _response(200, {"type": "FeatureCollection", "features": []}),
        ]))
        with self.assertRaises(lor_client.LorFetchError):
            lor_client.fetch_lor_boundaries()
        with self.assertRaises(lor_client.LorFetchError):
            lor_client.fetch_mss_population()


class AgentFallsBackOnFailure(PipelineTestCase):
    def _stale_osm_gap_setup(self):
        area = self.nodes[0].area_id
        snap = self.add_snapshot("osm_overpass", fetched_at="2026-01-01T00:00:00+00:00")
        self.add_poi(snap, area, "node/1", "Cafe", "cafe", 52.5201, 13.4001, "Mo-Su 08:00-18:00")

    def test_failed_live_tool_is_flagged_for_field_research(self):
        self._stale_osm_gap_setup()
        self.mock_network("poi_pipeline.fetch_overpass_pois", side_effect=osm_client.OverpassError("mock outage"))
        budget = agent_followup.CallBudget(max_calls_total=4, max_calls_per_area=2)
        log = agent_followup.run_followups(self.conn, self.nodes[0], budget)
        stale = next(e for e in log if e["gap_type"] == "stale_snapshot")
        self.assertIn("flag_for_field_research", stale["tool"])
        self.assertIn("live tool call failed", stale["stop_reason"])
        self.assertEqual(budget.used_total, 1)  # the failed attempt still costs its call
        self.assertEqual(self.count("SELECT COUNT(*) FROM development_events WHERE event_type = 'evidence_gap'"), 1)

    def test_exhausted_budget_stops_calling_and_flags_instead(self):
        self._stale_osm_gap_setup()
        fetch = self.mock_network("poi_pipeline.fetch_overpass_pois")
        budget = agent_followup.CallBudget(max_calls_total=0, max_calls_per_area=0)
        log = agent_followup.run_followups(self.conn, self.nodes[0], budget)
        self.assertEqual(fetch.call_count, 0)
        self.assertTrue(all("budget exhausted" in (e["stop_reason"] or "") for e in log if e["gap_type"] == "stale_snapshot"))


class ReportRefusesUnverifiedFigures(PipelineTestCase):
    """The report's own "API" is the database: if a cited figure doesn't
    verify, no report file and no analysis_runs row may be written."""

    nodes = CANDIDATE_NODES

    def _seed_minimal(self):
        import metrics_pipeline
        for i, n in enumerate(self.nodes):
            snap = self.add_snapshot("osm_overpass")
            for j in range(5 + i):
                self.add_poi(snap, n.area_id, f"node/{i}{j}", f"S{j}", "cafe", n.lat + j * 1e-4, n.lon,
                             "Mo-Fr 06:00-20:00" if j % 2 else "Mo-Fr 10:00-22:00")
            ihk = self.add_snapshot("ihk_gewerbedaten")
            self.add_business(ihk, n.area_id, f"b/{i}", "Gastronomie", "10 - 19 Beschäftigte", branch_id="56")
            metrics_pipeline.compute_area(self.conn, n)
        self.conn.commit()

    def test_citation_mismatch_aborts_the_run_atomically(self):
        self._seed_minimal()
        with mock.patch.object(report, "verify_citations", side_effect=AssertionError("mock: figure does not trace")):
            with self.assertRaises(AssertionError):
                report.run(self.db_path)
        self.assertEqual(self.count("SELECT COUNT(*) FROM analysis_runs"), 0)
        self.assertFalse((self.root / "reports").exists() and any((self.root / "reports").iterdir()))

    def test_verify_citations_detects_a_changed_value(self):
        self._seed_minimal()
        import scoring
        results = scoring.score_all(self.conn, [n.area_id for n in self.nodes])
        crits = {c["key"]: c for c in results[self.nodes[0].area_id]["criteria"]}
        cited = crits["weekday_competition"]["metric_ids"][0]
        self.conn.execute("UPDATE area_metrics SET metric_value = metric_value + 1 WHERE metric_id = ?", (cited,))
        with self.assertRaises(AssertionError):
            report.verify_citations(self.conn, results)

    def test_successful_run_writes_report_and_run_row(self):
        self._seed_minimal()
        path = report.run(self.db_path)
        self.assertTrue(path.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM analysis_runs WHERE report_ref IS NOT NULL"), 1)


if __name__ == "__main__":
    unittest.main()
