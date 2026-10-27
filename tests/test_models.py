from app import models


def _payload(**over):
    p = {
        "model": "openai/gpt-4o",
        "litellm_call_id": "call-1",
        "response": {"id": "chatcmpl-abc", "created": 1767225600, "choices": []},
    }
    p.update(over)
    return p


def test_request_id_priority():
    assert models.read_request_id(_payload()) == "chatcmpl-abc"
    p = _payload()
    del p["response"]["id"]
    assert models.read_request_id(p) == "call-1"
    p2 = _payload()
    del p2["response"]["id"]
    del p2["litellm_call_id"]
    assert models.read_request_id(p2) != ""


def test_hash_fallback_stable_across_redelivery():
    a = _payload(end_time="2026-01-01T00:00:00Z", response_ms=120)
    del a["response"]["id"]
    del a["litellm_call_id"]
    b = dict(a, end_time="2026-01-01T00:00:05Z", response_ms=999)
    assert models.read_request_id(a) == models.read_request_id(b)


def test_usage_completion_shape():
    p = _payload(response={"id": "x", "usage": {"prompt_tokens": 3, "completion_tokens": 7}})
    assert models.read_usage(p) == (3, 7, True)


def test_usage_bare_dict():
    p = _payload(response={"prompt_tokens": 5, "completion_tokens": 2})
    assert models.read_usage(p) == (5, 2, True)


def test_top_level_usage_key():
    # spec §5: response.usage.* -> usage.* -> estimate
    p = _payload(usage={"prompt_tokens": 1, "completion_tokens": 2}, response={})
    assert models.read_usage(p) == (1, 2, True)


def test_missing_everything_returns_none():
    assert models.read_usage({}) == (0, 0, False)
    assert models.read_latency({}) is None
    assert models.read_raw_model({}) is None
    assert models.read_ts({}) is not None  # falls back to ingest time
    assert models.has_tool_calls({}) is False
    assert models.response_text({}) == ""


def test_latency_fallback_chain():
    assert models.read_latency({"response_ms": 10}) == 10
    assert models.read_latency({"ms_since_first_proxy_request": 20}) == 20
    p = {"start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-01T00:00:03Z"}
    assert models.read_latency(p) == 3000


def test_end_time_used_when_no_created():
    p = {"end_time": "2020-01-01T00:00:00Z"}
    assert models.read_ts(p).startswith("2020-01-01")


def test_created_wins_over_end_time():
    p = _payload(end_time="2020-01-01T00:00:00Z")  # created=1767225600 -> 2026
    assert models.read_ts(p).startswith("2026")


def test_tool_calls_detection():
    p = _payload(
        response={
            "choices": [{"message": {"content": "", "tool_calls": [{"id": "t1"}]}}]
        }
    )
    assert models.has_tool_calls(p) is True
    assert models.response_text(_payload(response={"choices": [{"message": {"content": "hi"}}]})) == "hi"
