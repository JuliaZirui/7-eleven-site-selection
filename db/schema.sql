-- Site-selection pipeline schema. See site_candidates.md and
-- project_plan_v3.md ("Database Structure" / "Key Rules") for the
-- rationale: data-as-of date vs. fetch date are always separate,
-- snapshots are appended (never overwritten), missing values are
-- never defaulted to 0, cross-source records are never summed
-- directly, and LOR codes are strings with an explicit boundary
-- version.

CREATE TABLE areas (
    area_id             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    anchor_lat          REAL NOT NULL,
    anchor_lon          REAL NOT NULL,
    radius_m            INTEGER NOT NULL,
    lor_code            TEXT,
    lor_boundary_version TEXT,
    created_at          TEXT NOT NULL,
    notes               TEXT
);

CREATE TABLE source_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    data_as_of_date TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    license         TEXT,
    raw_file_ref    TEXT,
    notes           TEXT
);

CREATE TABLE observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
    area_id         TEXT NOT NULL REFERENCES areas(area_id),
    entity_type     TEXT NOT NULL,
    external_ref    TEXT,
    name            TEXT,
    category        TEXT,
    lat             REAL,
    lon             REAL,
    opening_hours_raw TEXT,
    payload_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_observations_snapshot ON observations(snapshot_id);
CREATE INDEX idx_observations_area ON observations(area_id);
CREATE UNIQUE INDEX idx_observations_dedup
    ON observations(snapshot_id, entity_type, external_ref)
    WHERE external_ref IS NOT NULL;

CREATE TABLE area_metrics (
    metric_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id             TEXT NOT NULL REFERENCES areas(area_id),
    metric_name         TEXT NOT NULL,
    metric_value        REAL,
    is_missing          INTEGER NOT NULL DEFAULT 0,
    missing_reason       TEXT,
    unit                TEXT NOT NULL,
    period_label        TEXT NOT NULL,
    computation_method_version TEXT NOT NULL,
    source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    computed_at         TEXT NOT NULL,
    CHECK ((is_missing = 0 AND metric_value IS NOT NULL)
        OR (is_missing = 1 AND metric_value IS NULL))
);

CREATE INDEX idx_area_metrics_lookup
    ON area_metrics(area_id, metric_name, period_label);

CREATE TABLE development_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id         TEXT NOT NULL REFERENCES areas(area_id),
    event_type      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    event_date      TEXT,
    source_snapshot_id INTEGER REFERENCES source_snapshots(snapshot_id),
    source_url      TEXT,
    recorded_at     TEXT NOT NULL
);

CREATE INDEX idx_development_events_area ON development_events(area_id);

CREATE TABLE analysis_runs (
    run_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at                  TEXT NOT NULL,
    store_hypothesis_json   TEXT NOT NULL,
    area_ids_json           TEXT NOT NULL,
    snapshot_ids_json       TEXT NOT NULL,
    weights_json            TEXT,
    model_version           TEXT,
    tool_log_json           TEXT,
    report_ref              TEXT,
    notes                   TEXT
);
