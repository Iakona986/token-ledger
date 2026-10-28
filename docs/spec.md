# token-ledger — Technical Specification

Version: 0.1 (pre-implementation contract)
Status: authoritative; code must follow this unless the spec is amended here first.

## 1. Purpose

token-ledger receives per-request success webhooks from a LiteLLM proxy,
normalizes each payload into a `SpendEvent`, prices it against a local rate
table, and stores it exactly once in a SQLite ledger. It is a single small
FastAPI service; no queues, no external dependencies beyond SQLite.

## 2. Stack

| Concern        | Choice                                             |
| -------------- | -------------------------------------------------- |
| Language       | Python 3.14+                                       |
| Web framework  | FastAPI + uvicorn                                  |
| Validation     | pydantic v2                                        |
| Storage        | SQLite via stdlib `sqlite3` (WAL mode)             |
| Token estimate | tiktoken                                           |
| Config         | `.env` (loaded with python-dotenv)                 |

## 3. HTTP surface

### `POST /webhook`

- Accepts a JSON LiteLLM **proxy success webhook** payload (per-request;
  contains `request`, `response`, `proxy_server_request` / call-tracking keys).
- Auth: when `WEBHOOK_SHARED_SECRET` is set, require header
  `Authorization: Bearer <secret>`; mismatch → `401`. Unset → no auth (dev only).
- Responses:
  - `200 {"status": "inserted"}` — new event stored.
  - `200 {"status": "duplicate"}` — same `request_id` already stored; **no
    re-insert**, still 200 so LiteLLM does not retry-storm.
  - `422` — body is not valid JSON or lacks both a usable model and any usable
    request/response object.
  - Ingest must **never** 5xx for pricing or token-count problems (see §7).

### `GET /health`

`200 {"status": "ok"}`; also verifies the DB file is writable.

## 4. SQLite schema

Database path from `TOKEN_LEDGER_DB` (default `token_ledger.db`). Created on
startup; migrations are additive `ALTER TABLE` guarded by
`PRAGMA user_version`.

```sql
CREATE TABLE IF NOT EXISTS events (
  id                INTEGER PRIMARY KEY,
  request_id        TEXT    NOT NULL UNIQUE,
  ts                TEXT    NOT NULL,           -- ISO-8601 UTC, event time
  model             TEXT    NOT NULL,           -- resolved pricing model name
  prompt_tokens     INTEGER NOT NULL,           -- never NULL; 0 only if truly 0
  completion_tokens INTEGER NOT NULL,
  latency_ms        INTEGER,                    -- NULL when unknown
  calculated_cost   REAL    NOT NULL,           -- USD, frozen at ingest
  is_estimated      INTEGER NOT NULL DEFAULT 0, -- 1 = tokens estimated (§7.1)
  is_tool_call      INTEGER NOT NULL DEFAULT 0, -- 1 = completion had tool_calls
  raw_model         TEXT                        -- original model string
);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_model ON events (model);
```

```sql
CREATE TABLE IF NOT EXISTS models (
  id              INTEGER PRIMARY KEY,
  pattern         TEXT    NOT NULL UNIQUE,  -- glob: 'gpt-4o*', 'claude-3-5-*', '*'
  input_per_mtok  REAL    NOT NULL,         -- USD per 1M prompt tokens
  output_per_mtok REAL    NOT NULL,         -- USD per 1M completion tokens
  is_free         INTEGER NOT NULL DEFAULT 0
);
```

`request_id` UNIQUE is the dedup guarantee. Writes use
`INSERT OR IGNORE`; affected-rows tells inserted vs duplicate.

## 5. Payload → SpendEvent mapping

Input is LiteLLM's success-webhook JSON. Field access is defensive: every read
is `obj.get(...)`, arrays are checked by key presence, and unknown extra keys are
ignored.

### `request_id` (dedup key) — priority order

1. `response.id` (e.g. `chatcmpl-abc123`)
2. `litellm_call_id`
3. SHA-256 of the canonicalized JSON payload (fallback so the row is still
   dedup-stable across redeliveries)

### Other fields

| SpendEvent field    | Source (first present wins)                                      |
| ------------------- | ---------------------------------------------------------------- |
| `ts`                | `response.created` (unix → ISO UTC) → `end_time` → ingest time    |
| `model`             | `model` / `proxy_server_request.model` (normalized, see §6)       |
| `prompt_tokens`     | `response.usage.prompt_tokens` → `usage.prompt_tokens` → estimate |
| `completion_tokens` | `response.usage.completion_tokens` → `usage.completion_tokens` → estimate |
| `latency_ms`        | `response_ms` → `ms_since_first_proxy_request` → `end_time - start_time` → NULL |
| `is_tool_call`      | 1 if `response.choices[*].message.tool_calls` non-empty           |
| `raw_model`         | the literal model string as received                              |

### Response-shape flexibility

`response` may be a full completion object (with `choices`/`usage`) or a usage
dict depending on LiteLLM version. Both are accepted; extraction tries the
completion shape first, then a bare `usage` mapping.

## 6. Pricing

### Resolution order for a model string

1. Normalize: strip LiteLLM provider prefixes (`openai/`, `ollama/`, `anthropic/`,
   `hosted_vllm/`, …) for matching, but keep `raw_model`.
2. Look up the `models` table: exact `pattern` match first, then glob matches in
   order of **longest literal prefix** (most specific wins), then the `*` default.
3. If no row matches at all → **unknown model**: store the event with
   `calculated_cost = 0.0` and log a WARNING naming the model. Ingest does not
   fail.

### Cost formula (computed at ingest, frozen in the row)

```
cost = prompt_tokens / 1_000_000 * input_per_mtok
     + completion_tokens / 1_000_000 * output_per_mtok
```

- `is_free = 1` rows (seeded for `ollama/*`, `local/*`, `huggingface/*`, …)
  short-circuit to exactly `0.0`.
- Costs are never recomputed on read; history is immutable.
- `models` is seeded by `seed_pricing.py`, which reads an optional
  `pricing.yaml` at seed time. **The table is the runtime source of truth.**

`pricing.yaml` seed format:

```yaml
models:
  - pattern: "gpt-4o*"
    input_per_mtok: 2.50
    output_per_mtok: 10.00
  - pattern: "ollama/*"
    is_free: true
  - pattern: "*"
    input_per_mtok: 0.0
    output_per_mtok: 0.0
```

## 7. Edge-case rules

### 7.1 Missing or zero token counts

Common on streaming calls where LiteLLM did not aggregate `usage`.

- If prompt count missing → estimate by tiktoken-encoding the serialized
  `messages` (+ `tools` JSON if present) with the closest available encoding
  (`cl100k_base` fallback).
- If completion count missing → estimate by tiktoken-encoding response text
  (concatenated `choices[*].message.content` + tool-call arguments).
- Any estimated row gets `is_estimated = 1` and is still priced normally.
- If both estimate to 0 (empty call), store zeros honestly.

### 7.2 Streaming completions

Streaming payloads often lack `usage` entirely. No special path beyond §7.1;
the estimator is the streaming fallback by design.

### 7.3 Tool calls

Tool-call arguments are billed inside `completion_tokens` by OpenAI-style APIs,
so they are **not** counted separately. The event is only flagged
`is_tool_call = 1` for querying. If the completion is tool-calls-only (empty
`content`), the estimator (§7.1) serializes the tool-call arguments as the
completion text.

### 7.4 Zero-cost local vs paid cloud

Decided by data: the matched `models` row (`is_free` or rates of 0). No
hardcoded provider checks in the ingest path.

### 7.5 Duplicate deliveries

`INSERT OR IGNORE` on the UNIQUE `request_id`. First delivery wins; later
identical deliveries return `200 {"status": "duplicate"}` and metrics stay flat.

### 7.6 Unknown model

Store at cost `0.0`, log a WARNING (§6). Never reject.

### 7.7 Clock/timezone

All timestamps stored ISO-8601 UTC. `response.created` (seconds, UTC) is
preferred over proxy wall-clock keys.

## 8. Configuration (`.env`)

| Key                    | Default           | Purpose                                  |
| ---------------------- | ----------------- | ---------------------------------------- |
| `TOKEN_LEDGER_DB`      | `token_ledger.db` | SQLite file path                          |
| `TOKEN_LEDGER_HOST`    | `127.0.0.1`       | Bind host (uvicorn)                       |
| `TOKEN_LEDGER_PORT`    | `8000`            | Bind port (uvicorn)                       |
| `WEBHOOK_SHARED_SECRET`| *(unset)*         | When set, `/webhook` requires `Authorization: Bearer …` |
| `LOG_LEVEL`            | `INFO`            | Python log level                          |

`.env` is gitignored; `.env.example` (committed) documents the keys.

## 9. Module layout

```
token-ledger/
  app/
    main.py          # FastAPI app: /webhook, /health, startup (schema+migrate)
    models.py        # pydantic: SpendEvent + defensive payload readers
    ingest.py        # payload dict -> SpendEvent (field mapping, §5)
    pricing.py       # models-table lookup + cost formula (§6)
    estimate.py      # tiktoken fallback estimation (§7.1)
    db.py            # connection, schema DDL, user_version migrations, insert
  seed_pricing.py    # pricing.yaml -> models table (idempotent upsert)
  pricing.yaml       # seed data only, not read at runtime
  docs/spec.md       # this file
  tests/             # pytest + FastAPI TestClient
```

## 10. Acceptance tests (minimum)

1. Happy path: full payload with `usage` → inserted, exact cost math.
2. Duplicate: same `request_id` twice → second is `{"status":"duplicate"}`,
   one row.
3. Streaming: payload with no `usage` → `is_estimated = 1`, cost > 0.
4. Tool call: `tool_calls`-only completion → `is_tool_call = 1`, no double count.
5. Local model: `ollama/llama3` → `calculated_cost == 0.0`.
6. Unknown model: no matching pattern, no `*` row → stored, cost 0.0, warning logged.
7. Auth: wrong/missing bearer secret → 401 when secret configured.
8. Most-specific pattern wins: `gpt-4o-mini` matches `gpt-4o-mini*` over `gpt-4o*`.
