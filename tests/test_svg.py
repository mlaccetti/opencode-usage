"""Tests for opencode_usage.svg."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from opencode_usage.svg import render_bar_chart_svg  # noqa: E402


def test_render_bar_chart_svg_empty_rows_is_written_to_file(tmp_path):
    """Mirrors the CLI `graph` no-data path: even with zero rows, the
    rendered SVG must still be a valid, writable file, not skipped."""
    svg = render_bar_chart_svg("Sessions by model (no window)", [])
    output = tmp_path / "sessions.svg"
    output.write_text(svg)

    written = output.read_text()
    assert written.startswith("<svg")
    assert "no data" in written


def test_render_bar_chart_svg_basic():
    rows = [("claude-sonnet-5", 12), ("gpt-5", 4)]
    svg = render_bar_chart_svg("Sessions by model", rows)

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "<svg" in svg
    assert svg.count("<rect") >= len(rows) + 1  # background + one bar per row
    assert "claude-sonnet-5" in svg
    assert "gpt-5" in svg
    assert ">12<" in svg
    assert ">4<" in svg


def test_render_bar_chart_svg_escapes_labels():
    rows = [("<script>alert(1)</script>", 3)]
    svg = render_bar_chart_svg("Title", rows)

    assert "<script>alert(1)</script>" not in svg
    assert "&lt;script&gt;" in svg


def test_render_bar_chart_svg_empty_rows():
    svg = render_bar_chart_svg("Sessions by model", [])

    assert "<svg" in svg
    assert "no data" in svg
