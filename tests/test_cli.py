"""Entry point (src/cli.py) and site input (src/site_input.py): input
validation, failure handling and exit codes, and the offline demo."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (sets sys.path for src/, blocks the network)

import cli
import models
import site_input
from site_input import SiteInputError, parse_sites

GOOD = {"area_id": "alexanderplatz", "name": "Alexanderplatz", "lat": 52.5215, "lon": 13.4113, "radius_m": 400,
        "gtfs_stations": ["S+U Alexanderplatz Bhf (Berlin)"]}


class _TempFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        cli.log.handlers[:] = [logging.NullHandler()]
        cli.log.propagate = False

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, payload, name="sites.json") -> Path:
        path = self.tmp / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path


class SiteInput(_TempFiles):
    def test_example_input_is_what_the_pipeline_scores(self):
        sites = parse_sites(site_input.DEFAULT_SITE_INPUT)
        self.assertEqual([s["area_id"] for s in sites], [n.area_id for n in models.CANDIDATE_NODES])
        self.assertEqual(models.NODE_STATIONS["warschauer_strasse"],
                         ("S+U Warschauer Str. (Berlin)", "S Warschauer Str. (Berlin)"))

    def test_each_input_error_names_the_field(self):
        cases = {
            "not valid JSON": '{"sites": [',
            'non-empty "sites"': {"sites": []},
            "missing gtfs_stations": {"sites": [{k: v for k, v in GOOD.items() if k != "gtfs_stations"}]},
            "area_id must be lowercase": {"sites": [{**GOOD, "area_id": "Alex Platz"}]},
            "duplicate area_id": {"sites": [GOOD, GOOD]},
            "outside Berlin": {"sites": [{**GOOD, "lat": 53.55, "lon": 10.0}]},
            "radius_m must be 400": {"sites": [{**GOOD, "radius_m": 600}]},
            "must be numbers": {"sites": [{**GOOD, "lat": "52.5"}]},
        }
        for expected, payload in cases.items():
            with self.subTest(expected):
                with self.assertRaisesRegex(SiteInputError, expected):
                    parse_sites(self.write(payload))
        with self.assertRaisesRegex(SiteInputError, "file not found"):
            parse_sites(self.tmp / "nope.json")

    def test_validate_rejects_an_unknown_station_and_suggests_one(self):
        path = self.write({"sites": [{**GOOD, "gtfs_stations": ["S+U Alexanderplatz"]}]})
        with self.assertLogs(cli.log, "ERROR") as logs:
            self.assertIsNone(cli.check_sites(path))
        self.assertIn("S+U Alexanderplatz Bhf (Berlin)", logs.output[0])
        self.assertEqual(cli.check_sites(self.write({"sites": [GOOD]})), ["alexanderplatz"])


class FailureFeedback(_TempFiles):
    def _refresh(self, step_results: dict) -> int:
        """Run cmd_refresh with every step replaced: a value is returned
        as the step's result, an exception is raised."""
        db = self.tmp / "site.db"
        db.touch()

        def fake(step, db_path, args):
            out = step_results.get(step.key, {})
            if isinstance(out, Exception):
                raise out
            return out

        args = argparse.Namespace(sites=None, db=str(db), only=None, policy="impact_rule")
        with mock.patch.object(cli, "_run_step", side_effect=fake), \
                mock.patch.object(cli, "check_sites", return_value=["a"]), \
                mock.patch.object(cli, "PROJECT_ROOT", self.tmp):
            return cli.cmd_refresh(args)

    def test_all_steps_ok_exits_0(self):
        self.assertEqual(self._refresh({}), cli.EXIT_OK)

    def test_a_skipped_source_is_a_warning_with_a_hint(self):
        skipped = {"result": [{"area_id": "a", "sources": {"fsq_os_places": {"error": "HF_TOKEN is not set."}}}]}
        with self.assertLogs(cli.log, "WARNING") as logs:
            self.assertEqual(self._refresh({"poi": skipped}), cli.EXIT_WARN)
        self.assertTrue(any("Foursquare skipped" in line for line in logs.output))

    def test_a_failed_network_step_does_not_stop_the_run(self):
        ran = []

        def fake(step, db_path, args):
            ran.append(step.key)
            if step.key == "lor":
                raise RuntimeError("LOR fetch failed: timeout")
            return {}

        db = self.tmp / "site.db"
        db.touch()
        args = argparse.Namespace(sites=None, db=str(db), only=None, policy="impact_rule")
        with mock.patch.object(cli, "_run_step", side_effect=fake), \
                mock.patch.object(cli, "check_sites", return_value=["a"]), \
                mock.patch.object(cli, "PROJECT_ROOT", self.tmp):
            self.assertEqual(cli.cmd_refresh(args), cli.EXIT_WARN)
        self.assertEqual(ran, list(cli.STEP_KEYS))  # report still built from the stored snapshots

    def test_a_database_outside_the_repository_is_refused(self):
        args = argparse.Namespace(sites=None, db=str(self.tmp / "x.db"), only=None, policy="impact_rule")
        with mock.patch.object(cli, "check_sites", return_value=["a"]):
            self.assertEqual(cli.cmd_refresh(args), cli.EXIT_FAIL)

    def test_a_failed_essential_step_stops_with_exit_2(self):
        self.assertEqual(self._refresh({"metrics": RuntimeError("no such table: area_metrics")}), cli.EXIT_FAIL)

    def test_errors_are_found_at_any_depth(self):
        result = {"result": [{"area_id": "a", "sources": {"osm": {"error": "x"}, "fsq": {"snapshot_id": 1}}},
                             {"area_id": "b", "error": "y"}]}
        self.assertEqual(cli._errors(result), ["a / osm: x", "b: y"])
        # The poi step repeats the OSM error at area level; multi-line errors keep their first line.
        poi = [{"area_id": "a", "error": "Overpass failed:\nmirror 1", "sources": {"osm": {"error": "Overpass failed:\nmirror 1"}}}]
        self.assertEqual(cli._errors(poi), ["a / osm: Overpass failed:"])

    def test_hints(self):
        self.assertIn("Overpass", cli.hint_for("OverpassError: all mirrors failed"))
        self.assertIn("refresh", cli.hint_for("ConflationError: [x] no OSM source snapshot to conflate against"))
        self.assertIsNone(cli.hint_for("something nobody anticipated"))


class RunIsolation(unittest.TestCase):
    def test_a_custom_database_gets_its_own_reports_and_agent_logs(self):
        self.assertIsNone(cli.run_dir(cli.DB_PATH))
        self.assertEqual(cli.run_dir(cli.PROJECT_ROOT / "db" / "my_sites.db"), cli.PROJECT_ROOT / "db" / "my_sites")


class Demo(_TempFiles):
    def test_demo_runs_offline_and_reproduces_the_recorded_run(self):
        out = self.tmp / "demo"
        before = helpers.PROJECT_ROOT.joinpath("db", "site_selection.db").read_bytes()
        code = cli.cmd_demo(argparse.Namespace(out=str(out), as_of="recorded"))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(before, helpers.PROJECT_ROOT.joinpath("db", "site_selection.db").read_bytes())
        scores = json.loads(next((out / "reports").glob("site_scores_*.json")).read_text())
        conn = sqlite3.connect(helpers.PROJECT_ROOT / "db" / "site_selection.db")
        notes = conn.execute("SELECT notes FROM analysis_runs WHERE report_ref IS NOT NULL "
                             "ORDER BY run_id DESC LIMIT 1").fetchone()[0]
        conn.close()
        recorded = json.loads((helpers.PROJECT_ROOT / json.loads(notes)["scores_ref"]).read_text())
        for area, r in recorded["results"].items():
            self.assertAlmostEqual(scores["results"][area]["business_fit"], r["business_fit"], places=9)

    def test_demo_refuses_a_different_site_input(self):
        with mock.patch.dict("os.environ", {"SITE_INPUT": str(self.write({"sites": [GOOD]}))}):
            self.assertEqual(cli.cmd_demo(argparse.Namespace(out=str(self.tmp / "d"), as_of="recorded")),
                             cli.EXIT_FAIL)


if __name__ == "__main__":
    unittest.main()
