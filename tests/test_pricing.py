from app import db, pricing


def _conn(tmp_path):
    conn = db.get_conn(tmp_path / "p.db")
    db.init_db(conn)
    return conn


def _seed(conn, *rows):
    conn.executemany(
        "INSERT INTO models (pattern, input_per_mtok, output_per_mtok, is_free) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()


def test_most_specific_glob_wins(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("gpt-4o*", 2.5, 10.0, 0), ("gpt-4o-mini*", 0.15, 0.6, 0))
    assert pricing.resolve(c, "gpt-4o-mini")["pattern"] == "gpt-4o-mini*"


def test_exact_beats_glob(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("gpt-4o*", 2.5, 10.0, 0), ("gpt-4o", 1.0, 1.0, 0))
    assert pricing.resolve(c, "gpt-4o")["pattern"] == "gpt-4o"


def test_raw_pattern_matches_before_normalized(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("ollama/*", 0.0, 0.0, 1))
    row = pricing.resolve(c, "ollama/llama3")
    assert row is not None and row["pattern"] == "ollama/*"


def test_normalized_fallback(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("gpt-4o*", 2.5, 10.0, 0))
    assert pricing.resolve(c, "openai/gpt-4o")["pattern"] == "gpt-4o*"


def test_free_is_zero(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("ollama/*", 5.0, 5.0, 1))
    assert pricing.cost(1_000_000, 1_000_000, pricing.resolve(c, "ollama/llama3")) == 0.0


def test_unknown_returns_none(tmp_path):
    c = _conn(tmp_path)
    assert pricing.resolve(c, "zzz-model") is None


def test_glob_tie_is_deterministic(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("a*", 1.0, 1.0, 0), ("b*", 2.0, 2.0, 0))  # equal prefix length 'a' vs 'b'
    # both match 'ab'; lexicographic tie-break must pick one deterministically
    r1 = pricing.resolve(c, "ab")["pattern"]
    r2 = pricing.resolve(c, "ab")["pattern"]
    assert r1 == r2


def test_star_default(tmp_path):
    c = _conn(tmp_path)
    _seed(c, ("*", 0.5, 1.5, 0))
    assert pricing.resolve(c, "whatever")["pattern"] == "*"


def test_cost_formula():
    row = {"input_per_mtok": 2.5, "output_per_mtok": 10.0, "is_free": 0}
    assert pricing.cost(1_000_000, 1_000_000, row) == 12.5
    assert pricing.cost(0, 0, row) == 0.0


def test_normalize():
    assert pricing.normalize("openai/gpt-4o") == "gpt-4o"
    assert pricing.normalize("claude-3-5-sonnet") == "claude-3-5-sonnet"
