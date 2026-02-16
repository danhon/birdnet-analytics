# Lessons / pitfalls (2026-02-16)

## 1) Repo location
- Put code under `/home/node/agentic/...` (mounted dev volume), not workspace root.

## 2) Shell brace expansion
- Avoid `mkdir -p project/{a,b,c}` unless you are sure brace expansion is enabled.
- Safer: run separate `mkdir -p` commands or use a small script.

## 3) Python deps
- Use `uv` for venv/deps (avoid `pip`). Commit `uv.lock`.

## 4) src/ layout + uvicorn
- If package lives under `src/`, run uvicorn with `--app-dir src` (or install the project into the venv).

## 5) Timezones
- BirdNET timestamps include offsets with >6 fractional digits.
- Do not rely on SQLite `datetime()` for tz conversion.
- Parse timestamps in Python (truncate to microseconds), convert to configured TZ.

## 6) Chart.js mixed charts
- For mixed bar/line charts, set an explicit base `type`.

## 7) Private IP access
- OpenClaw `web_fetch` blocks private/internal IPs (incl. Tailscale 100.x).
- Use `exec` + curl/python to test Tailnet endpoints.
