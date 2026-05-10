# Project status (2026-05-10)

## Repo
- Python: uv-managed (`uv sync`, `uv run ...`)
- Package is installable via `uv sync` (hatchling build backend); no `PYTHONPATH` override needed.

## Data
- BirdNET-GO SQLite source-of-truth: configure via `BIRDNET_DB_PATH` or `BIRDNET_DB_DIR`
- Sample copy kept locally under `_data/sample/birdnet.db` (gitignored)
- Policy: **Do not commit real birdnet.db** (contains precise lat/lon + timestamps)
  - See `docs/sample-data.md`

## Schema
birdnet-go migrated from a flat `notes` table to a normalized schema. This project targets the
new schema exclusively.

Key tables used:
- `detections` — one row per detection; `detected_at` is a unix epoch integer
- `labels` — species lookup; join via `detections.label_id = labels.id`
- `daily_events` — per-day sunrise/sunset (integer unix seconds; 0 = not populated)

The old `notes` table no longer exists.

## Implemented

### Dawn chorus detections/hour (CLI)
- `scripts/dawn_chorus_hourly.py`
  - Queries `detections` table; uses `date(detected_at, 'unixepoch')` for date filtering
  - Uses Astral sunrise (fallback when `daily_events.sunrise` is 0/unset)
  - Correct local-hour bucketing via `datetime.fromtimestamp(ts, tz=tz)`
  - Configurable: `--tz`, `--before-min`, `--after-min`

### Web dashboard
- `src/birdnet_analytics/web.py` — FastAPI app
- Endpoints:
  - `GET /` — single-page Chart.js dashboard
  - `GET /health`
  - `GET /api/dawn/hourly` — dawn-window detections bucketed by clock time
  - `GET /api/dawn/by_day` — per-day dawn buckets (heatmap + stacked chart)
  - `GET /api/dayparts/daily` — fixed 6-hour time blocks + precipitation overlay
  - `GET /api/activity/hourly_stats` — per-hour min/mean/max/percentiles + today overlay
  - `GET /api/topshare/daily` — top-N species share (100% stacked)
  - `GET /api/wow` — week-over-week detections + unique species
  - `GET /api/species/search` — species autocomplete (scientific name)

### Run locally
```bash
BIRDNET_DB_PATH=_data/sample/birdnet.db BIRDNET_ANALYTICS_TZ=America/Los_Angeles \
  uv run uvicorn birdnet_analytics.web:app --reload --host 0.0.0.0 --port 8787
```

### Config env vars
| Var | Default | Purpose |
|---|---|---|
| `BIRDNET_DB_PATH` | — | Absolute path to birdnet.db |
| `BIRDNET_DB_DIR` | — | Directory containing birdnet.db (alternative to PATH) |
| `BIRDNET_ANALYTICS_TZ` | `America/Los_Angeles` | IANA timezone for charts |
| `BIRDNET_ANALYTICS_HOST` | `127.0.0.1` | Bind host |
| `BIRDNET_ANALYTICS_PORT` | `8787` | Bind port |
| `BIRDNET_ANALYTICS_REFRESH_SECONDS` | `30` | Dashboard auto-refresh interval |

## Known issues / caveats
- `daily_events.sunrise/sunset` in the sample DB are all zeros; sunrise is computed via Astral
  using lat/lon from the first `detections` row that has coordinates.
- `api_activity_hourly_stats` iterates day-by-day (one DB query per day). Fine for months of
  data; may slow for multi-year datasets. A grouped SQL query would scale better.
- Species are identified by `scientific_name` only (no common names in the DB schema).

## Left to do (next steps)
1. Add `label_type_id = 1` filter to species queries as future-proofing once birdnet-go starts
   populating noise/environment detection labels.
2. Materialize an analytics DB (separate SQLite) for speed at larger scale.
3. Optional: bring in `hourly_weathers` table for richer weather correlation.
