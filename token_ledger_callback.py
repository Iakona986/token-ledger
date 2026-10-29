"""Sender shim: runs inside the LiteLLM proxy, posts success events to token-ledger.

Litellm config.yaml:
    litellm_settings:
      callbacks: [token_ledger_callback.token_ledger_handler]

Env: TOKEN_LEDGER_URL (default http://localhost:8000/webhook),
     WEBHOOK_SHARED_SECRET must match the ledger's.
"""

import asyncio
import json
import logging
import os

import httpx
from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("token-ledger-shim")


class TokenLedger(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        payload = {
            "model": kwargs.get("model"),
            "messages": kwargs.get("messages"),
            "tools": kwargs.get("tools"),
            "litellm_call_id": kwargs.get("litellm_call_id"),
            "proxy_server_request": kwargs.get("proxy_server_request"),
            "response_ms": kwargs.get("response_ms"),
            # str() because ledger reads ISO strings; litellm may hand datetime objects,
            # and litellm versions drift on this — one integration test guards it
            "start_time": str(start_time),
            "end_time": str(end_time),
        }
        headers = {}
        secret = os.getenv("WEBHOOK_SHARED_SECRET")
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        url = os.getenv("TOKEN_LEDGER_URL", "http://localhost:8000/webhook")
        try:
            # shortcut: litellm's own response wrapper keeps drifting across versions;
            # upgrade when the ledger's integration test sees a real field loss
            payload["response"] = json.loads(response_obj.model_dump_json())
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json=payload, headers=headers)
        except Exception:
            # failure isolation: a dead ledger must never affect the LLM request
            log.warning("token-ledger delivery failed", exc_info=True)


token_ledger_handler = TokenLedger()
