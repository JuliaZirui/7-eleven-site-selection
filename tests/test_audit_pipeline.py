"""Data-plausibility audit in the pipeline (Phase 5): the refresh step,
the report line, and writing accepted findings to the correction tables.
No network, no API: the auditor runs against a scripted fake client."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers

import audit_apply
import audit_candidates
import audit_llm
import audit_pipeline
import cli
from test_audit_llm import URL, FakeClient, _report, _resp, _search_result

QUEUE_COLUMNS = audit_llm.QUEUE_COLUMNS


class PipelineStepTest(helpers.PipelineTestCase):
    def test_disabled(self):
        self.assertEqual(audit_pipeline.run(self.db_path, enabled=False)["status"], "disabled")

    def test_no_credentials_skip_with_a_message(self):
        with mock.patch.object(audit_llm, "preflight", side_effect=audit_llm.AuditError("API not usable (TypeError). x")), \
                mock.patch.object(audit_llm, "load_dotenv"):
            result = audit_pipeline.run(self.db_path)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("audit not run", result["error"])
        self.assertIn("ANTHROPIC_API_KEY", cli.hint_for(result["error"]))
        self.assertEqual(cli._errors(result)[0].split(": ", 1)[1], result["error"])

    def test_runs_each_site_and_queues_its_flags(self):
        node = self.nodes[0]
        ctx = audit_candidates.NodeContext(
            area_id=node.area_id, name=node.name, lat=node.lat, lon=node.lon, radius_m=node.radius_m,
            registrations=[{"opendata_id": "1", "employees_range": "1000 - 2499 Beschäftigte",
                            "lat": node.lat, "lon": node.lon}],
            visitor_records=[], competitors=[], osm_elements=[])
        cid = "concentration:ihk:1"
        responses = [_resp([_search_result(),
                            _report(cid, "t1", proposed={"low": 50, "central": 80, "high": 120, "unit": "persons_on_site"},
                                    sources=[{"url": URL, "quote": "80 people work here."}])], stop="tool_use"),
                     _resp([]),
                     _resp([_report("large_registration:ihk:1", "t2", verdict="plausible")], stop="tool_use"),
                     _resp([])]
        client = FakeClient(*responses)
        with mock.patch.object(audit_llm, "preflight"), mock.patch.object(audit_llm, "load_dotenv"), \
                mock.patch("models.CANDIDATE_NODES", [node]), \
                mock.patch.object(audit_candidates, "candidate_context", return_value=ctx), \
                mock.patch.object(audit_candidates, "_nearest_station_fn", return_value=lambda la, lo: ("S X", 10.0)), \
                mock.patch.object(audit_llm, "check_allowed"), \
                mock.patch.object(audit_llm, "PROJECT_ROOT", self.root), \
                mock.patch.object(audit_llm.Auditor, "__init__", lambda self, **kw: _init(self, client, **kw)):
            result = audit_pipeline.run(self.db_path)
        site = result["sites"][0]
        self.assertEqual((site["area_id"], site["flags"]), (node.area_id, 1))
        self.assertTrue((self.root / site["run_file"]).exists())
        line = audit_pipeline.summary_line(node.area_id)
        self.assertIn("1 flag(s) — 0 accepted, 0 rejected, 1 pending review", line)

    def test_summary_line_without_a_run(self):
        self.assertIn("not run for this site", audit_pipeline.summary_line("nowhere"))


def _init(self, client, max_usd=0.0, **kw):
    self.client, self.model, self.effort, self.max_usd = client, audit_llm.AUDIT_MODEL, audit_llm.AUDIT_EFFORT, max_usd
    self.calls, self.conversations = [], []


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self.root = Path(self._tmp.name)
        cal = root / "cal"
        cal.mkdir()
        heads = {
            "worker_onsite_verified.csv": "opendata_id,area_id,registered_class,identified_as,identification,onsite_low,"
                                          "onsite_central,onsite_high,confidence,basis,sources,station_check,"
                                          "weekend_share,weekend_basis",
            "worker_onsite_unregistered.csv": "name,area_id,lat,lon,onsite_low,onsite_central,onsite_high,confidence,"
                                              "basis,sources,weekend_share,weekend_basis",
            "venue_attendance_verified.csv": "de_title,annual_low,annual_central,annual_high,basis,sources",
            "poi_corrections_verified.csv": "external_ref,area_id,name,action,reason,evidence,found_by,reviewed_on",
        }
        for name, head in heads.items():
            (cal / name).write_text(head)  # no trailing newline on purpose
        tables = {k: cal / v.name for k, v in audit_apply.TABLES.items()}
        self.cal = cal
        run = {"node": {"area_id": "n"}, "findings": [
            {"candidate_id": "large_registration:ihk:7", "lat": None},
            {"candidate_id": "concentration:wikidata:Q1"},
            {"candidate_id": "missing_entity:osm:way/5"},
            {"candidate_id": "poi_duplicate:a|b"},
            {"candidate_id": "poi_category:c"}], "new_entities": []}
        (root / "run.json").write_text(json.dumps(run))
        wl_dir = root / "comparison" / "audit_runs" / "worklists"
        wl_dir.mkdir(parents=True)
        (wl_dir / "n.json").write_text(json.dumps({"candidates": [
            {"candidate_id": "large_registration:ihk:7", "record": {"employees_range": "500 - 999 Beschäftigte"}},
            {"candidate_id": "concentration:wikidata:Q1", "record": {"wikipedia_titles": {"dewiki": "Club X"}}},
            {"candidate_id": "missing_entity:osm:way/5", "record": {"name": "Tower", "lat": 52.5, "lon": 13.4}}]}))
        self.queue = root / "queue.csv"
        rows = [
            self._row("large_registration:ihk:7", "ihk:7", "company_wide_headcount", "persons_on_site", "accepted"),
            self._row("concentration:wikidata:Q1", "wikidata:Q1", "fame_vs_attendance", "visitors_per_year", "accepted"),
            self._row("missing_entity:osm:way/5", "osm:way/5", "missing_entity", "persons_on_site", "accepted"),
            self._row("poi_duplicate:a|b", "a|b", "duplicate", "", "accepted"),
            self._row("poi_category:c", "c", "category_error", "", "accepted", note=""),
            self._row("poi_category:c", "c", "category_error", "", "pending"),
        ]
        rows[3]["reviewer_note"] = "same shop; exclude=b"
        with self.queue.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        self._patches = [mock.patch.object(audit_apply, "TABLES", tables),
                         mock.patch.object(audit_apply, "PROJECT_ROOT", root)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _row(self, cid, ref, issue, unit, status, note="") -> dict:
        return {"queue_id": f"run:{cid}", "run_file": "run.json", "area_id": "n", "candidate_id": cid, "module": "m",
                "issue_type": issue, "verdict": "implausible", "record_ref": ref, "target_table": "",
                "proposed_low": "100" if unit else "", "proposed_central": "200" if unit else "",
                "proposed_high": "300" if unit else "", "proposed_unit": unit, "reason": "r",
                "sources": json.dumps([{"url": URL, "quote": "q"}]), "evidence_needed": "", "guard_notes": "",
                "review_status": status, "reviewer": "Zirui", "reviewer_note": note}

    def _table(self, name) -> list[dict]:
        with (self.cal / name).open() as f:
            return list(csv.DictReader(f))

    def test_dry_run_writes_nothing(self):
        report = audit_apply.apply(write=False, queue=self.queue)
        self.assertEqual(sum(l.startswith("would append") for l in report), 5)
        self.assertEqual(self._table("worker_onsite_verified.csv"), [])

    def test_write_appends_each_type_once(self):
        audit_apply.apply(write=True, queue=self.queue)
        reg = self._table("worker_onsite_verified.csv")
        self.assertEqual((reg[0]["opendata_id"], reg[0]["onsite_central"], reg[0]["registered_class"]),
                         ("7", "200.0", "500 - 999 Beschäftigte"))
        self.assertIn("reviewed by Zirui", reg[0]["basis"])
        self.assertEqual(self._table("venue_attendance_verified.csv")[0]["de_title"], "Club X")
        self.assertEqual(self._table("worker_onsite_unregistered.csv")[0]["name"], "Tower")
        self.assertEqual([r["external_ref"] for r in self._table("poi_corrections_verified.csv")], ["b", "c"])
        with self.queue.open() as f:
            statuses = [r["review_status"] for r in csv.DictReader(f)]
        self.assertEqual(statuses, ["applied"] * 5 + ["pending"])
        # A second run finds nothing left to do.
        self.assertEqual(audit_apply.apply(write=True, queue=self.queue), [])

    def test_rows_missing_what_the_table_needs_stay_accepted(self):
        with self.queue.open() as f:
            rows = list(csv.DictReader(f))
        rows[0]["proposed_central"] = ""
        rows[3]["reviewer_note"] = "same shop"
        with self.queue.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        report = audit_apply.apply(write=True, queue=self.queue)
        self.assertEqual(sum(l.startswith("left accepted") for l in report), 2)
        with self.queue.open() as f:
            statuses = [r["review_status"] for r in csv.DictReader(f)]
        self.assertEqual(statuses[0], "accepted")
        self.assertEqual(statuses[3], "accepted")


class AnnotateTest(ApplyTest):
    def test_hints_for_corrected_records_and_evaluation_labels(self):
        with (self.cal / "poi_corrections_verified.csv").open("a") as f:
            f.write("\nc,n,,exclude,known,,analyst,2026-10-01\n")
        labels = self.root / "labels.csv"
        labels.write_text("area_id;candidate_id;label\nn;poi_duplicate:a|b;false_alarm\n")
        with self.queue.open() as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["review_status"], r["reviewer_note"] = "pending", ""
        with self.queue.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        self.assertEqual(audit_apply.annotate(self.queue, labels), 3)
        with self.queue.open() as f:
            notes = {r["candidate_id"]: r["reviewer_note"] for r in csv.DictReader(f)}
        self.assertIn("already corrected in poi_corrections_verified.csv", notes["poi_category:c"])
        self.assertEqual(notes["poi_duplicate:a|b"], "hint: evaluation label false_alarm")
        self.assertEqual(notes["large_registration:ihk:7"], "")

    def test_hints_never_reach_a_correction_table(self):
        row = self._row("large_registration:ihk:7", "ihk:7", "company_wide_headcount", "persons_on_site", "accepted",
                        note="hint: evaluation label true_issue; checked the annual report")
        self.assertNotIn("hint", audit_apply._basis(row))
        self.assertIn("checked the annual report", audit_apply._basis(row))


class CliWiringTest(unittest.TestCase):
    def test_audit_runs_after_metrics_and_before_followup(self):
        keys = list(cli.STEP_KEYS)
        self.assertEqual(keys.index("audit"), keys.index("metrics") + 1)
        self.assertFalse(next(s for s in cli.STEPS if s.key == "audit").essential)

    def test_no_audit_flag(self):
        args = cli.build_parser().parse_args(["refresh", "--no-audit"])
        self.assertTrue(args.no_audit)


if __name__ == "__main__":
    unittest.main()
