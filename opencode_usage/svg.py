"""Pure-stdlib SVG rendering for the `graph` command.

No third-party imports. Renders a horizontal bar chart as a hand-written
SVG document, given a title and a list of (label, value) rows.
"""

from __future__ import annotations

from xml.sax.saxutils import escape


def render_bar_chart_svg(
    title: str,
    rows: list[tuple[str, int]],
    *,
    width: int = 900,
) -> str:
    """Render a horizontal bar chart as a standalone SVG document.

    One bar per row, bar length proportional to its value relative to the
    largest value in `rows`. Label and numeric value are drawn as text next
    to each bar. Returns an empty-state SVG when `rows` is empty.
    """
    margin_left = 220
    margin_right = 80
    margin_top = 50
    row_height = 32
    bar_height = 20

    if not rows:
        height = 120
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="100%" height="100%" fill="#111111"/>'
            f'<text x="{width / 2}" y="{height / 2}" fill="#eeeeee" '
            f'font-family="sans-serif" font-size="16" text-anchor="middle">'
            f"no data</text>"
            f"</svg>"
        )

    max_value = max(value for _, value in rows) or 1
    chart_width = width - margin_left - margin_right
    height = margin_top + row_height * len(rows) + 20

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#111111"/>',
        f'<text x="{margin_left}" y="28" fill="#eeeeee" font-family="sans-serif" '
        f'font-size="18" font-weight="bold">{escape(title)}</text>',
    ]

    for index, (label, value) in enumerate(rows):
        y = margin_top + index * row_height
        bar_width = (value / max_value) * chart_width if max_value else 0
        parts.append(
            f'<text x="{margin_left - 10}" y="{y + bar_height - 5}" fill="#eeeeee" '
            f'font-family="sans-serif" font-size="13" text-anchor="end">'
            f"{escape(str(label))}</text>"
        )
        parts.append(
            f'<rect x="{margin_left}" y="{y}" width="{bar_width:.2f}" '
            f'height="{bar_height}" fill="#7aa2f7"/>'
        )
        parts.append(
            f'<text x="{margin_left + bar_width + 8}" y="{y + bar_height - 5}" '
            f'fill="#eeeeee" font-family="sans-serif" font-size="13">'
            f"{escape(str(value))}</text>"
        )

    parts.append("</svg>")
    return "".join(parts)
