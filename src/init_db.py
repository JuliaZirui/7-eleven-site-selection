"""Create the SQLite database and seed the areas table from CANDIDATE_NODES."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models import CANDIDATE_NODES

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "site_selection.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def init_db(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.close()
    seed_areas(db_path)


def seed_areas(db_path: Path = DB_PATH) -> list[str]:
    """Add an areas row for every input site not yet in the database;
    returns the area_ids added. Existing rows are never changed."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    before = {r[0] for r in conn.execute("SELECT area_id FROM areas")}
    now = datetime.now(timezone.utc).isoformat()
    for node in CANDIDATE_NODES:
        conn.execute(
            """
            INSERT OR IGNORE INTO areas
                (area_id, name, anchor_lat, anchor_lon, radius_m, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (node.area_id, node.name, node.lat, node.lon, node.radius_m, now),
        )

    conn.commit()
    conn.close()
    return [n.area_id for n in CANDIDATE_NODES if n.area_id not in before]


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
