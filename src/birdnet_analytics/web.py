from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import re
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

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


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="birdnet-analytics", root_path=settings.root_path)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/dawn/hourly")
    def api_dawn_hourly(
        day: str = Query(default_factory=lambda: date.today().isoformat(), description="YYYY-MM-DD"),
        tz_name: str = Query(default="America/Los_Angeles"),
        before_min: int = 90,
        after_min: int = 150,
    ):
        tz = ZoneInfo(tz_name)
        db_path = _resolve_birdnet_db_path()
        db = BirdnetDb(db_path)
        with db.connect_ro() as con:
            rows = _dawn_hourly_for_day(con=con, day=day, tz=tz, before_min=before_min, after_min=after_min)
        return {"date": day, "tz": tz_name, "before_min": before_min, "after_min": after_min, "rows": rows}

    @app.get("/", response_class=HTMLResponse)
    def index():
        # Minimal single-file HTML (public-ish safe; does not show raw lat/lon)
        return HTMLResponse(
            _INDEX_HTML,
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
  <div class=\"muted\">Dawn chorus detections/hour (BirdNET-GO). v0.</div>

  <div class=\"card\">
    <div class=\"row\">
      <div>
        <label for=\"day\">Date</label>
        <input id=\"day\" type=\"date\" />
      </div>
      <div>
        <label for=\"tz\">Timezone</label>
        <input id=\"tz\" type=\"text\" value=\"America/Los_Angeles\" />
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
      <canvas id=\"chart\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw\"></pre>
    </details>
  </div>

<script>
  const dayInput = document.getElementById('day');
  const tzInput = document.getElementById('tz');
  const beforeInput = document.getElementById('before');
  const afterInput = document.getElementById('after');
  const rawPre = document.getElementById('raw');
  const runBtn = document.getElementById('run');

  // default date = today
  const today = new Date();
  dayInput.value = today.toISOString().slice(0,10);

  let chart;

  async function run() {
    const day = dayInput.value;
    const tz = tzInput.value;
    const before = beforeInput.value;
    const after = afterInput.value;

    const url = `/api/dawn/hourly?day=${encodeURIComponent(day)}&tz_name=${encodeURIComponent(tz)}&before_min=${encodeURIComponent(before)}&after_min=${encodeURIComponent(after)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawPre.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => String(r.hour).padStart(2,'0') + ':00');
    const values = data.rows.map(r => r.detections);

    const ctx = document.getElementById('chart');
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
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

  runBtn.addEventListener('click', run);
  run();
</script>
</body>
</html>"""


app = create_app()
