import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    token_ledger_db: str
    token_ledger_host: str
    token_ledger_port: int
    webhook_shared_secret: str | None
    log_level: str


config = Config(
    token_ledger_db=os.getenv("TOKEN_LEDGER_DB", "token_ledger.db"),
    token_ledger_host=os.getenv("TOKEN_LEDGER_HOST", "127.0.0.1"),
    token_ledger_port=int(os.getenv("TOKEN_LEDGER_PORT", "8000")),
    webhook_shared_secret=os.getenv("WEBHOOK_SHARED_SECRET") or None,
    log_level=os.getenv("LOG_LEVEL", "INFO"),
)
