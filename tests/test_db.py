import sqlite3

from app import db


def _event(request_id="chatcmpl-1", cost=0.012):
    return {
        "request_id": request_id,
        "ts": "2026-01-01T00:00:00+00:00",
        "model": "gpt-4o",
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "latency_ms": 5,
        "calculated_cost": cost,
        "is_estimated": 0,
        "is_tool_call": 0,
        "raw_model": "openai/gpt-4o",
    }


def test_init_creates_tables_and_wal(tmp_path):
    conn = db.get_conn(tmp_path / "ledger.db")
    db.init_db(conn)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
    }
    assert {"events", "models", "idx_events_ts", "idx_events_model"} <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_insert_then_duplicate(tmp_path):
    conn = db.get_conn(tmp_path / "ledger.db")
    db.init_db(conn)
    assert db.insert_event(conn, _event()) == "inserted"
    # second delivery, different cost: first row must win untouched
    assert db.insert_event(conn, _event(cost=999.0)) == "duplicate"
    rows = conn.execute("SELECT calculated_cost FROM events").fetchall()
    assert len(rows) == 1 and rows[0][0] == 0.012


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("TOKEN_LEDGER_DB", raising=False)
    from app.config import config

    assert config.token_ledger_db == "token_ledger.db"
    assert config.token_ledger_host == "127.0.0.1"
    assert config.token_ledger_port == 8000
    assert config.log_level == "INFO"
