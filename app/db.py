import os
import sqlite3
from typing import Literal

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS events (
  id                INTEGER PRIMARY KEY,
  request_id        TEXT    NOT NULL UNIQUE,
  ts                TEXT    NOT NULL,
  model             TEXT    NOT NULL,
  prompt_tokens     INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  latency_ms        INTEGER,
  calculated_cost   REAL    NOT NULL,
  is_estimated      INTEGER NOT NULL DEFAULT 0,
  is_tool_call      INTEGER NOT NULL DEFAULT 0,
  raw_model         TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_model ON events (model);

CREATE TABLE IF NOT EXISTS models (
  id              INTEGER PRIMARY KEY,
  pattern         TEXT    NOT NULL UNIQUE,
  input_per_mtok  REAL    NOT NULL,
  output_per_mtok REAL    NOT NULL,
  is_free         INTEGER NOT NULL DEFAULT 0
);
"""


def get_conn(path) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.fspath(path)) or ".", exist_ok=True)
    return sqlite3.connect(os.fspath(path))


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=wal")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        # additive migrations guard on user_version; v1 is create-only
        conn.executescript(DDL)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def insert_event(conn: sqlite3.Connection, event: dict) -> Literal["inserted", "duplicate"]:
    cur = conn.execute(
        """INSERT OR IGNORE INTO events (
             request_id, ts, model, prompt_tokens, completion_tokens,
             latency_ms, calculated_cost, is_estimated, is_tool_call, raw_model
           ) VALUES (:request_id, :ts, :model, :prompt_tokens, :completion_tokens,
                     :latency_ms, :calculated_cost, :is_estimated, :is_tool_call, :raw_model)""",
        event,
    )
    conn.commit()
    return "inserted" if cur.rowcount else "duplicate"
