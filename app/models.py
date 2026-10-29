"""Defensive readers for LiteLLM success-webhook payloads (spec §5)."""

import hashlib
import json
from datetime import datetime, timezone

# SpendEvent rows travel as plain dicts straight into db.insert_event


def choices(payload: dict) -> list:
    resp = payload.get("response")
    if isinstance(resp, dict):
        ch = resp.get("choices")
        if isinstance(ch, list):
            return ch
    ch = payload.get("choices")
    return ch if isinstance(ch, list) else []


def response_text(payload: dict) -> str:
    return _completion_text(choices(payload))


def _completion_text(chs: list) -> str:
    # one rule for "what the completion text is": content + tool-call args (spec §7.3);
    # estimate.estimate_completion shares it so estimator and dedup hash never disagree
    parts: list[str] = []
    for ch in chs:
        msg = ch.get("message") if isinstance(ch, dict) else None
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            # tool-call-only completion: args stand in for the text (spec §7.3)
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                    parts.append(fn["arguments"])
    return "".join(parts)


def has_tool_calls(payload: dict) -> bool:
    for ch in choices(payload):
        msg = ch.get("message") if isinstance(ch, dict) else None
        if isinstance(msg, dict) and msg.get("tool_calls"):
            return True
    return False


def read_request_id(payload: dict) -> str:
    resp = payload.get("response")
    if isinstance(resp, dict) and resp.get("id"):
        return str(resp["id"])
    if payload.get("litellm_call_id"):
        return str(payload["litellm_call_id"])
    # fallback: hash a STABLE subset so redeliveries with new timing fields dedup
    stable = {
        "model": payload.get("model"),
        "messages": payload.get("messages"),
        "created": resp.get("created") if isinstance(resp, dict) else None,
        "text": response_text(payload),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def read_ts(payload: dict) -> str:
    resp = payload.get("response")
    if isinstance(resp, dict) and isinstance(resp.get("created"), (int, float)):
        try:
            return _iso_utc(datetime.fromtimestamp(resp["created"], timezone.utc))
        except (OverflowError, OSError, ValueError):
            pass  # out-of-range epoch -> fall through to wall-clock keys
    for key in ("end_time", "start_time"):
        v = payload.get(key)
        if isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)  # naive stamps are UTC (spec §7.7)
                return _iso_utc(dt)
            except ValueError:
                pass
    return _iso_utc(datetime.now(timezone.utc))


def read_ts_safe(payload: dict) -> str:
    # fallback path in main.py must not raise either (reviewer finding 4)
    try:
        return read_ts(payload)
    except Exception:
        return _iso_utc(datetime.now(timezone.utc))


def read_raw_model(payload: dict) -> str | None:
    m = payload.get("model")
    if not m:
        psr = payload.get("proxy_server_request")
        if isinstance(psr, dict):
            m = psr.get("model")
    return str(m) if m else None


def read_usage(payload: dict) -> tuple[int, int, bool]:
    """(prompt, completion, found). Completion shape first, then bare usage dict."""
    for src in (payload.get("response"), payload):
        if not isinstance(src, dict):
            continue
        usage = src.get("usage") if "usage" in src else src
        if isinstance(usage, dict) and ("prompt_tokens" in usage or "completion_tokens" in usage):
            try:
                return int(round(usage.get("prompt_tokens") or 0)), int(round(usage.get("completion_tokens") or 0)), True
            except (TypeError, ValueError):
                continue  # non-numeric counts -> treat as missing and estimate (§7.1)
    return 0, 0, False


def read_latency(payload: dict) -> int | None:
    for key in ("response_ms", "ms_since_first_proxy_request"):
        v = payload.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    start, end = payload.get("start_time"), payload.get("end_time")
    try:
        d0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        d1 = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        d = (d1 - d0).total_seconds() * 1000
        # clock skew can put end before start; schema says NULL when unknown, not negative junk
        return int(d) if d >= 0 else None
    except (TypeError, ValueError):
        return None
