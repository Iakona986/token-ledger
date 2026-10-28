"""tiktoken fallback token estimation (spec §7.1)."""

import json

import tiktoken

from app import models


def _encoding() -> tiktoken.Encoding | None:
    for name in ("cl100k_base", "o200k_base"):
        try:
            return tiktoken.get_encoding(name)
        except Exception:  # ValueError, or network error fetching the BPE file offline
            continue
    return None


def _count(text: str, enc: tiktoken.Encoding | None) -> int:
    if enc is not None:
        return len(enc.encode(text))
    # offline fallback heuristic; rows it touches are flagged is_estimated upstream
    return len(text) // 4 if text else 0


def estimate_prompt(messages: list, tools: object) -> int:
    if not messages and not tools:
        return 0
    text = json.dumps({"messages": messages, "tools": tools}, sort_keys=True, default=str)
    return _count(text, _encoding())


def estimate_completion(choices: list) -> int:
    # single text-extraction rule lives in models._completion_text (shared with the dedup hash)
    return _count(models._completion_text(choices), _encoding())
