# Migration Plan: birdnet-go schema update

## Background

birdnet-go replaced its flat `notes` table with a normalized schema. The analytics web app
(`src/birdnet_analytics/web.py`) was already migrated in commit `3f2874b` and works correctly
against the new DB. One script was missed.

## New schema (confirmed against `_data/sample/birdnet.db`)

Key tables and columns used by this project:

```sql
detections (
  id          INTEGER PRIMARY KEY,
  detected_at INTEGER NOT NULL,   -- unix epoch seconds (was text ISO 8601 w/ nanoseconds)
  confidence  REAL NOT NULL,
  label_id    INTEGER NOT NULL,   -- FK → labels.id
  model_id    INTEGER NOT NULL,   -- FK → ai_models.id
  latitude    REAL,
  longitude   REAL,
  ...
)

labels (
  id              INTEGER PRIMARY KEY,
  scientific_name TEXT NOT NULL,  -- replaces common_name from old notes
  model_id        INTEGER NOT NULL,
  label_type_id   INTEGER NOT NULL,  -- FK → label_types.id  (1=species, 2=noise, ...)
  ...
)

label_types (id INTEGER, name TEXT)  -- 1=species, 2=noise, 3=environment, 4=device

daily_events (
  id               INTEGER PRIMARY KEY,
  date             TEXT,       -- YYYY-MM-DD
  sunrise          INTEGER,    -- unix epoch seconds (0 = not set)
  sunset           INTEGER,
  country          TEXT,       -- new column (not used by this project)
  city_name        TEXT,       -- new column (not used by this project)
  moon_phase       REAL,       -- new column (not used by this project)
  moon_illumination REAL       -- new column (not used by this project)
)
```

Old schema that no longer exists:

```sql
notes (
  date        TEXT,        -- YYYY-MM-DD
  begin_time  TEXT,        -- ISO 8601 with nanosecond precision e.g. '2026-02-16 09:33:43.731828028-08:00'
  common_name TEXT,
  confidence  REAL,
  latitude    REAL,
  longitude   REAL,
  ...
)
```

## What is broken

### 1. `scripts/dawn_chorus_hourly.py` — broken, crashes immediately

Queries the deleted `notes` table in two places:

```python
# Line 63 — fails with OperationalError: no such table: notes
days = [r[0] for r in con.execute("SELECT DISTINCT date FROM notes ORDER BY date")]

# Line 108 — would also fail
for (bt,) in con.execute(
    "SELECT begin_time FROM notes WHERE date = ? AND begin_time IS NOT NULL", (day,)
):
```

Also uses `parse_begin_time()` / `_TS_RE` regex to strip nanoseconds from text timestamps —
no longer needed because `detected_at` is an integer.

**Error observed:**
```
OperationalError: no such table: notes
```

## What was already fixed (commit 3f2874b)

- `src/birdnet_analytics/db.py`: `guess_lat_lon` reads from `detections` instead of `notes`
- `src/birdnet_analytics/web.py`: all queries rewritten to use `detections JOIN labels`; text
  timestamp parsing removed; `datetime.fromtimestamp(ts, tz=tz)` used for local time

All web API endpoints confirmed working against the new sample DB:
- `/api/dawn/hourly`
- `/api/dawn/by_day`
- `/api/dayparts/daily`
- `/api/activity/hourly_stats`
- `/api/topshare/daily`
- `/api/wow`
- `/api/species/search`

## Fix plan

### Fix 1 (required): Rewrite `scripts/dawn_chorus_hourly.py`

Replace the `notes` queries with `detections`-based equivalents and remove the text-timestamp
parsing code.

**Old → new mapping:**

| Old (notes) | New (detections) |
|---|---|
| `SELECT DISTINCT date FROM notes ORDER BY date` | `SELECT DISTINCT date(detected_at, 'unixepoch') FROM detections ORDER BY 1` |
| `SELECT begin_time FROM notes WHERE date = ?` | `SELECT detected_at FROM detections WHERE date(detected_at, 'unixepoch') = ?` |
| `parse_begin_time(bt).astimezone(tz)` | `datetime.fromtimestamp(bt, tz=tz)` |

Full rewrite of `main()` body:

```python
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("--tz", default="America/Los_Angeles")
    ap.add_argument("--before-min", type=int, default=90)
    ap.add_argument("--after-min", type=int, default=150)
    args = ap.parse_args()

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(args.tz)

    db = BirdnetDb(args.db)
    before = timedelta(minutes=args.before_min)
    after = timedelta(minutes=args.after_min)

    with db.connect_ro() as con:
        lat, lon = guess_lat_lon(con)

        days = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT date(detected_at, 'unixepoch') FROM detections ORDER BY 1"
            )
        ]

        print("date\thour_local\tdetections")
        for day in days:
            sunrise_int = _daily_events_sunrise(con, day)
            if sunrise_int is not None:
                from datetime import timezone
                sunrise_dt = datetime.fromtimestamp(sunrise_int, tz=timezone.utc).astimezone(tz)
            else:
                sun_times = compute_sun_times(
                    on_date=datetime.fromisoformat(day).date(),
                    latitude=lat,
                    longitude=lon,
                    tz_name=args.tz,
                )
                sunrise_dt = sun_times.sunrise

            start, end = dawn_window(sunrise=sunrise_dt, before=before, after=after)

            buckets: dict[int, int] = {}
            for (ts,) in con.execute(
                "SELECT detected_at FROM detections WHERE date(detected_at, 'unixepoch') = ?",
                (day,),
            ):
                dt = datetime.fromtimestamp(ts, tz=tz)
                if start <= dt < end:
                    buckets[dt.hour] = buckets.get(dt.hour, 0) + 1

            for hour in sorted(buckets):
                print(f"{day}\t{hour:02d}\t{buckets[hour]}")
```

Also remove the now-unused `parse_begin_time` inner function and `import re` inside the loop.

### Fix 2 (cleanup): Remove unused dependencies from `pyproject.toml`

`sqlalchemy` and `pandas` are listed as core dependencies but are not imported anywhere in the
project. Removing them speeds up `uv sync` and shrinks the venv (~80 MB).

```toml
# Remove these lines from [project].dependencies:
"pandas>=2.2",
"sqlalchemy>=2.0",
```

### Fix 3 (packaging): Add `[build-system]` to `pyproject.toml`

Without a `[build-system]` table the package is not properly installable with `pip install -e .`.
Currently the workaround is `PYTHONPATH=src` or `uvicorn --app-dir src`. Adding the section makes
the package installable in the standard way.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/birdnet_analytics"]
```

(Or use `flit-core` if hatchling is not preferred.)

### Fix 4 (docs): Update stale documentation

- `docs/STATUS.md`: still references `notes` table and pre-migration schema. Update to reflect
  current state (new schema operational, scripts status).
- `docs/LESSONS.md` lesson #5: references nanosecond text timestamps. Replace with a note about
  integer unix-second `detected_at` and `datetime.fromtimestamp()`.

## Future considerations (not blocking)

- **`label_type_id` filtering**: The `labels` table has a `label_type_id` column distinguishing
  species (1) from noise/environment/device labels. The current DB only contains species labels,
  but future birdnet-go versions may add noise detections. Consider filtering
  `WHERE l.label_type_id = 1` in species-facing queries to future-proof.

- **`hourly_stats` performance**: `api_activity_hourly_stats` iterates one DB query per day.
  For a 3-month dataset (~95 days) this is fine, but it will degrade linearly. A single grouped
  query (or a pre-materialized rollup table) would scale better.

- **Common name display**: Species are identified by `scientific_name` only. birdnet-go does not
  store common names in the DB. If human-readable names are desired, a lookup table (e.g. from
  the BirdNET label files) would need to be bundled separately.

## Verification steps

After applying the fixes:

```bash
# 1. Confirm script runs against new DB
PYTHONPATH=src python scripts/dawn_chorus_hourly.py _data/sample/birdnet.db | head

# 2. Confirm web app starts and all endpoints return data
BIRDNET_DB_PATH=_data/sample/birdnet.db BIRDNET_ANALYTICS_TZ=America/Los_Angeles \
  uv run uvicorn --app-dir src birdnet_analytics.web:app --port 8787
curl http://localhost:8787/api/wow?weeks=4
curl http://localhost:8787/api/species/search?limit=5
```
