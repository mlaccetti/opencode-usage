"""Pure-stdlib query core for opencode usage reporting.

No third-party imports here (no typer, no rich). All aggregation over
opencode.db lives in this module so it can be reused by the CLI, an HTML
report, or a future webapp without dragging in presentation dependencies.

The database is always opened read-only:

    sqlite3.connect(f"file:{path}?mode=ro", uri=True)

`mode=ro` (not `immutable`) is used deliberately so reads stay correct while
opencode is actively writing to the DB in WAL mode.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import quote


DEFAULT_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")


def connect_readonly(path: os.PathLike | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open opencode.db strictly read-only.

    Uses mode=ro (not immutable) so WAL-mode reads stay consistent while
    opencode is running and writing concurrently.
    """
    uri = f"file:{quote(os.fspath(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _date_to_epoch_millis(date_str: str, end_of_day: bool = False) -> int:
    """Convert a YYYY-MM-DD string, interpreted in local time, to epoch millis.

    `end_of_day=True` returns the last millisecond of that local day, so
    `until` bounds are inclusive of the whole day.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
    # datetime.strptime gives a naive datetime; astimezone() with no argument
    # attaches the system local timezone, which is what we want.
    local_dt = dt.astimezone()
    return int(local_dt.timestamp() * 1000)


def _window_clause(since: Optional[str], until: Optional[str], column: str = "time_created"):
    """Build a SQL WHERE fragment + params for a since/until local-date window."""
    clauses = []
    params: list = []
    if since:
        clauses.append(f"{column} >= ?")
        params.append(_date_to_epoch_millis(since, end_of_day=False))
    if until:
        clauses.append(f"{column} <= ?")
        params.append(_date_to_epoch_millis(until, end_of_day=True))
    return clauses, params


def _base_filter(
    since: Optional[str],
    until: Optional[str],
    include_empty: bool = False,
    extra: Optional[list[str]] = None,
) -> tuple[str, list]:
    """Build the shared WHERE clause + params used by every aggregate query.

    Combines the since/until window with the "empty session" exclusion:
    sessions where tokens_input + tokens_output = 0 are phantom sessions
    (e.g. Orca-spawned sessions that never ran a real turn) and are
    excluded by default. Pass include_empty=True to bring them back.

    `extra` are additional base clauses (e.g. "model IS NOT NULL") that
    should be ANDed in alongside the window and emptiness clauses.
    """
    clauses, params = _window_clause(since, until)
    clauses = list(extra or []) + clauses
    if not include_empty:
        clauses.append("(tokens_input + tokens_output) > 0")
    if not clauses:
        clauses = ["1=1"]
    return " AND ".join(clauses), params


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Divide, guarding the divide-by-zero case by returning 0.0."""
    if not denominator:
        return 0.0
    return numerator / denominator


_BUCKET_FORMAT = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
}


def cost_by_model(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    group_by: Optional[str] = None,
    include_empty: bool = False,
) -> list[dict]:
    """Cost and tokens by model + provider, optionally bucketed by day/week.

    Empty (zero-token) phantom sessions are excluded by default; pass
    include_empty=True to include them.
    """
    where, params = _base_filter(since, until, include_empty, extra=["model IS NOT NULL"])

    bucket_select = ""
    group_extra = ""
    if group_by in ("day", "week"):
        fmt = _BUCKET_FORMAT[group_by]
        bucket_select = (
            f", strftime('{fmt}', time_created / 1000, 'unixepoch', 'localtime') AS bucket"
        )
        group_extra = ", bucket"

    sql = f"""
        SELECT
            json_extract(model, '$.id') AS model_id,
            json_extract(model, '$.providerID') AS provider,
            COUNT(*) AS sessions,
            COALESCE(SUM(cost), 0) AS cost,
            COALESCE(SUM(tokens_input), 0) AS tokens_input,
            COALESCE(SUM(tokens_output), 0) AS tokens_output
            {bucket_select}
        FROM session
        WHERE {where}
        GROUP BY model_id, provider{group_extra}
        ORDER BY cost DESC
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def sessions_by_model(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    include_empty: bool = False,
) -> list[dict]:
    """Session counts per model + provider, sorted by sessions desc.

    Thin wrapper over the same window/grouping logic as cost_by_model, just
    reordered and trimmed to (model_id, provider, sessions) for graphing.
    Empty (zero-token) phantom sessions are excluded by default.
    """
    rows = cost_by_model(conn, since=since, until=until, include_empty=include_empty)
    result = [
        {
            "model_id": r["model_id"],
            "provider": r["provider"],
            "sessions": r["sessions"],
        }
        for r in rows
    ]
    result.sort(key=lambda r: r["sessions"], reverse=True)
    return result


def agent_breakdown(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    include_empty: bool = False,
) -> dict:
    """Per-agent breakdown with top-level vs sub-agent totals.

    `is_subagent` is derived from `parent_id IS NOT NULL`. Empty (zero-token)
    phantom sessions are excluded by default.
    """
    where, params = _base_filter(since, until, include_empty)

    sql = f"""
        SELECT
            COALESCE(agent, 'unknown') AS agent,
            (parent_id IS NOT NULL) AS is_subagent,
            COUNT(*) AS sessions,
            COALESCE(SUM(cost), 0) AS cost,
            COALESCE(SUM(tokens_input), 0) AS tokens_input,
            COALESCE(SUM(tokens_output), 0) AS tokens_output
        FROM session
        WHERE {where}
        GROUP BY agent, is_subagent
        ORDER BY cost DESC
    """
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["is_subagent"] = bool(r["is_subagent"])

    totals_sql = f"""
        SELECT
            (parent_id IS NOT NULL) AS is_subagent,
            COUNT(*) AS sessions,
            COALESCE(SUM(cost), 0) AS cost,
            COALESCE(SUM(tokens_input), 0) AS tokens_input,
            COALESCE(SUM(tokens_output), 0) AS tokens_output
        FROM session
        WHERE {where}
        GROUP BY is_subagent
    """
    totals_rows = conn.execute(totals_sql, params).fetchall()
    totals = {
        "top_level": {"sessions": 0, "cost": 0.0, "tokens_input": 0, "tokens_output": 0},
        "subagent": {"sessions": 0, "cost": 0.0, "tokens_input": 0, "tokens_output": 0},
    }
    for row in totals_rows:
        key = "subagent" if row["is_subagent"] else "top_level"
        totals[key] = {
            "sessions": row["sessions"],
            "cost": row["cost"],
            "tokens_input": row["tokens_input"],
            "tokens_output": row["tokens_output"],
        }

    return {"agents": rows, "totals": totals}


def cost_by_project(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    include_empty: bool = False,
) -> list[dict]:
    """Cost/tokens grouped by project worktree, displayed as its basename.

    Sessions with no matching project (or no project_id) fall back to the
    display name "global". Empty (zero-token) phantom sessions are
    excluded by default.
    """
    where, params = _base_filter(since, until, include_empty)

    sql = f"""
        SELECT
            session.project_id AS project_id,
            project.worktree AS worktree,
            COUNT(*) AS sessions,
            COALESCE(SUM(session.cost), 0) AS cost,
            COALESCE(SUM(session.tokens_input), 0) AS tokens_input,
            COALESCE(SUM(session.tokens_output), 0) AS tokens_output
        FROM session
        LEFT JOIN project ON session.project_id = project.id
        WHERE {where}
        GROUP BY session.project_id
        ORDER BY cost DESC
    """
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        worktree = d.pop("worktree")
        project_id = d.pop("project_id")
        if worktree:
            display_name = os.path.basename(worktree.rstrip("/")) or worktree
        elif project_id:
            display_name = project_id
        else:
            display_name = "global"
        d["display_name"] = display_name
        result.append(d)
    return result


def session_detail(conn: sqlite3.Connection, session_id: str) -> Optional[dict]:
    """One session's full detail: parsed model, token split, children, message count."""
    row = conn.execute(
        """
        SELECT id, project_id, parent_id, slug, directory, title,
               cost, tokens_input, tokens_output, tokens_reasoning,
               tokens_cache_read, tokens_cache_write, agent, model,
               time_created, time_updated
        FROM session WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None

    session = dict(row)
    model_raw = session.pop("model")
    model_id = None
    provider = None
    if model_raw:
        try:
            parsed = json.loads(model_raw)
            model_id = parsed.get("id")
            provider = parsed.get("providerID")
        except (ValueError, TypeError):
            pass
    session["model_id"] = model_id
    session["provider"] = provider

    children_rows = conn.execute(
        """
        SELECT id, agent, cost, tokens_input, tokens_output,
               tokens_reasoning, tokens_cache_read, tokens_cache_write
        FROM session WHERE parent_id = ?
        ORDER BY time_created
        """,
        (session_id,),
    ).fetchall()
    children = [dict(r) for r in children_rows]

    message_count_row = conn.execute(
        "SELECT COUNT(*) AS n FROM message WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    message_count = message_count_row["n"] if message_count_row else 0

    return {
        "session": session,
        "children": children,
        "message_count": message_count,
    }


def efficiency_by_model(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    include_empty: bool = False,
) -> list[dict]:
    """Per-model token efficiency ratios: cache read/write, reasoning share, output:input.

    Empty (zero-token) phantom sessions are excluded by default.
    """
    where, params = _base_filter(since, until, include_empty, extra=["model IS NOT NULL"])

    sql = f"""
        SELECT
            json_extract(model, '$.id') AS model_id,
            json_extract(model, '$.providerID') AS provider,
            COALESCE(SUM(tokens_input), 0) AS tokens_input,
            COALESCE(SUM(tokens_output), 0) AS tokens_output,
            COALESCE(SUM(tokens_reasoning), 0) AS tokens_reasoning,
            COALESCE(SUM(tokens_cache_read), 0) AS tokens_cache_read,
            COALESCE(SUM(tokens_cache_write), 0) AS tokens_cache_write
        FROM session
        WHERE {where}
        GROUP BY model_id, provider
        ORDER BY model_id
    """
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["cache_read_ratio"] = _safe_ratio(
            d["tokens_cache_read"], d["tokens_input"] + d["tokens_cache_read"]
        )
        d["reasoning_share"] = _safe_ratio(d["tokens_reasoning"], d["tokens_output"])
        d["output_input_ratio"] = _safe_ratio(d["tokens_output"], d["tokens_input"])
        result.append(d)
    return result
