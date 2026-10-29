import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(db_path, pricing_path):
    return subprocess.run(
        [
            sys.executable,
            "seed_pricing.py",
            "--db",
            str(db_path),
            "--pricing",
            str(pricing_path),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,  # tests assert on the seed script's exit code themselves
    )


def _make_db(tmp_path):
    from app import db

    conn = db.get_conn(tmp_path / "s.db")
    db.init_db(conn)
    conn.close()
    return tmp_path / "s.db"


def test_seed_is_idempotent(tmp_path):
    dbp = _make_db(tmp_path)
    yaml = tmp_path / "p.yaml"
    yaml.write_text(
        "models:\n  - pattern: 'gpt-4o*'\n    input_per_mtok: 2.50\n    output_per_mtok: 10.00\n"
    )
    assert _run(dbp, yaml).returncode == 0
    assert _run(dbp, yaml).returncode == 0
    conn = sqlite3.connect(dbp)
    assert conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 1


def test_rejects_non_suffix_glob(tmp_path):
    dbp = _make_db(tmp_path)
    yaml = tmp_path / "bad.yaml"
    yaml.write_text(
        "models:\n  - pattern: 'claude-*-3-5-*'\n    input_per_mtok: 1\n    output_per_mtok: 1\n"
    )
    r = _run(dbp, yaml)
    assert r.returncode != 0
    assert "claude-*-3-5-*" in r.stderr


def test_seeded_rows_match_yaml(tmp_path):
    dbp = _make_db(tmp_path)
    yaml = tmp_path / "p.yaml"
    yaml.write_text(
        "models:\n"
        "  - pattern: 'ollama/*'\n    is_free: true\n"
        "  - pattern: '*'\n    input_per_mtok: 0.0\n    output_per_mtok: 0.0\n"
    )
    assert _run(dbp, yaml).returncode == 0
    conn = sqlite3.connect(dbp)
    rows = dict(conn.execute("SELECT pattern, is_free FROM models"))
    assert rows == {"ollama/*": 1, "*": 0}


def test_repo_pricing_yaml_seeds_clean():
    # the committed starter pricing.yaml must itself pass validation + seed
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        dbp = Path(td) / "r.db"
        from app import db

        conn = db.get_conn(dbp)
        db.init_db(conn)
        conn.close()
        r = _run(dbp, REPO / "pricing.yaml")
        assert r.returncode == 0, r.stderr
