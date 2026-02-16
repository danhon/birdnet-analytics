# Project status (2026-02-16)

## Repo
- Path: `/home/node/agentic/birdnet-analytics`
- Python: uv-managed (`uv venv`, `uv sync`, `uv run ...`)

## Data
- BirdNET-GO SQLite source-of-truth:
  - Ubuntuplex path provided: `/home/danhon/birdnet-go-app/data/birdnet.db`
- Sample copy kept locally under `_data/sample/birdnet.db` (gitignored)
- Policy: **Do not commit real birdnet.db** (contains precise lat/lon + timestamps)
  - See `docs/sample-data.md`

## Implemented

### Dawn chorus detections/hour (CLI)
- `scripts/dawn_chorus_hourly.py`
  - Uses Astral sunrise (fallback when `daily_events.sunrise` is 0/unset)
  - Correct local-hour bucketing by parsing timestamps in **Python** (SQLite datetime parsing produced wrong hours)
  - Configurable:
    - `--tz` (default America/Los_Angeles)
    - `--before-min` (default 90)
    - `--after-min` (default 150)

### Minimal web dashboard (v0)
- `src/birdnet_analytics/web.py`
  - FastAPI app
  - `GET /` serves a single HTML page with Chart.js bar chart
  - `GET /health`
  - `GET /api/dawn/hourly?day=YYYY-MM-DD&tz_name=...&before_min=...&after_min=...`
- Local run pattern (important):
  - `uv run uvicorn --app-dir src birdnet_analytics.web:app --reload --host 0.0.0.0 --port 8787`
  - Bind to `0.0.0.0` for LAN access

### Config skeleton
- `configs/ubuntuplex.env.example`
  - `BIRDNET_DB_DIR=/home/danhon/birdnet-go-app/data`
  - `BIRDNET_DB_PATH=...` (preferred)
  - `BIRDNET_ANALYTICS_HOST`, `BIRDNET_ANALYTICS_PORT`
  - `BIRDNET_ANALYTICS_TZ=America/Los_Angeles`

## Known issues / caveats
- `daily_events.sunrise/sunset` in the sample DB are all zeros, so sunrise currently computed via Astral using lat/lon from `notes`.
- Current API computes metrics on-demand from BirdNET DB; for larger history we likely want rollups/materialization.

## Left to do (next steps)
1. Add day-part analytics (morning/afternoon/evening) + chart
   - Decide fixed clock boundaries vs sunrise/sunset-based boundaries.
2. Add species-X activity-by-hour analytics + chart (hour-of-day histogram)
3. Add week-by-week rollups (as requested) and/or daily totals
4. Materialize an analytics DB (separate SQLite) for speed:
   - build rollup tables (hourly, daily, weekly)
   - incremental refresh job (cron/systemd timer)
5. Ubuntuplex deployment:
   - systemd unit + (optional) nginx reverse proxy
   - security: treat as potentially public (no raw paths/lat-lon in UI)
6. Optional: bring in `hourly_weathers` table for weather correlation.
