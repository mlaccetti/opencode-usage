"""typer + rich CLI for opencode usage reporting.

All aggregation logic lives in queries.py (pure stdlib). This module only
handles argument parsing, table rendering, JSON output, and the --html
report export.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "typer>=0.12",
#     "rich>=13",
# ]
# ///

from __future__ import annotations

import json as jsonlib
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from opencode_usage import queries
from opencode_usage.svg import render_bar_chart_svg

app = typer.Typer(
    name="opencode-usage",
    help=(
        "Report opencode usage (cost, tokens, agents, projects) from opencode.db. "
        "Read-only. Empty (zero-token) phantom sessions are excluded by default; "
        "pass --include-empty to bring them back."
    ),
    no_args_is_help=True,
)
console = Console()


def _open(db: str):
    return queries.connect_readonly(db)


def _emit_table(title: str, rows: list[dict], as_json: bool, html: Optional[str], html_kind: str = "generic"):
    if as_json:
        console.print_json(jsonlib.dumps(rows, default=str))
        return
    if not rows:
        console.print(f"[yellow]No data for[/yellow] {title}")
        return
    table = Table(title=title)
    for key in rows[0].keys():
        table.add_column(str(key))
    for row in rows:
        table.add_row(*[_fmt(v) for v in row.values()])
    console.print(table)


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return "" if value is None else str(value)


DbOption = typer.Option(queries.DEFAULT_DB_PATH, "--db", help="Path to opencode.db")
SinceOption = typer.Option(None, "--since", help="Local date YYYY-MM-DD, inclusive")
UntilOption = typer.Option(None, "--until", help="Local date YYYY-MM-DD, inclusive")
JsonOption = typer.Option(False, "--json", help="Emit machine-readable JSON instead of a table")
GroupByOption = typer.Option(None, "--group-by", help="day|week bucketing for time-series commands")
HtmlOption = typer.Option(None, "--html", help="Write a self-contained HTML report to this path")
IncludeEmptyOption = typer.Option(
    False,
    "--include-empty",
    help=(
        "Include empty (zero-token) phantom sessions, e.g. Orca-spawned "
        "sessions that never ran a turn. Excluded by default."
    ),
)


@app.command()
def models(
    db: str = DbOption,
    since: Optional[str] = SinceOption,
    until: Optional[str] = UntilOption,
    json: bool = JsonOption,
    group_by: Optional[str] = GroupByOption,
    html: Optional[str] = HtmlOption,
    include_empty: bool = IncludeEmptyOption,
):
    """Cost and tokens by model + provider over time.

    Empty (zero-token) sessions are excluded by default; pass
    --include-empty to bring them back.
    """
    conn = _open(db)
    try:
        rows = queries.cost_by_model(
            conn, since=since, until=until, group_by=group_by, include_empty=include_empty
        )
        _emit_table("Cost by model", rows, json, html)
        if html:
            _write_html_report(conn, since, until, html, include_empty=include_empty)
    finally:
        conn.close()


@app.command()
def agents(
    db: str = DbOption,
    since: Optional[str] = SinceOption,
    until: Optional[str] = UntilOption,
    json: bool = JsonOption,
    html: Optional[str] = HtmlOption,
    include_empty: bool = IncludeEmptyOption,
):
    """Top-level agent vs sub-agent spend.

    Empty (zero-token) sessions are excluded by default; pass
    --include-empty to bring them back.
    """
    conn = _open(db)
    try:
        result = queries.agent_breakdown(conn, since=since, until=until, include_empty=include_empty)
        if json:
            console.print_json(jsonlib.dumps(result, default=str))
        else:
            _emit_table("Agent breakdown", result["agents"], False, None)
            totals = result["totals"]
            console.print(
                f"[bold]Top-level:[/bold] {totals['top_level']['sessions']} sessions, "
                f"${totals['top_level']['cost']:.4f}, "
                f"{totals['top_level']['tokens_input']}in/{totals['top_level']['tokens_output']}out"
            )
            console.print(
                f"[bold]Sub-agent:[/bold] {totals['subagent']['sessions']} sessions, "
                f"${totals['subagent']['cost']:.4f}, "
                f"{totals['subagent']['tokens_input']}in/{totals['subagent']['tokens_output']}out"
            )
        if html:
            _write_html_report(conn, since, until, html, include_empty=include_empty)
    finally:
        conn.close()


@app.command()
def projects(
    db: str = DbOption,
    since: Optional[str] = SinceOption,
    until: Optional[str] = UntilOption,
    json: bool = JsonOption,
    html: Optional[str] = HtmlOption,
    include_empty: bool = IncludeEmptyOption,
):
    """Cost by project / repo.

    Empty (zero-token) sessions are excluded by default; pass
    --include-empty to bring them back.
    """
    conn = _open(db)
    try:
        rows = queries.cost_by_project(conn, since=since, until=until, include_empty=include_empty)
        _emit_table("Cost by project", rows, json, html)
        if html:
            _write_html_report(conn, since, until, html, include_empty=include_empty)
    finally:
        conn.close()


@app.command()
def session(
    session_id: str = typer.Argument(..., help="Session id to drill into"),
    db: str = DbOption,
    json: bool = JsonOption,
):
    """Per-session drill-down: model, cost, token split, sub-agents, message count."""
    conn = _open(db)
    try:
        detail = queries.session_detail(conn, session_id)
        if detail is None:
            console.print(f"[red]No session found with id[/red] {session_id}")
            raise typer.Exit(code=1)
        if json:
            console.print_json(jsonlib.dumps(detail, default=str))
            return
        s = detail["session"]
        console.print(f"[bold]{s['title']}[/bold] ({s['id']})")
        console.print(f"Model: {s['model_id']} / {s['provider']}  Agent: {s['agent']}")
        console.print(f"Directory: {s['directory']}")
        console.print(
            f"Cost: ${s['cost']:.4f}  "
            f"Tokens in={s['tokens_input']} out={s['tokens_output']} "
            f"reasoning={s['tokens_reasoning']} cache_read={s['tokens_cache_read']} "
            f"cache_write={s['tokens_cache_write']}"
        )
        console.print(f"Message turns: {detail['message_count']}")
        if detail["children"]:
            _emit_table("Sub-agent sessions", detail["children"], False, None)
        else:
            console.print("No sub-agent sessions.")
    finally:
        conn.close()


@app.command()
def efficiency(
    db: str = DbOption,
    since: Optional[str] = SinceOption,
    until: Optional[str] = UntilOption,
    json: bool = JsonOption,
    html: Optional[str] = HtmlOption,
    include_empty: bool = IncludeEmptyOption,
):
    """Token efficiency per model: cache read/write, reasoning share, output:input.

    Empty (zero-token) sessions are excluded by default; pass
    --include-empty to bring them back.
    """
    conn = _open(db)
    try:
        rows = queries.efficiency_by_model(conn, since=since, until=until, include_empty=include_empty)
        _emit_table("Efficiency by model", rows, json, html)
        if html:
            _write_html_report(conn, since, until, html, include_empty=include_empty)
    finally:
        conn.close()


@app.command()
def graph(
    db: str = DbOption,
    since: Optional[str] = SinceOption,
    until: Optional[str] = UntilOption,
    output: str = typer.Option(
        "./opencode-usage-model-sessions.svg",
        "--output",
        "-o",
        help="Path to write the rendered SVG",
    ),
    metric: str = typer.Option(
        "sessions",
        "--metric",
        help="Metric to graph (only 'sessions' is supported today)",
    ),
    include_empty: bool = IncludeEmptyOption,
):
    """Render a horizontal bar chart of sessions per model for the window.

    Empty (zero-token) sessions are excluded by default; pass
    --include-empty to bring them back.
    """
    if metric != "sessions":
        console.print(f"[red]Unsupported metric[/red] {metric!r}; only 'sessions' is supported.")
        raise typer.Exit(code=1)

    conn = _open(db)
    try:
        rows = queries.sessions_by_model(conn, since=since, until=until, include_empty=include_empty)
    finally:
        conn.close()

    window_desc = f"{since or 'earliest'} to {until or 'latest'}"

    if not rows:
        console.print(f"[yellow]No session data for[/yellow] {window_desc}")
        return

    labels = [f"{r['model_id']} ({r['provider']})" for r in rows]
    values = [r["sessions"] for r in rows]

    svg = render_bar_chart_svg(f"Sessions by model ({window_desc})", list(zip(labels, values)))
    with open(output, "w") as f:
        f.write(svg)

    console.print(f"[green]Saved graph to[/green] {output}")

    max_sessions = max(values) if values else 0
    bar_width = 40
    for label, value in zip(labels, values):
        filled = int(bar_width * value / max_sessions) if max_sessions else 0
        bar = "█" * filled
        console.print(f"{label:<30} {bar} {value}")


def _write_html_report(
    conn, since: Optional[str], until: Optional[str], path: str, include_empty: bool = False
) -> None:
    """Render a self-contained HTML report: overview, cost-over-time, by-model,
    agent vs sub-agent split. Uses Chart.js via CDN; data is injected inline.
    """
    model_rows = queries.cost_by_model(conn, since=since, until=until, include_empty=include_empty)
    daily_rows = queries.cost_by_model(
        conn, since=since, until=until, group_by="day", include_empty=include_empty
    )
    agent_result = queries.agent_breakdown(conn, since=since, until=until, include_empty=include_empty)

    total_cost = sum(r["cost"] for r in model_rows)
    total_sessions = sum(r["sessions"] for r in model_rows)
    total_tokens_in = sum(r["tokens_input"] for r in model_rows)
    total_tokens_out = sum(r["tokens_output"] for r in model_rows)

    # cost-over-time: aggregate by bucket across models
    by_bucket: dict[str, float] = {}
    for r in daily_rows:
        by_bucket[r["bucket"]] = by_bucket.get(r["bucket"], 0.0) + r["cost"]
    buckets = sorted(by_bucket.keys())
    bucket_costs = [by_bucket[b] for b in buckets]

    model_labels = [r["model_id"] for r in model_rows]
    model_costs = [r["cost"] for r in model_rows]

    totals = agent_result["totals"]
    agent_split_labels = ["Top-level", "Sub-agent"]
    agent_split_values = [totals["top_level"]["sessions"], totals["subagent"]["sessions"]]

    payload = {
        "overview": {
            "total_cost": total_cost,
            "total_tokens_input": total_tokens_in,
            "total_tokens_output": total_tokens_out,
            "sessions": total_sessions,
        },
        "cost_over_time": {"labels": buckets, "values": bucket_costs},
        "by_model": {"labels": model_labels, "values": model_costs},
        "agent_split": {"labels": agent_split_labels, "values": agent_split_values},
    }

    html = _HTML_TEMPLATE.replace("__DATA__", jsonlib.dumps(payload))
    with open(path, "w") as f:
        f.write(html)
    console.print(f"[green]Wrote HTML report to[/green] {path}")


_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>opencode usage report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body { font-family: -apple-system, sans-serif; margin: 2rem; background: #111; color: #eee; }
  .overview { display: flex; gap: 2rem; margin-bottom: 2rem; }
  .card { background: #1e1e1e; padding: 1rem 1.5rem; border-radius: 8px; }
  .card h2 { margin: 0; font-size: 1.8rem; }
  .card p { margin: 0.25rem 0 0; color: #aaa; }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
  canvas { background: #1e1e1e; border-radius: 8px; padding: 1rem; }
  .note { color: #f0c674; margin-top: 2rem; font-size: 0.9rem; }
</style>
</head>
<body>
<h1>opencode usage report</h1>
<div class="overview">
  <div class="card"><h2 id="total-cost"></h2><p>Total cost</p></div>
  <div class="card"><h2 id="total-tokens"></h2><p>Total tokens (in+out)</p></div>
  <div class="card"><h2 id="total-sessions"></h2><p>Sessions</p></div>
</div>
<div class="charts">
  <canvas id="costOverTime"></canvas>
  <canvas id="byModel"></canvas>
  <canvas id="agentSplit"></canvas>
</div>
<p class="note">Cost can be $0 for free/local providers while tokens are large.
Always read tokens alongside cost; a $0 line is not "no usage".</p>
<script>
const data = __DATA__;
document.getElementById('total-cost').textContent = '$' + data.overview.total_cost.toFixed(2);
document.getElementById('total-tokens').textContent =
  (data.overview.total_tokens_input + data.overview.total_tokens_output).toLocaleString();
document.getElementById('total-sessions').textContent = data.overview.sessions;

new Chart(document.getElementById('costOverTime'), {
  type: 'line',
  data: {
    labels: data.cost_over_time.labels,
    datasets: [{ label: 'Cost over time', data: data.cost_over_time.values, borderColor: '#5fd3a3' }]
  }
});

new Chart(document.getElementById('byModel'), {
  type: 'bar',
  data: {
    labels: data.by_model.labels,
    datasets: [{ label: 'Cost by model', data: data.by_model.values, backgroundColor: '#7aa2f7' }]
  }
});

new Chart(document.getElementById('agentSplit'), {
  type: 'doughnut',
  data: {
    labels: data.agent_split.labels,
    datasets: [{ data: data.agent_split.values, backgroundColor: ['#7aa2f7', '#f7768e'] }]
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app()
