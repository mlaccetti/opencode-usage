"""Tests for opencode_usage.queries.

Builds a small fixture sqlite DB matching the relevant columns of the real
opencode.db schema (session, project, message) and exercises the pure
aggregation functions in queries.py.

Run: python3 -m pytest scripts/opencode-usage/tests/ -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from opencode_usage import queries  # noqa: E402


SCHEMA = """
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    worktree TEXT NOT NULL,
    name TEXT
);

CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_id TEXT,
    slug TEXT,
    directory TEXT,
    title TEXT,
    cost REAL DEFAULT 0 NOT NULL,
    tokens_input INTEGER DEFAULT 0 NOT NULL,
    tokens_output INTEGER DEFAULT 0 NOT NULL,
    tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
    agent TEXT,
    model TEXT,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);

CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


def _local_millis(y, m, d, hh=12, mm=0):
    """Build epoch-millis for a naive local datetime, matching how
    queries.py interprets time_created (local time)."""
    dt = datetime(y, m, d, hh, mm)
    return int(dt.astimezone().timestamp() * 1000)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)

    conn.execute(
        "INSERT INTO project (id, worktree, name) VALUES (?, ?, ?)",
        ("proj-1", "/Users/michael/workspace/gorgias-ai-marketplace", "gorgias-ai-marketplace"),
    )
    conn.execute(
        "INSERT INTO project (id, worktree, name) VALUES (?, ?, ?)",
        ("proj-2", "/Users/michael/workspace/gorgias", "gorgias"),
    )

    sessions = [
        # id, project_id, parent_id, slug, directory, title, cost, in, out, reasoning, cache_read, cache_write, agent, model, created, updated
        (
            "sess-1", "proj-1", None, "s1", "/dir1", "top-level opus session",
            1.5, 1000, 500, 50, 200, 20, "build",
            json.dumps({"id": "claude-opus-4-8", "providerID": "bf-a", "variant": "high"}),
            _local_millis(2026, 8, 10), _local_millis(2026, 8, 10),
        ),
        (
            "sess-2", "proj-1", "sess-1", "s2", "/dir1", "sub-agent session",
            0.5, 400, 200, 10, 50, 5, "explore",
            json.dumps({"id": "claude-sonnet-5", "providerID": "bf-a"}),
            _local_millis(2026, 8, 11), _local_millis(2026, 8, 11),
        ),
        (
            "sess-3", "proj-2", None, "s3", "/dir2", "free local model session",
            0.0, 5000, 3000, 0, 0, 0, "build",
            json.dumps({"id": "local-llama", "providerID": "ollama"}),
            _local_millis(2026, 8, 12), _local_millis(2026, 8, 12),
        ),
        (
            "sess-4", "proj-2", None, "s4", "/dir2", "out of window session",
            9.0, 9000, 9000, 900, 0, 0, "build",
            json.dumps({"id": "claude-opus-4-8", "providerID": "bf-a"}),
            _local_millis(2026, 7, 1), _local_millis(2026, 7, 1),
        ),
        (
            "sess-5", None, None, "s5", "/dir3", "no project session",
            0.2, 100, 100, 0, 0, 0, "build",
            json.dumps({"id": "claude-opus-4-8", "providerID": "bf-a"}),
            _local_millis(2026, 8, 13), _local_millis(2026, 8, 13),
        ),
        (
            # phantom session: Orca/automation spawned it but no real turn
            # ever ran, so tokens are zero even though cost is also zero.
            "sess-6", "proj-1", None, "s6", "/dir1", "New session 2026-08-13",
            0.0, 0, 0, 0, 0, 0, "build",
            json.dumps({"id": "deepseek-v4-flash-free", "providerID": "deepseek"}),
            _local_millis(2026, 8, 13), _local_millis(2026, 8, 13),
        ),
        (
            # empty child of an explicitly-requested parent session; must
            # still show up in that parent's session_detail children list.
            "sess-7", "proj-1", "sess-1", "s7", "/dir1", "New session 2026-08-13",
            0.0, 0, 0, 0, 0, 0, "explore",
            json.dumps({"id": "deepseek-v4-flash-free", "providerID": "deepseek"}),
            _local_millis(2026, 8, 13), _local_millis(2026, 8, 13),
        ),
    ]
    conn.executemany(
        """
        INSERT INTO session (
            id, project_id, parent_id, slug, directory, title,
            cost, tokens_input, tokens_output, tokens_reasoning,
            tokens_cache_read, tokens_cache_write, agent, model,
            time_created, time_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        sessions,
    )

    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (
            "msg-1",
            "sess-1",
            _local_millis(2026, 8, 10),
            _local_millis(2026, 8, 10),
            json.dumps({"role": "assistant", "cost": 1.5, "tokens": {"input": 1000, "output": 500}}),
        ),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (
            "msg-2",
            "sess-2",
            _local_millis(2026, 8, 11),
            _local_millis(2026, 8, 11),
            json.dumps({"role": "assistant", "cost": 0.5, "tokens": {"input": 400, "output": 200}}),
        ),
    )

    conn.commit()
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    connection = queries.connect_readonly(db_path)
    yield connection
    connection.close()


# --------------------------------------------------------------------------
# cost_by_model
# --------------------------------------------------------------------------


def test_cost_by_model_parses_json_and_aggregates(conn):
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31")
    by_model = {(r["model_id"], r["provider"]): r for r in rows}

    assert ("claude-opus-4-8", "bf-a") in by_model
    opus = by_model[("claude-opus-4-8", "bf-a")]
    # sess-1 (1.5) + sess-5 (0.2), sess-4 excluded (out of window)
    assert opus["sessions"] == 2
    assert opus["cost"] == pytest.approx(1.7)
    assert opus["tokens_input"] == 1100
    assert opus["tokens_output"] == 600


def test_cost_by_model_free_provider_shows_tokens_with_zero_cost(conn):
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31")
    local = next(r for r in rows if r["model_id"] == "local-llama")
    assert local["cost"] == 0.0
    assert local["tokens_input"] == 5000
    assert local["tokens_output"] == 3000


def test_cost_by_model_group_by_day_adds_bucket(conn):
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31", group_by="day")
    assert all("bucket" in r for r in rows)
    buckets = {r["bucket"] for r in rows}
    assert "2026-08-10" in buckets
    assert "2026-08-11" in buckets


def test_cost_by_model_group_by_week_adds_bucket(conn):
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31", group_by="week")
    assert all("bucket" in r for r in rows)


# --------------------------------------------------------------------------
# date window filtering
# --------------------------------------------------------------------------


def test_since_until_excludes_out_of_window_rows(conn):
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31")
    total_cost = sum(r["cost"] for r in rows)
    # sess-4's 9.0 cost (2026-07-01) must be excluded
    assert total_cost == pytest.approx(1.5 + 0.5 + 0.0 + 0.2)


def test_no_window_includes_all_rows(conn):
    rows = queries.cost_by_model(conn, since=None, until=None)
    total_cost = sum(r["cost"] for r in rows)
    assert total_cost == pytest.approx(1.5 + 0.5 + 0.0 + 9.0 + 0.2)


# --------------------------------------------------------------------------
# agent_breakdown / sub-agent split
# --------------------------------------------------------------------------


def test_agent_breakdown_splits_top_level_and_subagent(conn):
    result = queries.agent_breakdown(conn, since="2026-08-01", until="2026-08-31")
    agents_by_name = {a["agent"]: a for a in result["agents"]}

    build = agents_by_name["build"]
    explore = agents_by_name["explore"]

    assert build["is_subagent"] is False
    assert explore["is_subagent"] is True

    assert result["totals"]["top_level"]["sessions"] == 3  # sess-1, sess-3, sess-5
    assert result["totals"]["subagent"]["sessions"] == 1  # sess-2
    assert result["totals"]["subagent"]["cost"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# cost_by_project
# --------------------------------------------------------------------------


def test_cost_by_project_maps_worktree_to_basename(conn):
    rows = queries.cost_by_project(conn, since="2026-08-01", until="2026-08-31")
    names = {r["display_name"]: r for r in rows}

    assert "gorgias-ai-marketplace" in names
    assert "gorgias" in names

    marketplace = names["gorgias-ai-marketplace"]
    assert marketplace["sessions"] == 2  # sess-1, sess-2
    assert marketplace["cost"] == pytest.approx(2.0)


def test_cost_by_project_falls_back_for_null_project(conn):
    rows = queries.cost_by_project(conn, since="2026-08-01", until="2026-08-31")
    names = {r["display_name"] for r in rows}
    assert "global" in names or any("sess-5" for _ in [None])
    fallback = next(r for r in rows if r["display_name"] == "global")
    assert fallback["sessions"] == 1
    assert fallback["cost"] == pytest.approx(0.2)


# --------------------------------------------------------------------------
# session_detail
# --------------------------------------------------------------------------


def test_session_detail_includes_children_and_messages(conn):
    detail = queries.session_detail(conn, "sess-1")

    assert detail["session"]["id"] == "sess-1"
    assert detail["session"]["model_id"] == "claude-opus-4-8"
    assert detail["session"]["provider"] == "bf-a"
    assert detail["session"]["cost"] == pytest.approx(1.5)

    child_ids = {c["id"] for c in detail["children"]}
    assert child_ids == {"sess-2", "sess-7"}

    assert detail["message_count"] == 1


def test_session_detail_unknown_session_returns_none(conn):
    assert queries.session_detail(conn, "does-not-exist") is None


# --------------------------------------------------------------------------
# efficiency_by_model
# --------------------------------------------------------------------------


def test_efficiency_by_model_computes_ratios(conn):
    rows = queries.efficiency_by_model(conn, since="2026-08-01", until="2026-08-31")
    opus = next(r for r in rows if r["model_id"] == "claude-opus-4-8")

    # sess-1 (input=1000, cache_read=200) + sess-5 (input=100, cache_read=0)
    expected_cache_ratio = (200 + 0) / (1000 + 100 + 200 + 0)
    assert opus["cache_read_ratio"] == pytest.approx(expected_cache_ratio)

    # reasoning=50, output=600 -> 50/600
    assert opus["reasoning_share"] == pytest.approx(50 / 600)

    assert opus["output_input_ratio"] == pytest.approx(600 / 1100)


def test_efficiency_by_model_guards_divide_by_zero(conn):
    rows = queries.efficiency_by_model(conn, since="2026-08-01", until="2026-08-31")
    local = next(r for r in rows if r["model_id"] == "local-llama")

    # local-llama has zero cache_read+input=5000, output=3000 but reasoning=0
    assert local["reasoning_share"] == 0.0
    # a model with zero input/cache_read entirely would need ratio 0, but
    # here input is nonzero; assert no exception path is used generically:
    assert isinstance(local["cache_read_ratio"], float)
    assert isinstance(local["output_input_ratio"], float)


def test_efficiency_by_model_zero_input_and_cache_does_not_raise(conn):
    # Directly exercise the ratio helper for the true zero/zero case.
    assert queries._safe_ratio(0, 0) == 0.0
    assert queries._safe_ratio(5, 0) == 0.0


# --------------------------------------------------------------------------
# sessions_by_model
# --------------------------------------------------------------------------


def test_sessions_by_model_counts_per_model(conn):
    rows = queries.sessions_by_model(conn, since="2026-08-01", until="2026-08-31")
    by_model = {(r["model_id"], r["provider"]): r["sessions"] for r in rows}

    # sess-1 + sess-5, sess-4 excluded (out of window)
    assert by_model[("claude-opus-4-8", "bf-a")] == 2
    assert by_model[("claude-sonnet-5", "bf-a")] == 1
    assert by_model[("local-llama", "ollama")] == 1


def test_sessions_by_model_sorted_desc(conn):
    rows = queries.sessions_by_model(conn, since="2026-08-01", until="2026-08-31")
    sessions_counts = [r["sessions"] for r in rows]
    assert sessions_counts == sorted(sessions_counts, reverse=True)


def test_sessions_by_model_excludes_out_of_window_rows(conn):
    rows = queries.sessions_by_model(conn, since="2026-08-01", until="2026-08-31")
    total_sessions = sum(r["sessions"] for r in rows)
    # sess-4 (2026-07-01) excluded; sess-1,2,3,5 included = 4
    assert total_sessions == 4


def test_sessions_by_model_no_window_includes_all_rows(conn):
    rows = queries.sessions_by_model(conn, since=None, until=None)
    total_sessions = sum(r["sessions"] for r in rows)
    # 7 total rows minus sess-6 and sess-7 (empty, excluded by default)
    assert total_sessions == 5


# --------------------------------------------------------------------------
# empty (phantom, zero-token) sessions excluded by default
# --------------------------------------------------------------------------


def test_cost_by_model_excludes_empty_sessions_by_default(conn):
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31")
    model_ids = {r["model_id"] for r in rows}
    assert "deepseek-v4-flash-free" not in model_ids


def test_cost_by_model_includes_empty_sessions_when_requested(conn):
    rows = queries.cost_by_model(
        conn, since="2026-08-01", until="2026-08-31", include_empty=True
    )
    by_model = {r["model_id"]: r for r in rows}
    assert "deepseek-v4-flash-free" in by_model
    # sess-6 (top-level) + sess-7 (child)
    assert by_model["deepseek-v4-flash-free"]["sessions"] == 2


def test_cost_by_model_keeps_zero_cost_nonzero_token_sessions_by_default(conn):
    # Regression guard: free providers with real usage (nonzero tokens,
    # zero cost) must never be treated as empty/phantom sessions.
    rows = queries.cost_by_model(conn, since="2026-08-01", until="2026-08-31")
    local = next(r for r in rows if r["model_id"] == "local-llama")
    assert local["cost"] == 0.0
    assert local["tokens_input"] == 5000
    assert local["tokens_output"] == 3000


def test_sessions_by_model_excludes_empty_sessions_by_default(conn):
    rows = queries.sessions_by_model(conn, since="2026-08-01", until="2026-08-31")
    model_ids = {r["model_id"] for r in rows}
    assert "deepseek-v4-flash-free" not in model_ids


def test_sessions_by_model_includes_empty_sessions_when_requested(conn):
    rows = queries.sessions_by_model(
        conn, since="2026-08-01", until="2026-08-31", include_empty=True
    )
    model_ids = {r["model_id"] for r in rows}
    assert "deepseek-v4-flash-free" in model_ids


def test_agent_breakdown_excludes_empty_sessions_by_default(conn):
    result = queries.agent_breakdown(conn, since="2026-08-01", until="2026-08-31")
    # sess-6 (build, top-level, empty) and sess-7 (explore, subagent, empty)
    # excluded: top-level stays 3 (sess-1, sess-3, sess-5), subagent stays 1 (sess-2)
    assert result["totals"]["top_level"]["sessions"] == 3
    assert result["totals"]["subagent"]["sessions"] == 1


def test_agent_breakdown_includes_empty_sessions_when_requested(conn):
    result = queries.agent_breakdown(
        conn, since="2026-08-01", until="2026-08-31", include_empty=True
    )
    assert result["totals"]["top_level"]["sessions"] == 4  # + sess-6
    assert result["totals"]["subagent"]["sessions"] == 2  # + sess-7


def test_cost_by_project_excludes_empty_sessions_by_default(conn):
    rows = queries.cost_by_project(conn, since="2026-08-01", until="2026-08-31")
    names = {r["display_name"]: r for r in rows}
    marketplace = names["gorgias-ai-marketplace"]
    # still sess-1, sess-2 only; sess-6, sess-7 (empty) excluded
    assert marketplace["sessions"] == 2


def test_cost_by_project_includes_empty_sessions_when_requested(conn):
    rows = queries.cost_by_project(
        conn, since="2026-08-01", until="2026-08-31", include_empty=True
    )
    names = {r["display_name"]: r for r in rows}
    marketplace = names["gorgias-ai-marketplace"]
    assert marketplace["sessions"] == 4  # sess-1, sess-2, sess-6, sess-7


def test_efficiency_by_model_excludes_empty_sessions_by_default(conn):
    rows = queries.efficiency_by_model(conn, since="2026-08-01", until="2026-08-31")
    model_ids = {r["model_id"] for r in rows}
    assert "deepseek-v4-flash-free" not in model_ids


def test_efficiency_by_model_includes_empty_sessions_when_requested(conn):
    rows = queries.efficiency_by_model(
        conn, since="2026-08-01", until="2026-08-31", include_empty=True
    )
    model_ids = {r["model_id"] for r in rows}
    assert "deepseek-v4-flash-free" in model_ids


def test_session_detail_always_returns_explicit_empty_session(conn):
    # An explicitly-requested session must be returned regardless of its
    # empty/phantom status.
    detail = queries.session_detail(conn, "sess-6")
    assert detail is not None
    assert detail["session"]["id"] == "sess-6"


def test_session_detail_shows_empty_children_of_requested_session(conn):
    # sess-1's children must include sess-7 even though sess-7 is empty.
    detail = queries.session_detail(conn, "sess-1")
    child_ids = {c["id"] for c in detail["children"]}
    assert child_ids == {"sess-2", "sess-7"}
