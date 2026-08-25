# opencode-usage

A CLI that reports [opencode](https://opencode.ai) usage: cost, tokens, agents, and projects, read directly from the local opencode SQLite database.

## Install

Homebrew:

```
brew tap mlaccetti/tap
brew install opencode-usage
```

From source:

```
uv tool install .
# or
pipx install .
```

## Usage

All commands read `~/.local/share/opencode/opencode.db` by default; override with `--db`. Every reporting command accepts `--since YYYY-MM-DD` and `--until YYYY-MM-DD` (inclusive, local time), `--json` for machine-readable output, and `--include-empty` to bring back zero-token phantom sessions (excluded by default).

Cost and tokens by model:

```
opencode-usage models
opencode-usage models --since 2026-08-01 --until 2026-08-24
opencode-usage models --group-by day --html report.html
opencode-usage models --json
```

Top-level agent vs sub-agent spend:

```
opencode-usage agents
```

Cost by project:

```
opencode-usage projects
```

Drill into one session:

```
opencode-usage session <session-id>
opencode-usage session <session-id> --json
```

Token efficiency per model (cache read/write, reasoning share, output:input ratio):

```
opencode-usage efficiency
```

Horizontal bar chart of sessions per model, rendered as a standalone SVG (no third-party dependencies):

```
opencode-usage graph
opencode-usage graph --since 2026-08-01 --output sessions.svg
```

`--html` on `models`, `agents`, `projects`, or `efficiency` writes a self-contained HTML report (Chart.js via CDN) alongside the table output.

## How it works

The database is always opened read-only:

```python
sqlite3.connect(f"file:{path}?mode=ro", uri=True)
```

`mode=ro` (not `immutable`) is used deliberately so reads stay correct while opencode is actively writing to the database in WAL mode. This tool never writes to opencode.db.

## Phantom sessions

Sessions with zero input and zero output tokens are phantom sessions, most commonly Orca-spawned sessions that never ran a turn. They are excluded from every aggregate by default. Pass `--include-empty` if you want them counted.

## Cost vs tokens

Cost can be `$0` for free or local providers while token counts are large. Read cost alongside tokens; a `$0` line does not mean no usage happened.

## License

MIT
