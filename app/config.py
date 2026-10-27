import os
from dataclasses import dataclass

from dotenv import dotenv_values, load_dotenv

_ENV_KEYS = (
    "TOKEN_LEDGER_DB",
    "TOKEN_LEDGER_HOST",
    "TOKEN_LEDGER_PORT",
    "WEBHOOK_SHARED_SECRET",
    "LOG_LEVEL",
)


@dataclass(frozen=True)
class Config:
    token_ledger_db: str
    token_ledger_host: str
    token_ledger_port: int
    webhook_shared_secret: str | None
    log_level: str


# load_dotenv injects .env values into os.environ and never overwrites, so remember which
# of these keys were ALREADY set by the caller before injection — those stay authoritative
_ORIG_ENV = {key: os.environ.get(key) for key in _ENV_KEYS}

load_dotenv(override=False)


def live_env() -> dict[str, str]:
    """Effective env now: caller-set values win over .env; a monkeypatched (or deleted)
    key is honored even if a previous load_dotenv injected the .env value into os.environ."""
    file_values = dotenv_values()  # the .env file load_dotenv just read
    env: dict[str, str] = {}
    for key in _ENV_KEYS:
        current = os.environ.get(key)
        if current is not None and (current != file_values.get(key) or _ORIG_ENV[key] is not None):
            env[key] = current  # set by the caller (real env or monkeypatch), wins over .env
        elif key not in file_values:
            # .env does not define it: read os.environ fresh so a plain (non-.env) var that
            # was deleted from this process can simply be treated as unset
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        elif _ORIG_ENV[key] is not None:
            env[key] = _ORIG_ENV[key]  # real env had it; deletion falls back to that, not .env
    return env


def build_config(env: dict[str, str | None]) -> Config:
    g = env.get
    return Config(
        token_ledger_db=g("TOKEN_LEDGER_DB") or "token_ledger.db",
        token_ledger_host=g("TOKEN_LEDGER_HOST") or "127.0.0.1",
        token_ledger_port=int(g("TOKEN_LEDGER_PORT") or "8000"),
        webhook_shared_secret=g("WEBHOOK_SHARED_SECRET") or None,
        log_level=g("LOG_LEVEL") or "INFO",
    )


config = build_config(live_env())
