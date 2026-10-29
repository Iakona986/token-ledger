"""payload dict -> SpendEvent row dict (spec §5, §7)."""

import logging

from app import estimate, models, pricing

log = logging.getLogger("token-ledger")


def build_spend_event(conn, payload: dict) -> dict:
    raw_model = models.read_raw_model(payload) or ""
    prompt, completion, usage_found = models.read_usage(payload)

    # flag only guesses: a real usage report (even zeros) is trusted as-is;
    # estimation runs only when usage is missing or a count is absent (§7.1)
    if not usage_found:
        prompt = estimate.estimate_prompt(
            payload.get("messages") or [], payload.get("tools")
        )
        completion = estimate.estimate_completion(models.choices(payload))
        is_estimated = int(prompt != 0 or completion != 0)
    else:
        is_estimated = 0

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
