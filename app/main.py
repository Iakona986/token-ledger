import hmac
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request

from app import db, ingest

log = logging.getLogger("token-ledger")


def get_db(request: Request):
    return request.app.state.conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import build_config

    cfg = (
        build_config()
    )  # fresh read at boot so test env overrides take hold per client
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
    # cross-thread conn + _lock in insert_event; reads (pricing/health) are safe:
    # serialized by the same lock or sqlite's own reader path under WAL.
    # same_thread=False: sync endpoints run in AnyIO's worker pool (a different thread
    # than startup); all access is serialized through db._lock
    app.state.conn = db.get_conn(cfg.token_ledger_db, same_thread=False)
    app.state.secret = cfg.webhook_shared_secret
    app.state.allow_dev = cfg.allow_unauthenticated_dev
    db.init_db(app.state.conn)
    yield
    app.state.conn.close()


app = FastAPI(lifespan=lifespan)


def require_auth(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    # Fail closed: no secret configured is NOT a reason to accept anonymous writes.
    # An attacker who can reach an unconfigured instance could inject fake spend
    # rows (cost/usage fraud) or replay request_ids to poison the ledger.
    # Auth is disabled only by the explicit TOKEN_LEDGER_ALLOW_UNAUTHENTICATED_DEV flag.
    secret = getattr(request.app.state, "secret", None)
    if secret is None:
        if getattr(request.app.state, "allow_dev", False):
            return  # operator opted in to unauthenticated dev mode
        log.error("WEBHOOK_SHARED_SECRET unset: refusing request (503)")
        raise HTTPException(
            status_code=503,
            detail="authentication is not configured; set WEBHOOK_SHARED_SECRET "
            "(or TOKEN_LEDGER_ALLOW_UNAUTHENTICATED_DEV=1 for local dev only)",
        )
    if not hmac.compare_digest(
        (authorization or "").encode(), f"Bearer {secret}".encode()
    ):
        raise HTTPException(status_code=401)


@app.post("/webhook")
def webhook(
    payload: dict = Body(...), conn=Depends(get_db), _: None = Depends(require_auth)
):
    # FastAPI rejects non-object JSON with 422 before we run
    if not ingest.models.read_raw_model(payload) and not any(
        isinstance(payload.get(k), dict) for k in ("response", "request")
    ):
        raise HTTPException(
            status_code=422, detail="no usable model or request/response object"
        )

    try:
        event = ingest.build_spend_event(conn, payload)
    except Exception:
        # spec §3: never 5xx for pricing/token-count problems; store bare minimum.
        # uuid4: a constant id would dedup-collapse every later fallback row
        log.exception("ingest fallback for payload keys=%s", sorted(payload))
        event = {
            "request_id": f"fallback-{uuid.uuid4()}",
            "ts": ingest.models.read_ts_safe(payload),
            "model": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": None,
            "calculated_cost": 0.0,
            "is_estimated": 0,
            "is_tool_call": 0,
            "raw_model": payload.get("model"),
        }
    return {"status": db.insert_event(conn, event)}


@app.get("/health")
def health(conn=Depends(get_db)):
    try:
        # BEGIN IMMEDIATE + rollback proves the DB file is writable without writing (spec §3)
        with db._lock:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    from app.config import build_config

    cfg = build_config()

    uvicorn.run("app.main:app", host=cfg.token_ledger_host, port=cfg.token_ledger_port)
