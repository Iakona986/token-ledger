#!/usr/bin/env python3
"""Seed the models pricing table from pricing.yaml (idempotent upsert). Spec §6."""

import argparse
import re
import sys

import yaml

from app import db

SUFFIX_GLOB = re.compile(r"^[^*]*\*$|^[^*]+$")  # exact or prefix* — anything else is ambiguous


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed pricing table from YAML")
    ap.add_argument("--db", default=None, help="SQLite path (default: TOKEN_LEDGER_DB / token_ledger.db)")
    ap.add_argument("--pricing", default="pricing.yaml", help="YAML seed file")
    args = ap.parse_args()

    with open(args.pricing) as f:
        data = yaml.safe_load(f) or {}

    rows = []
    for entry in data.get("models", []):
        pattern = entry["pattern"]
        if not SUFFIX_GLOB.match(pattern):
            print(
                f"error: pattern {pattern!r} is not a suffix glob (use 'exact' or 'prefix*')",
                file=sys.stderr,
            )
            return 2
        rows.append(
            (
                pattern,
                float(entry.get("input_per_mtok", 0.0)),
                float(entry.get("output_per_mtok", 0.0)),
                int(bool(entry.get("is_free", False))),
            )
        )

    from app.config import build_config, live_env

    db_path = args.db or build_config(live_env()).token_ledger_db
    conn = db.get_conn(db_path)
    db.init_db(conn)
    conn.executemany(
        """INSERT INTO models (pattern, input_per_mtok, output_per_mtok, is_free)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(pattern) DO UPDATE SET
             input_per_mtok = excluded.input_per_mtok,
             output_per_mtok = excluded.output_per_mtok,
             is_free = excluded.is_free""",
        rows,
    )
    conn.commit()
    print(f"seeded {len(rows)} pricing rows into {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
