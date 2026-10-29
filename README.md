# token-ledger

My BootDev personal project: a small ingest service that sits behind a [LiteLLM](https://github.com/BerriAI/litellm) proxy, receives its success webhooks, and keeps a local cost ledger of every LLM call in SQLite.

Each webhook is normalized into a `SpendEvent`, priced immediately against a per-million-token rate table, and stored once — duplicate deliveries of the same `request_id` are ignored so retries never inflate metrics.

## Features

- `POST /webhook` accepts LiteLLM proxy success webhooks (per-request payloads with `request` / `response` objects).
- Exact dollar cost calculated at ingest time and frozen into the row; later price edits never rewrite history.
- SQLite `models` table maps model glob patterns (`gpt-4o*`, `claude-3-5-*`, `*`) to input/output rates per million tokens; free/local models (`ollama/*`, `local/*`, …) are data-driven, not hardcoded.
- Edge cases handled cleanly:
  - missing or zero token counts (common on streaming without usage aggregation) are estimated with `tiktoken` and flagged,
  - tool-call payloads are not double-counted (arguments are billed inside `completion_tokens`),
  - streaming completions fall back to the estimator when `usage` is absent,
  - unknown models are stored at cost `0.0` with a logged warning — ingest never fails on pricing gaps.
- `request_id` UNIQUE constraint + `INSERT OR IGNORE` for idempotent duplicate handling.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in values
python seed_pricing.py      # populate the models pricing table
python -m app.main         # serves on $TOKEN_LEDGER_HOST:$TOKEN_LEDGER_PORT
```

LiteLLM has no built-in per-request success webhook (see spec §2.1), so delivery
runs through the included shim `token_ledger_callback.py` inside the proxy:

```yaml
litellm_settings:
  callbacks: [token_ledger_callback.token_ledger_handler]
```

With env vars on the proxy side: `TOKEN_LEDGER_URL=http://your-host:8000/webhook`
and `WEBHOOK_SHARED_SECRET` matching the ledger's.

## Endpoints

| Method | Path          | Purpose                                        |
| ------ | ------------- | ---------------------------------------------- |
| POST   | `/webhook`    | Ingest a LiteLLM success webhook               |
| GET    | `/health`     | Liveness check                                 |

## Storage

Events land in a SQLite `events` table:

```
id, request_id (UNIQUE), ts, model,
prompt_tokens, completion_tokens, latency_ms, calculated_cost, …
```

Full schema: the DDL in `app/db.py`.
