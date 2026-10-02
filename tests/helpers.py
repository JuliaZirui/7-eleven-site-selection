"""Shared fixtures for the Day 6 fixed test cases.

Every test runs against its own throwaway SQLite database built from
db/schema.sql, with every module-level path (raw-file dirs, reports,
PROJECT_ROOT) redirected into a temp directory -- the real
db/site_selection.db and db/raw/ are never touched. Network access is
blocked by default: any httpx.get/post a test didn't explicitly mock
raises, so a test can never pass by quietly hitting a live API.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from models import CANDIDATE_NODES, LocationInput  # noqa: E402

SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"

# Counts every mocked network call a test makes, so the Day 6 runner
# can log "API calls attempted" per category without any real traffic.
NETWORK_CALLS = {"count": 0}


class UnmockedNetworkCall(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise UnmockedNetworkCall(f"test attempted a real network call: {args[:1]}")


def make_node(area_id: str = "test_node", lat: float = 52.52, lon: float = 13.40, radius_m: int = 400) -> LocationInput:
    return LocationInput(area_id=area_id, name=area_id.replace("_", " ").title(), lat=lat, lon=lon, radius_m=radius_m)


class PipelineTestCase(unittest.TestCase):
    """Temp DB + temp dirs + network guard, per test."""

    nodes: tuple[LocationInput, ...] = (make_node(),)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "db" / "raw").mkdir(parents=True)
        self.db_path = self.root / "db" / "test.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.executescript(SCHEMA_PATH.read_text())
        now = datetime.now(timezone.utc).isoformat()
        for n in self.nodes:
            self.conn.execute(
                "INSERT INTO areas (area_id, name, anchor_lat, anchor_lon, radius_m, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (n.area_id, n.name, n.lat, n.lon, n.radius_m, now),
            )
        self.conn.commit()

        self._patches = [
            mock.patch("httpx.get", side_effect=_blocked),
            mock.patch("httpx.post", side_effect=_blocked),
            # Overture/FSQ read parquet through DuckDB's own HTTP stack,
            # which the httpx guard can't see -- block them at the source.
            mock.patch("poi_pipeline.fetch_overture_places", side_effect=_blocked),
            mock.patch("poi_pipeline.fetch_fsq_places", side_effect=_blocked),
        ]
        for p in self._patches:
            p.start()
        self._redirect_paths()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self.conn.close()
        self._tmp.cleanup()

    def _redirect_paths(self) -> None:
        import agent_followup
        import ihk_pipeline
        import lor_pipeline
        import metrics_pipeline
        import poi_conflation
        import poi_pipeline
        import report

        raw = self.root / "db" / "raw"
        (self.root / "store_hypothesis.md").write_text((PROJECT_ROOT / "store_hypothesis.md").read_text())
        targets = {
            poi_pipeline: {
                "PROJECT_ROOT": self.root, "RAW_DIR": raw / "osm", "DB_PATH": self.db_path,
                "OVERTURE_RAW_DIR": raw / "overture", "FSQ_RAW_DIR": raw / "fsq", "FSQ_CACHE_DIR": raw / "fsq" / "cache",
            },
            poi_conflation: {"PROJECT_ROOT": self.root, "RAW_DIR": raw / "conflated"},
            ihk_pipeline: {"PROJECT_ROOT": self.root, "RAW_DIR": raw / "ihk", "DB_PATH": self.db_path},
            lor_pipeline: {"PROJECT_ROOT": self.root, "RAW_DIR": raw / "lor", "DB_PATH": self.db_path},
            metrics_pipeline: {"PROJECT_ROOT": self.root, "DB_PATH": self.db_path},
            agent_followup: {
                "PROJECT_ROOT": self.root, "DB_PATH": self.db_path,
                "AGENT_LOG_DIR": raw / "agent_log", "LOR_RAW_DIR": raw / "lor",
            },
            report: {
                "PROJECT_ROOT": self.root, "DB_PATH": self.db_path, "REPORTS_DIR": self.root / "reports",
                "AGENT_LOG_DIR": raw / "agent_log", "STORE_HYPOTHESIS_PATH": self.root / "store_hypothesis.md",
            },
        }
        for module, attrs in targets.items():
            for name, value in attrs.items():
                p = mock.patch.object(module, name, value)
                p.start()
                self._patches.append(p)

    def mock_network(self, target: str, **kwargs) -> mock.MagicMock:
        """Patch a network function for the rest of this test, counting calls."""
        side_effect = kwargs.pop("side_effect", None)
        return_value = kwargs.pop("return_value", None)
        m = mock.MagicMock(**kwargs)

        def counted(*a, **kw):
            NETWORK_CALLS["count"] += 1
            if side_effect is not None:
                if isinstance(side_effect, BaseException) or (isinstance(side_effect, type) and issubclass(side_effect, BaseException)):
                    raise side_effect
                if callable(side_effect):
                    return side_effect(*a, **kw)
                return next(side_effect)
            return return_value

        m.side_effect = counted
        p = mock.patch(target, m)
        p.start()
        self._patches.append(p)
        return m

    # ---- seeding helpers -------------------------------------------------

    def add_snapshot(self, source_name: str, fetched_at: str | None = None, data_as_of: str = "2026-09-23",
                     raw_file_ref: str | None = None) -> int:
        fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO source_snapshots (source_name, data_as_of_date, fetched_at, license, raw_file_ref, notes) "
            "VALUES (?, ?, ?, 'test', ?, 'test fixture')",
            (source_name, data_as_of, fetched_at, raw_file_ref),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_poi(self, snapshot_id: int, area_id: str, ref: str, name: str | None, category: str,
                lat: float, lon: float, hours: str | None) -> None:
        self.conn.execute(
            "INSERT INTO observations (snapshot_id, area_id, entity_type, external_ref, name, category, lat, lon, "
            "opening_hours_raw, payload_json) VALUES (?, ?, 'poi', ?, ?, ?, ?, ?, ?, '{}')",
            (snapshot_id, area_id, ref, name, category, lat, lon, hours),
        )
        self.conn.commit()

    def add_business(self, snapshot_id: int, area_id: str, ref: str, branch: str, employees: str,
                     branch_id: str = "62", lat: float = 52.52, lon: float = 13.40) -> None:
        payload = {"branch_top_level_desc": branch, "branch_top_level_id": branch_id, "employees_range": employees}
        self.conn.execute(
            "INSERT INTO observations (snapshot_id, area_id, entity_type, external_ref, name, category, lat, lon, "
            "opening_hours_raw, payload_json) VALUES (?, ?, 'business', ?, NULL, ?, ?, ?, NULL, ?)",
            (snapshot_id, area_id, ref, branch, lat, lon, json.dumps(payload)),
        )
        self.conn.commit()

    def add_population(self, snapshot_id: int, area_id: str, population: int | None, plr_id: str = "01000001") -> None:
        payload = {"plr_id": plr_id, "plr_name": "Test PLR", "population": population, "as_of_period": 202412}
        self.conn.execute(
            "INSERT INTO observations (snapshot_id, area_id, entity_type, external_ref, name, category, lat, lon, "
            "opening_hours_raw, payload_json) VALUES (?, ?, 'lor_population', ?, 'Test PLR', 'population', NULL, NULL, NULL, ?)",
            (snapshot_id, area_id, f"plr/{plr_id}", json.dumps(payload)),
        )
        self.conn.commit()

    def metric(self, area_id: str, name: str, period: str | None = None) -> dict:
        sql = ("SELECT metric_value, is_missing, missing_reason, period_label FROM area_metrics "
               "WHERE area_id = ? AND metric_name = ?")
        args: list = [area_id, name]
        if period is not None:
            sql += " AND period_label = ?"
            args.append(period)
        row = self.conn.execute(sql + " ORDER BY metric_id DESC LIMIT 1", args).fetchone()
        self.assertIsNotNone(row, f"metric {name} was never written for {area_id}")
        return {"value": row[0], "is_missing": bool(row[1]), "reason": row[2], "period": row[3]}

    def count(self, sql: str, *args) -> int:
        return self.conn.execute(sql, args).fetchone()[0]
