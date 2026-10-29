"""models-table lookup + cost formula (spec §6)."""

import fnmatch
import sqlite3

from app import db

PROVIDER_PREFIXES = ("openai/", "anthropic/", "ollama/", "hosted_vllm/", "gemini/", "bedrock/")


def normalize(model: str) -> str:
    for p in PROVIDER_PREFIXES:
        if model.startswith(p):
            return model[len(p):]
    # any other single-segment prefix like "azure/..." — strip only known ones above;
    # generic fallback: LiteLLM uses "<provider>/<name>", so strip first segment if it looks like one
    if "/" in model:
        head, tail = model.split("/", 1)
        if head.isidentifier() and tail:
            return tail
    return model


def _prefix_len(pattern: str) -> int:
    return pattern.find("*") if "*" in pattern else len(pattern) + 1  # exact sorts after globs


_models_cache = None


def clear_cache():
    global _models_cache
    with db._lock:
        _models_cache = None


def resolve(conn: sqlite3.Connection, raw_model: str) -> tuple | None:
    global _models_cache
    with db._lock:
        if _models_cache is None:
            _models_cache = conn.execute(
                "SELECT pattern, input_per_mtok, output_per_mtok, is_free FROM models"
            ).fetchall()
        rows = _models_cache
    # best match across BOTH candidates (raw and normalized): exact > longest literal
    # prefix > '*' catch-all last; ties lexicographic, raw candidate wins ties. Reviewer
    # fix: a '*' row must not shadow the normalized match for prefixed models.
    best = None  # max by (is_exact, prefix_len, raw-candidate-preferred, lexicographic-first)
    for cand_rank, candidate in enumerate((raw_model, normalize(raw_model))):
        for r in rows:
            pattern = r["pattern"]
            exact = pattern == candidate
            if not exact and not ("*" in pattern and fnmatch.fnmatchcase(candidate, pattern)):
                continue
            key = (int(exact), _prefix_len(pattern), -cand_rank, tuple(-ord(c) for c in pattern))
            if best is None or key > best[0]:
                best = (key, r)
    # '*' sorts last by prefix_len 0; tie between raw and normalized goes to raw
    return best[1] if best else None


def cost(prompt_tokens: int, completion_tokens: int, row) -> float:
    if row["is_free"]:
        return 0.0
    return (
        prompt_tokens / 1_000_000 * row["input_per_mtok"]
        + completion_tokens / 1_000_000 * row["output_per_mtok"]
    )
