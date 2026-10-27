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


def test_config_defaults(tmp_path, monkeypatch):
    # empty environment -> spec §8 defaults
    for key in ("TOKEN_LEDGER_DB", "TOKEN_LEDGER_HOST", "TOKEN_LEDGER_PORT",
                "WEBHOOK_SHARED_SECRET", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    import app.config as cfgmod

    with monkeypatch.context() as m:
        m.chdir(tmp_path)  # no .env here
        c = cfgmod.build_config()
    assert c.token_ledger_db == "token_ledger.db"
    assert c.token_ledger_host == "127.0.0.1"
    assert c.token_ledger_port == 8000
    assert c.log_level == "INFO"


def test_dotenv_only_values_are_loaded(tmp_path, monkeypatch):
    # Critical-1 regression: a value that exists ONLY in .env must reach the config
    for key in ("TOKEN_LEDGER_DB", "WEBHOOK_SHARED_SECRET"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text("WEBHOOK_SHARED_SECRET=from-dotenv\n")
    import app.config as cfgmod

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        c = cfgmod.build_config()
    assert c.webhook_shared_secret == "from-dotenv"


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("WEBHOOK_SHARED_SECRET=from-dotenv\n")
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", "from-real-env")
    import app.config as cfgmod

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        c = cfgmod.build_config()
    assert c.webhook_shared_secret == "from-real-env"


def test_same_thread_connection_works_through_testclient(tmp_path, monkeypatch):
    # reviewer Critical 2 regression: endpoints must run without sqlite3.ProgrammingError
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOKEN_LEDGER_DB", str(tmp_path / "st.db"))
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    import app.main as mainmod

    with TestClient(mainmod.app) as c:
        assert c.get("/health").status_code == 200
        r = c.post("/webhook", json={"model": "x", "response": {"id": "y"}})
        assert r.status_code == 200  # not a 500 ProgrammingError
