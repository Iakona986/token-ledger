import pytest
from fastapi.testclient import TestClient


def _payload(request_id="chatcmpl-1"):
    return {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "response": {
            "id": request_id,
            "created": 1767225600,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "choices": [{"message": {"content": "ok"}}],
        },
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    # no .env in tmp_path: build_config() there sees only the monkeypatched env
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOKEN_LEDGER_DB", str(tmp_path / "api.db"))
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    import app.main as mainmod

    with TestClient(mainmod.app) as c:
        c.state_db = str(tmp_path / "api.db")
        yield c


def _seed(client):
    import sqlite3
    from app import pricing

    conn = sqlite3.connect(client.state_db)
    conn.execute(
        "INSERT INTO models (pattern, input_per_mtok, output_per_mtok, is_free) VALUES ('gpt-4o*',2.5,10.0,0)"
    )
    conn.commit()
    conn.close()
    pricing.clear_cache()


def test_happy_path_inserted(client):
    _seed(client)
    r = client.post("/webhook", json=_payload())
    assert r.status_code == 200 and r.json() == {"status": "inserted"}
    # end-to-end: the stored row carries the priced cost, with gpt-4o* seeded (reviewer rec)
    import sqlite3

    row = sqlite3.connect(client.state_db).execute(
        "SELECT calculated_cost FROM events WHERE request_id='chatcmpl-1'"
    ).fetchone()
    assert row[0] == pytest.approx(10 / 1e6 * 2.5 + 20 / 1e6 * 10.0)


def test_duplicate_returns_200_duplicate(client):
    _seed(client)
    client.post("/webhook", json=_payload())
    r = client.post("/webhook", json=_payload())
    assert r.status_code == 200 and r.json() == {"status": "duplicate"}


def test_invalid_body_422(client):
    r = client.post("/webhook", json={"nonsense": True})
    assert r.status_code == 422


def test_auth_required_when_secret_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("TOKEN_LEDGER_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", "s3cret")
    import app.main as mainmod

    with TestClient(mainmod.app) as c:
        assert c.post("/webhook", json=_payload()).status_code == 401
        r = c.post("/webhook", json=_payload(), headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401  # acceptance case 7: wrong bearer, not just missing
        r = c.post("/webhook", json=_payload(), headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200


def test_auth_skipped_when_secret_unset(client):
    # Review Focus 5: header present but no secret configured -> accepted
    _seed(client)
    r = client.post("/webhook", json=_payload(), headers={"Authorization": "Bearer whatever"})
    assert r.status_code == 200


def test_health_ok_and_writable(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_pricing_gap_never_5xx(client):
    # no models rows at all + garbage payload shape -> still 200, cost 0
    r = client.post(
        "/webhook",
        json={"model": "zzz", "response": {"id": "x1", "created": 1767225600}},
    )
    assert r.status_code == 200 and r.json() == {"status": "inserted"}
