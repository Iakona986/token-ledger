import os
from dataclasses import dataclass

from dotenv import dotenv_values

_ENV_KEYS = (
    "TOKEN_LEDGER_DB",
    "TOKEN_LEDGER_HOST",
    "TOKEN_LEDGER_PORT",
    "WEBHOOK_SHARED_SECRET",
    "LOG_LEVEL",
)

# cwd-relative at call time: python-dotenv's find_dotenv walks up from this module's
# file, which would load the project .env even when tests chdir to a temp dir
def _dotenv_path() -> str:
    return os.path.join(os.getcwd(), ".env")


@dataclass(frozen=True)
class Config:
    token_ledger_db: str
    token_ledger_host: str
    token_ledger_port: int
    webhook_shared_secret: str | None
    log_level: str


def build_config() -> Config:
    """Merge view of spec §8 keys: real environment wins over the .env file."""
    env = {k: v for k, v in dotenv_values(_dotenv_path()).items() if v is not None}
    for key in _ENV_KEYS:
        current = os.environ.get(key)
        if current is not None:
            env[key] = current  # real env wins over the file by construction
    g = env.get
    return Config(
        token_ledger_db=g("TOKEN_LEDGER_DB") or "token_ledger.db",
        token_ledger_host=g("TOKEN_LEDGER_HOST") or "127.0.0.1",
        token_ledger_port=int(g("TOKEN_LEDGER_PORT") or "8000"),
        webhook_shared_secret=g("WEBHOOK_SHARED_SECRET") or None,
        log_level=g("LOG_LEVEL") or "INFO",
    )
