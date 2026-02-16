from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import re
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from birdnet_analytics.config import load_settings
from birdnet_analytics.db import BirdnetDb, guess_lat_lon
from birdnet_analytics.sun import compute_sun_times, dawn_window


def _resolve_birdnet_db_path() -> Path:
    s = load_settings()
    if s.db_path:
        return s.db_path
    if s.db_dir:
        p = s.db_dir / "birdnet.db"
        if p.exists():
            return p
    raise RuntimeError("Set BIRDNET_DB_PATH or BIRDNET_DB_DIR (containing birdnet.db)")


_TS_RE = re.compile(r"\.(\d{6})\d+")


def _parse_begin_time(s: str) -> datetime:
    # Example in DB: '2026-02-16 09:33:43.731828028-08:00'
    s = s.replace(" ", "T", 1)
    s = _TS_RE.sub(r".\1", s)  # truncate to microseconds
    return datetime.fromisoformat(s)


def _dawn_hourly_for_day(
    *, con, day: str, tz: ZoneInfo, before_min: int, after_min: int
) -> list[dict]:
    lat, lon = guess_lat_lon(con)
    sun_times = compute_sun_times(on_date=date.fromisoformat(day), latitude=lat, longitude=lon, tz_name=str(tz.key))
    start, end = dawn_window(sunrise=sun_times.sunrise, before=timedelta(minutes=before_min), after=timedelta(minutes=after_min))

    buckets: dict[int, int] = {}
    for (bt,) in con.execute(
        "SELECT begin_time FROM notes WHERE date = ? AND begin_time IS NOT NULL", (day,)
    ):
        dt = _parse_begin_time(bt).astimezone(tz)
        if start <= dt < end:
            buckets[dt.hour] = buckets.get(dt.hour, 0) + 1

    return [{"hour": h, "detections": buckets[h]} for h in sorted(buckets)]


def _dayparts() -> list[tuple[str, int, int]]:
    # Fixed clock-time dayparts (local time), as requested.
    return [
        ("00-06", 0, 6),
        ("06-12", 6, 12),
        ("12-18", 12, 18),
        ("18-24", 18, 24),
    ]


def _parse_days_param(days: str) -> int | None:
    days = (days or "").strip().lower()
    if days in ("all", "*", "0", "none"):
        return None
    return int(days)


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="birdnet-analytics", root_path=settings.root_path)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/dawn/hourly")
    def api_dawn_hourly(
        day: str = Query(default_factory=lambda: date.today().isoformat(), description="YYYY-MM-DD"),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
        before_min: int = 90,
        after_min: int = 150,
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        db_path = _resolve_birdnet_db_path()
        db = BirdnetDb(db_path)
        with db.connect_ro() as con:
            rows = _dawn_hourly_for_day(con=con, day=day, tz=tz, before_min=before_min, after_min=after_min)
        return {"date": day, "tz": tz_name_eff, "before_min": before_min, "after_min": after_min, "rows": rows}

    @app.get("/api/dayparts/daily")
    def api_dayparts_daily(
        days: str = Query(default="30", description="Number of days back (e.g. 30) or 'all'"),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        limit_days = _parse_days_param(days)

        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
            # Determine date range from notes.
            min_day, max_day = con.execute("SELECT min(date), max(date) FROM notes").fetchone()
            if not min_day or not max_day:
                return {"tz": tz_name_eff, "rows": []}

            end_day = date.fromisoformat(max_day)
            if limit_days is None:
                start_day = date.fromisoformat(min_day)
            else:
                start_day = end_day - timedelta(days=limit_days - 1)
                min_possible = date.fromisoformat(min_day)
                if start_day < min_possible:
                    start_day = min_possible

            parts = _dayparts()
            rows_out: list[dict] = []

            # Iterate per day, bucket notes.begin_time into fixed local-time dayparts.
            cur_day = start_day
            while cur_day <= end_day:
                day_s = cur_day.isoformat()
                buckets = {name: 0 for (name, _, _) in parts}

                for (bt,) in con.execute(
                    "SELECT begin_time FROM notes WHERE date = ? AND begin_time IS NOT NULL", (day_s,)
                ):
                    dt = _parse_begin_time(bt).astimezone(tz)
                    h = dt.hour
                    for name, h0, h1 in parts:
                        if h0 <= h < h1:
                            buckets[name] += 1
                            break

                row = {"date": day_s}
                row.update(buckets)
                rows_out.append(row)
                cur_day += timedelta(days=1)

        return {"tz": tz_name_eff, "parts": [p[0] for p in _dayparts()], "rows": rows_out}

    @app.get("/api/species/activity")
    def api_species_activity(
        name: str = Query(..., description="Common name (notes.common_name)"),
        days: str = Query(default="all", description="Number of days back (e.g. 30) or 'all'"),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        limit_days = _parse_days_param(days)

        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
            min_day, max_day = con.execute(
                "SELECT min(date), max(date) FROM notes WHERE common_name = ?", (name,)
            ).fetchone()
            if not min_day or not max_day:
                return {"name": name, "tz": tz_name_eff, "rows": []}

            end_day = date.fromisoformat(max_day)
            if limit_days is None:
                start_day = date.fromisoformat(min_day)
            else:
                start_day = end_day - timedelta(days=limit_days - 1)
                min_possible = date.fromisoformat(min_day)
                if start_day < min_possible:
                    start_day = min_possible

            buckets = {h: 0 for h in range(24)}

            for (bt,) in con.execute(
                """
                SELECT begin_time FROM notes
                WHERE common_name = ?
                  AND date >= ? AND date <= ?
                  AND begin_time IS NOT NULL
                """,
                (name, start_day.isoformat(), end_day.isoformat()),
            ):
                dt = _parse_begin_time(bt).astimezone(tz)
                buckets[dt.hour] += 1

        return {
            "name": name,
            "tz": tz_name_eff,
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "rows": [{"hour": h, "detections": buckets[h]} for h in range(24)],
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        # Minimal single-file HTML (public-ish safe; does not show raw lat/lon)
        return HTMLResponse(
            _INDEX_HTML.replace("__DEFAULT_TZ__", settings.tz_name),
            headers={"Cache-Control": "no-store"},
        )

    return app


_INDEX_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>birdnet-analytics</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; max-width: 980px; }
    h1 { margin: 0 0 12px 0; }
    .row { display: flex; gap: 16px; align-items: end; flex-wrap: wrap; }
    label { display: block; font-size: 12px; color: #444; margin-bottom: 6px; }
    input, select { padding: 8px; font-size: 14px; }
    button { padding: 9px 12px; font-size: 14px; cursor: pointer; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-top: 16px; }
    .muted { color: #666; font-size: 13px; }
    canvas { width: 100%; max-height: 360px; }
    pre { background: #f6f6f6; padding: 12px; border-radius: 8px; overflow: auto; }
  </style>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js\"></script>
</head>
<body>
  <h1>birdnet-analytics</h1>
  <div class=\"muted\">BirdNET-GO analytics. v0.</div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Dawn chorus (detections/hour)</h2>
    <div class=\"row\">
      <div>
        <label for=\"day\">Date</label>
        <input id=\"day\" type=\"date\" />
      </div>
      <div>
        <label for=\"tz\">Timezone</label>
        <input id=\"tz\" type=\"text\" value=\"__DEFAULT_TZ__\" />
      </div>
      <div>
        <label for=\"before\">Minutes before sunrise</label>
        <input id=\"before\" type=\"number\" value=\"90\" min=\"0\" />
      </div>
      <div>
        <label for=\"after\">Minutes after sunrise</label>
        <input id=\"after\" type=\"number\" value=\"150\" min=\"0\" />
      </div>
      <div>
        <button id=\"run\">Run</button>
      </div>
    </div>

    <div style=\"margin-top: 16px\">
      <canvas id=\"chart_dawn\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_dawn\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Day parts (fixed: 00-06, 06-12, 12-18, 18-24)</h2>
    <div class=\"row\">
      <div>
        <label for=\"dayparts_days\">Days</label>
        <input id=\"dayparts_days\" type=\"number\" value=\"30\" min=\"1\" />
      </div>
      <div>
        <button id=\"run_dayparts\">Run</button>
      </div>
    </div>

    <div style=\"margin-top: 16px\">
      <canvas id=\"chart_dayparts\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_dayparts\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Species activity (detections by hour of day)</h2>
    <div class=\"row\">
      <div style=\"min-width: 280px\">
        <label for=\"species\">Common name (exact match)</label>
        <input id=\"species\" type=\"text\" placeholder=\"Song Sparrow\" />
      </div>
      <div>
        <label for=\"species_days\">Days</label>
        <input id=\"species_days\" type=\"text\" value=\"all\" />
      </div>
      <div>
        <button id=\"run_species\">Run</button>
      </div>
    </div>

    <div style=\"margin-top: 16px\">
      <canvas id=\"chart_species\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_species\"></pre>
    </details>
  </div>

<script>
  const dayInput = document.getElementById('day');
  const tzInput = document.getElementById('tz');
  const beforeInput = document.getElementById('before');
  const afterInput = document.getElementById('after');
  const rawDawn = document.getElementById('raw_dawn');
  const rawDayparts = document.getElementById('raw_dayparts');
  const rawSpecies = document.getElementById('raw_species');

  const runBtn = document.getElementById('run');
  const runDaypartsBtn = document.getElementById('run_dayparts');
  const runSpeciesBtn = document.getElementById('run_species');

  const daypartsDays = document.getElementById('dayparts_days');
  const speciesInput = document.getElementById('species');
  const speciesDays = document.getElementById('species_days');

  // default date = today
  const today = new Date();
  dayInput.value = today.toISOString().slice(0,10);

  let chartDawn;
  let chartDayparts;
  let chartSpecies;

  async function runDawn() {
    const day = dayInput.value;
    const tz = tzInput.value;
    const before = beforeInput.value;
    const after = afterInput.value;

    const url = `/api/dawn/hourly?day=${encodeURIComponent(day)}&tz_name=${encodeURIComponent(tz)}&before_min=${encodeURIComponent(before)}&after_min=${encodeURIComponent(after)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawDawn.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => String(r.hour).padStart(2,'0') + ':00');
    const values = data.rows.map(r => r.detections);

    const ctx = document.getElementById('chart_dawn');
    if (chartDawn) chartDawn.destroy();
    chartDawn = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Detections',
          data: values,
          backgroundColor: 'rgba(54, 162, 235, 0.6)',
          borderColor: 'rgba(54, 162, 235, 1)',
          borderWidth: 1,
        }]
      },
      options: {
        responsive: true,
        scales: {
          y: { beginAtZero: true }
        }
      }
    });
  }

  async function runDayparts() {
    const tz = tzInput.value;
    const days = daypartsDays.value;
    const url = `/api/dayparts/daily?days=${encodeURIComponent(days)}&tz_name=${encodeURIComponent(tz)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawDayparts.textContent = JSON.stringify(data, null, 2);

    const parts = data.parts;
    const labels = data.rows.map(r => r.date);
    const datasets = [
      { key: parts[0], color: 'rgba(75, 192, 192, 0.65)' },
      { key: parts[1], color: 'rgba(255, 159, 64, 0.65)' },
      { key: parts[2], color: 'rgba(153, 102, 255, 0.65)' },
      { key: parts[3], color: 'rgba(255, 99, 132, 0.65)' },
    ].map(({key, color}) => ({
      label: key,
      data: data.rows.map(r => r[key] ?? 0),
      backgroundColor: color,
      borderWidth: 0,
      stack: 'stack1',
    }));

    const ctx = document.getElementById('chart_dayparts');
    if (chartDayparts) chartDayparts.destroy();
    chartDayparts = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        scales: {
          x: { stacked: true },
          y: { stacked: true, beginAtZero: true }
        }
      }
    });
  }

  async function runSpecies() {
    const tz = tzInput.value;
    const name = speciesInput.value.trim();
    const days = speciesDays.value.trim() || 'all';
    if (!name) {
      alert('Enter a species common name (exact match from BirdNET-GO).');
      return;
    }

    const url = `/api/species/activity?name=${encodeURIComponent(name)}&days=${encodeURIComponent(days)}&tz_name=${encodeURIComponent(tz)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawSpecies.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => String(r.hour).padStart(2,'0') + ':00');
    const values = data.rows.map(r => r.detections);

    const ctx = document.getElementById('chart_species');
    if (chartSpecies) chartSpecies.destroy();
    chartSpecies = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Detections',
          data: values,
          backgroundColor: 'rgba(99, 102, 241, 0.6)',
          borderColor: 'rgba(99, 102, 241, 1)',
          borderWidth: 1,
        }]
      },
      options: {
        responsive: true,
        scales: {
          y: { beginAtZero: true }
        }
      }
    });
  }

  runBtn.addEventListener('click', runDawn);
  runDaypartsBtn.addEventListener('click', runDayparts);
  runSpeciesBtn.addEventListener('click', runSpecies);

  runDawn();
  runDayparts();
</script>
</body>
</html>"""


app = create_app()
