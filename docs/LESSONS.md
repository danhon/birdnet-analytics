# Lessons / pitfalls

## 1) Repo location
- Put code under a mounted dev volume, not the workspace root.

## 2) Shell brace expansion
- Avoid `mkdir -p project/{a,b,c}` unless you are sure brace expansion is enabled.
- Safer: run separate `mkdir -p` commands.

## 3) Python deps
- Use `uv` for venv/deps (avoid `pip`). Commit `uv.lock`.

## 4) src/ layout + uvicorn
- With a `[build-system]` table in `pyproject.toml` and `uv sync`, the package installs
  into the venv so `uv run uvicorn birdnet_analytics.web:app` works directly.
- Without it, you need `--app-dir src` or `PYTHONPATH=src`.

## 5) Timestamps in birdnet-go (new schema)
- `detections.detected_at` is an **integer unix epoch (seconds)**.
- Convert to local time in Python: `datetime.fromtimestamp(ts, tz=ZoneInfo("America/Los_Angeles"))`.
- Do not rely on SQLite `datetime()` for timezone conversion; use Python.

## 6) Old schema (notes table) — no longer exists
- birdnet-go replaced the flat `notes` table with `detections + labels`.
- The old `notes.begin_time` was an ISO 8601 text string with nanosecond precision
  (e.g. `2026-02-16 09:33:43.731828028-08:00`) — parsing required truncating to microseconds.
- This is irrelevant for the current schema; do not reintroduce text-timestamp parsing.

## 7) Chart.js mixed charts
- For mixed bar/line charts, set an explicit base `type`.

## 8) Private IP access
- Some web fetch tools block private/internal IPs (incl. Tailscale 100.x).
- Use `exec` + curl/python to test Tailnet endpoints.

## 9) Git workflow: commit vs push
- Local commits are invisible on other machines until pushed.
- For user-visible changes (UI, endpoints, bugfixes), prefer **commit + push** immediately.
- Always report: commit hash + whether it was pushed.
- Treat `uv.lock` updates as part of "done" when deps change.
