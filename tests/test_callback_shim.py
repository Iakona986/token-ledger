"""Self-check for the LiteLLM sender shim: payload keys and non-200 logging."""

import asyncio
import logging
import types

import pytest

httpx = pytest.importorskip("httpx")

import token_ledger_callback as shim


class _FakeClient:
    def __init__(self, captured, status=200):
        self.captured = captured
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.captured.update(url=url, payload=json, headers=headers)
        return types.SimpleNamespace(status_code=self.status)


def _run_shim(monkeypatch, status=200):
    captured = {}
    monkeypatch.setattr(
        shim.httpx, "AsyncClient", lambda **kw: _FakeClient(captured, status)
    )
    resp_obj = types.SimpleNamespace(
        model_dump_json=lambda: (
            '{"id": "chatcmpl-1", "choices": [{"message": {"content": "hi"}}]}'
        )
    )
    kwargs = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": None,
        "litellm_call_id": "call-1",
        "proxy_server_request": {"model": "gpt-4o"},
        "response_ms": 42,
    }
    asyncio.run(
        shim.token_ledger_handler.async_log_success_event(
            kwargs, resp_obj, "2026-01-01T00:00:00", "2026-01-01T00:00:01"
        )
    )
    return captured


def test_shim_posts_expected_payload(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", "s3cret")
    got = _run_shim(monkeypatch)
    assert got["url"] == "http://localhost:8000/webhook"
    assert got["headers"]["Authorization"] == "Bearer s3cret"
    p = got["payload"]
    for key in (
        "model",
        "messages",
        "litellm_call_id",
        "proxy_server_request",
        "response_ms",
        "start_time",
        "end_time",
        "response",
    ):
        assert key in p
    assert p["response"]["id"] == "chatcmpl-1"


def test_shim_logs_non_200(monkeypatch, caplog):
    # audit finding 2: a 401/503 response is not an httpx exception; it must be logged
    with caplog.at_level(logging.WARNING, logger="token-ledger-shim"):
        _run_shim(monkeypatch, status=503)
    assert "503" in caplog.text
