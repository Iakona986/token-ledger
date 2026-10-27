from app import db, ingest


def _conn(tmp_path):
    conn = db.get_conn(tmp_path / "i.db")
    db.init_db(conn)
    conn.executemany(
        "INSERT INTO models (pattern, input_per_mtok, output_per_mtok, is_free) VALUES (?,?,?,?)",
        [
            ("gpt-4o*", 2.5, 10.0, 0),
            ("ollama/*", 0.0, 0.0, 1),
            ("local/*", 0.0, 0.0, 1),
        ],
    )
    conn.commit()
    return conn


def _full_payload():
    return {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "response": {
            "id": "chatcmpl-1",
            "created": 1767225600,
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            "choices": [{"message": {"content": "ok"}}],
        },
        "response_ms": 42,
    }


def test_full_payload_exact_cost(tmp_path):
    ev = ingest.build_spend_event(_conn(tmp_path), _full_payload())
    assert ev["calculated_cost"] == 12.5
    assert ev["request_id"] == "chatcmpl-1"
    assert ev["is_estimated"] == 0
    assert ev["ts"].startswith("2026-01-01")
    assert ev["latency_ms"] == 42


def test_streaming_estimated(tmp_path):
    p = _full_payload()
    del p["response"]["usage"]
    ev = ingest.build_spend_event(_conn(tmp_path), p)
    assert ev["is_estimated"] == 1
    assert ev["prompt_tokens"] > 0 and ev["completion_tokens"] > 0
    assert ev["calculated_cost"] > 0


def test_bare_usage_dict_response(tmp_path):
    p = _full_payload()
    p["response"] = {"prompt_tokens": 500, "completion_tokens": 250}
    ev = ingest.build_spend_event(_conn(tmp_path), p)
    assert (ev["prompt_tokens"], ev["completion_tokens"]) == (500, 250)
    assert ev["is_estimated"] == 0


def test_tool_call_flag_no_double_count(tmp_path):
    p = _full_payload()
    p["response"]["choices"] = [
        {"message": {"content": "", "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}}
    ]
    ev = ingest.build_spend_event(_conn(tmp_path), p)
    assert ev["is_tool_call"] == 1
    assert ev["completion_tokens"] == 1_000_000  # usage still wins, no extra count


def test_free_model_zero(tmp_path):
    p = _full_payload()
    p["model"] = "ollama/llama3"
    ev = ingest.build_spend_event(_conn(tmp_path), p)
    assert ev["calculated_cost"] == 0.0


def test_unknown_model_zero(tmp_path, caplog):
    p = _full_payload()
    p["model"] = "zzz-unknown"
    ev = ingest.build_spend_event(_conn(tmp_path), p)
    assert ev["calculated_cost"] == 0.0
    assert "zzz-unknown" in caplog.text  # WARNING naming the model


def test_latency_fallback_chain(tmp_path):
    conn = _conn(tmp_path)
    p = _full_payload()
    del p["response_ms"]
    p["ms_since_first_proxy_request"] = 77
    assert ingest.build_spend_event(conn, p)["latency_ms"] == 77
    del p["ms_since_first_proxy_request"]
    p["start_time"] = "2026-01-01T00:00:00Z"
    p["end_time"] = "2026-01-01T00:00:02Z"
    assert ingest.build_spend_event(conn, p)["latency_ms"] == 2000
    del p["start_time"], p["end_time"]
    assert ingest.build_spend_event(conn, p)["latency_ms"] is None
