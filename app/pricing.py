"""models-table lookup + cost formula (spec §6)."""

import fnmatch
import sqlite3

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


def resolve(conn: sqlite3.Connection, raw_model: str) -> tuple | None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT pattern, input_per_mtok, output_per_mtok, is_free FROM models"
    ).fetchall()
    # audit fix: try the raw string first (so seeded 'ollama/*' matches), then normalized
    for candidate in (raw_model, normalize(raw_model)):
        exact = next((r for r in rows if r["pattern"] == candidate), None)
        if exact:
            return exact
        # globs by longest literal prefix; ties broken lexicographically
        globs = sorted(
            (r for r in rows if "*" in r["pattern"] and fnmatch.fnmatchcase(candidate, r["pattern"])),
            key=lambda r: (-_prefix_len(r["pattern"]), r["pattern"]),
        )
        if globs:
            return globs[0]
    return None


def cost(prompt_tokens: int, completion_tokens: int, row) -> float:
    if row["is_free"]:
        return 0.0
    return (
        prompt_tokens / 1_000_000 * row["input_per_mtok"]
        + completion_tokens / 1_000_000 * row["output_per_mtok"]
    )
