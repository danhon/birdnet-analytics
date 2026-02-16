# birdnet-analytics

Analytics + reporting for BirdNET-GO (nightly-20260118).

Goals:
- Use BirdNET-GO’s existing SQLite database as the source of truth.
- Optionally ingest BirdNET-GO SSE events for realtime dashboards/alerts.
- Produce reproducible rollups (hourly/daily species counts, trends, QA metrics).

## Project layout
- `src/birdnet_analytics/` — library code
- `scripts/` — runnable entrypoints (ETL, backfills, reports)
- `configs/` — deployment/runtime configs (paths, DB locations)
- `docs/` — notes

## Dev / install (uv)
We use **uv** for Python env + dependency management.

```sh
cd birdnet-analytics
uv venv
uv sync
```

Run a script:
```sh
uv run python scripts/print_schema.py /path/to/birdnet.db
```

## Next steps
1. Confirm BirdNET-GO SQLite DB path on **UBUNTUPLEX**.
2. Capture schema (`.tables`, `.schema`).
3. Decide: analytics in separate SQLite vs DuckDB/Postgres.
