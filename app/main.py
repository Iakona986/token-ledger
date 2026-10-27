import logging

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request

from app import db, ingest

log = logging.getLogger("token-ledger")


def get_db(request: Request):
    return request.app.state.conn


app = FastAPI()


@app.on_event("startup")
def startup() -> None:
    from app.config import build_config, live_env

    cfg = build_config(live_env())  # fresh read so test env overrides take hold per client
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
    app.state.conn = db.get_conn(cfg.token_ledger_db)
    app.state.secret = cfg.webhook_shared_secret
    db.init_db(app.state.conn)


def require_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    # state.secret set by startup from the live config at boot time
    secret = getattr(request.app.state, "secret", None)
    if secret is None:
        return  # dev mode: no auth configured
    if authorization != f"Bearer {secret}":
        raise HTTPException(status_code=401)


@app.post("/webhook")
def webhook(payload: dict = Body(...), conn=Depends(get_db), _: None = Depends(require_auth)):
    # FastAPI rejects non-object JSON with 422 before we run; sync def keeps sqlite3 on one thread
    has_obj = any(isinstance(payload.get(k), dict) for k in ("response", "request"))
    if not payload.get("model") and not has_obj:
        raise HTTPException(status_code=422, detail="no usable model or request/response object")

    try:
        event = ingest.build_spend_event(conn, payload)
    except Exception:
        # spec §3: never 5xx for pricing/token-count problems; store bare minimum
        log.exception("ingest fallback for payload keys=%s", sorted(payload))
        event = {
            "request_id": str(payload.get("litellm_call_id") or payload.get("response", {}).get("id") or "unknown"),
            "ts": ingest.models.read_ts(payload),
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
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
