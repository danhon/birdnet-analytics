# birdnet-analytics

**Status:** PROTOTYPE

- Analytics + reporting for BirdNET-GO (nightly-20260118).
- Current focus: reproducible rollups and a small web dashboard.

## What / Why

What this is:
- A small analytics layer over the BirdNET-GO SQLite DB.
- Scripts + (optional) web UI for exploring detection patterns over time.

Why it exists:
- Make it easy to answer questions like “who sings at dawn?” without bespoke SQL every time.
- Build repeatable, comparable rollups (hourly/daily counts, trends, QA).

Non-goals (for now):
- Replacing BirdNET-GO’s storage.

## Preamble (agentic)

This project is **agentic LLM code directed by Dan Hon**.

Notes:
- Expect fast iteration and occasional rough edges.
- Prefer filing an issue with repro steps over guessing intent.
- If something seems surprising, assume it’s a bug or an unfinished idea.

## Quickstart (demo)

### Option A: docker compose (recommended)

Prereqs:
- Docker + **docker compose**

Setup:
```sh
cp .env.example .env
# edit .env and set BIRDNET_DB_HOST_PATH=/absolute/path/to/birdnet.db
```

Run (daemonized):
```sh
docker compose up --build -d
```

What you should see:
- A running container (`docker compose ps`)
- A local server at <http://127.0.0.1:8787/>

Update to latest `main`:
```sh
git pull
docker compose up --build -d
```

Logs:
```sh
docker compose logs -f
```

Stop:
```sh
docker compose down
```

### Option B: local dev (uv)

Prereqs:
- Python + **uv**

Setup:
```sh
cd birdnet-analytics
uv venv
uv sync
```

Run a script (schema print):
```sh
uv run python scripts/print_schema.py /path/to/birdnet.db
```

Run the web dashboard locally:
```sh
export BIRDNET_DB_PATH=/path/to/birdnet.db
export BIRDNET_ANALYTICS_TZ=America/Los_Angeles
uv run uvicorn birdnet_analytics.web:app --reload --port 8787
```

## How it works

Project layout:
- `src/birdnet_analytics/` — library code
- `scripts/` — runnable entrypoints (ETL, backfills, reports)
- `configs/` — deployment/runtime configs (paths, DB locations)
- `docs/` — notes

Assumptions / invariants:
- BirdNET-GO’s SQLite DB is the source of truth.

### Decision log

- 2026-02-20 — Start keeping feature/stats ideas in README so collaborators can see the direction.

## Data sources

Source(s) of truth:
- BirdNET-GO SQLite database (`birdnet.db`).

Refresh / update cadence:
- Depends on the BirdNET-GO instance (out of scope here).

Known quirks / limitations:
- Raw detections can overcount repeated calls; most analytics should collapse into “events”.

## Roadmap

Top 5:
- [ ] Implement per-species hourly activity profiles (normalized).
- [ ] Morning vs afternoon index per species.
- [ ] Hour × month heatmaps per species.
- [ ] Event-collapsing rules (dedupe adjacent detections) as a first-class primitive.
- [ ] QA: detector drift / mis-ID sentinel checks.

## Feature ideas / stats backlog

Time-of-day / daily rhythms
- Per-species hourly activity profile (normalized) + peak hour.
- Morning vs afternoon index per species.
- Dawn-chorus concentration (% of detections in a window around sunrise).
- Crepuscular/nocturnal flags (sunset/overnight concentration).
- Weekend vs weekday pattern differences.
- Seasonal shift in peak hour.

Sunrise/sunset aware
- Activity vs solar time (minutes since sunrise/sunset) for season-robust comparisons.
- Pre-dawn vs post-dawn ratios.
- Photoperiod sensitivity (activity window vs day length).

Presence/absence & “show up” patterns
- First/last detection dates per year (arrival/departure proxy).
- Cumulative arrival/departure curves; year-over-year comparisons.
- Persistence: consecutive-day “run length” distributions (resident vs transient).
- Rarity spikes / bursty appearances (outliers for review).

Seasonality (multi-scale)
- Hour × month heatmap per species.
- Breeding-season proxy: spring-morning singing increases.
- Inter-annual trends (weekly counts with smoothing).

Weather correlations (if we join weather)
- Temperature/wind/rain effects (who goes quiet in wind, who pops after rain).
- Pressure-change/front effects.
- Extreme-day comparisons (hottest/coolest 5%).

Soundscape / detection ecology
- Noise-floor proxy vs detection rates (if audio metadata available).
- Confidence distribution per species (IDs that are consistently low-confidence).
- Co-detection networks (species that tend to appear together by hour/day).
- “Top species by hour block” through the year.

Spatial (multi-recorder)
- Site specialization and seasonal changes.
- Turnover between sites (present here/not there).
- Habitat gradients (e.g., distance-to-water / urban) vs detections (if site metadata exists).

Behavior-ish proxies from detections
- Calling-bout structure (gaps/clustering within mornings).
- Schedule consistency (“punctual” vs “all day”).
- Diversity by hour (species richness/entropy).

Data hygiene (make the archive trustworthy)
- Collapse adjacent duplicate detections into “events” to avoid overcounting.
- Detector drift checks (counts jump after model/recorder changes).
- Mis-ID sentinel list (out-of-range/season detections → review queue).

## Done / Next

Done:
- [x] Captured an initial feature/stats backlog in the README.

Next:
- [ ] Confirm BirdNET-GO SQLite DB path (out of this repo’s scope, but needed for real data).
- [ ] Capture schema (`.tables`, `.schema`) and commit sample outputs under `docs/`.

## Troubleshooting (top 3 footguns)

1) The dashboard loads but shows no data
- Cause: `BIRDNET_DB_PATH` points at the wrong file.
- Fix: set `BIRDNET_DB_PATH` to a real `birdnet.db`.

2) `uv` commands fail / wrong Python
- Cause: missing uv or an unexpected Python version.
- Fix: install uv and recreate the venv (`uv venv && uv sync`).

3) Counts look inflated
- Cause: repeated detections of the same individual are being counted as separate events.
- Fix: use event-collapsing (dedupe) before aggregating.
