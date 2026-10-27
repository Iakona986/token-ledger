"""payload dict -> SpendEvent row dict (spec §5, §7)."""

import logging

from app import estimate, models, pricing

log = logging.getLogger("token-ledger")


def build_spend_event(conn, payload: dict) -> dict:
    raw_model = models.read_raw_model(payload) or ""
    prompt, completion, usage_found = models.read_usage(payload)
    is_estimated = 0
    if not usage_found or prompt == 0:
        prompt = estimate.estimate_prompt(payload.get("messages") or [], payload.get("tools"))
        is_estimated = 1
    if not usage_found or completion == 0:
        completion = estimate.estimate_completion(models.choices(payload))
        is_estimated = 1

    # money math never trusts input sign (audit finding 1); negatives are garbage
    prompt, completion = max(0, prompt), max(0, completion)

    row = pricing.resolve(conn, raw_model) if raw_model else None
    if row is None:
        log.warning("unknown model %r — storing at cost 0.0", raw_model)
        calculated = 0.0
    else:
        calculated = pricing.cost(prompt, completion, row)

    return {
        "request_id": models.read_request_id(payload),
        "ts": models.read_ts(payload),
        "model": pricing.normalize(raw_model) if raw_model else "",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "latency_ms": models.read_latency(payload),
        "calculated_cost": calculated,
        "is_estimated": is_estimated,
        "is_tool_call": int(models.has_tool_calls(payload)),
        "raw_model": raw_model or None,
    }
