"""Console-script entry point for the installed `opencode-usage` command.

`opencode_usage.cli:app` is a `typer.Typer()` instance, not a plain callable
function, so it can't be pointed at directly from `[project.scripts]` on
every typer/click version. This tiny wrapper gives `pyproject.toml` a real
function to target without touching `cli.py` (owned by another change).
"""

from __future__ import annotations

from .cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
