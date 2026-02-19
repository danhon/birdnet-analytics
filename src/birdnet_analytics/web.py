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
from birdnet_analytics.weather import (
    open_cache,
    get_cached_range,
    upsert_many,
    fetch_open_meteo_daily_precip_mm,
)


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


def _dawn_buckets_for_day(
    *,
    con,
    day: str,
    tz: ZoneInfo,
    before_min: int,
    after_min: int,
    bucket_min: int,
    min_confidence: float,
) -> list[dict]:
    if bucket_min <= 0 or bucket_min > 60:
        raise ValueError("bucket_min must be between 1 and 60")

    lat, lon = guess_lat_lon(con)
    sun_times = compute_sun_times(
        on_date=date.fromisoformat(day), latitude=lat, longitude=lon, tz_name=str(tz.key)
    )
    start, end = dawn_window(
        sunrise=sun_times.sunrise,
        before=timedelta(minutes=before_min),
        after=timedelta(minutes=after_min),
    )

    # Bucket by clock time (e.g. 15-minute increments): 06:00, 06:15, 06:30, ...
    buckets: dict[datetime, int] = {}
    for (bt, conf) in con.execute(
        "SELECT begin_time, confidence FROM notes WHERE date = ? AND begin_time IS NOT NULL",
        (day,),
    ):
        if conf is None or float(conf) < min_confidence:
            continue
        dt = _parse_begin_time(bt).astimezone(tz)
        if not (start <= dt < end):
            continue

        minute = (dt.minute // bucket_min) * bucket_min
        bdt = dt.replace(minute=minute, second=0, microsecond=0)
        buckets[bdt] = buckets.get(bdt, 0) + 1

    return [
        {"time": bdt.strftime("%H:%M"), "detections": buckets[bdt]}
        for bdt in sorted(buckets)
    ]


def _dawn_bucket_offsets_for_day(
    *,
    con,
    day: str,
    tz: ZoneInfo,
    before_min: int,
    after_min: int,
    bucket_min: int,
    min_confidence: float,
) -> dict[int, int]:
    """Return counts keyed by bucket index relative to the dawn window start.

    This is the right shape for a per-day stacked chart where each stack segment is a
    15-minute slice within the (before_min/after_min) window.
    """

    if bucket_min <= 0 or bucket_min > 60:
        raise ValueError("bucket_min must be between 1 and 60")

    lat, lon = guess_lat_lon(con)
    sun_times = compute_sun_times(
        on_date=date.fromisoformat(day), latitude=lat, longitude=lon, tz_name=str(tz.key)
    )
    start, end = dawn_window(
        sunrise=sun_times.sunrise,
        before=timedelta(minutes=before_min),
        after=timedelta(minutes=after_min),
    )

    buckets: dict[int, int] = {}
    for (bt, conf) in con.execute(
        "SELECT begin_time, confidence FROM notes WHERE date = ? AND begin_time IS NOT NULL",
        (day,),
    ):
        if conf is None or float(conf) < min_confidence:
            continue
        dt = _parse_begin_time(bt).astimezone(tz)
        if not (start <= dt < end):
            continue

        idx = int((dt - start).total_seconds() // (bucket_min * 60))
        buckets[idx] = buckets.get(idx, 0) + 1

    return buckets


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
        bucket_min: int = Query(default=15, ge=1, le=60, description="Bucket size in minutes"),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        db_path = _resolve_birdnet_db_path()
        db = BirdnetDb(db_path)
        with db.connect_ro() as con:
            rows = _dawn_buckets_for_day(
                con=con,
                day=day,
                tz=tz,
                before_min=before_min,
                after_min=after_min,
                bucket_min=bucket_min,
                min_confidence=min_confidence,
            )
        return {
            "date": day,
            "tz": tz_name_eff,
            "before_min": before_min,
            "after_min": after_min,
            "bucket_min": bucket_min,
            "min_confidence": min_confidence,
            "rows": rows,
        }

    @app.get("/api/dawn/by_day")
    def api_dawn_by_day(
        days: str = Query(default="30", description="Number of days back (e.g. 30) or 'all'"),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
        before_min: int = 90,
        after_min: int = 150,
        bucket_min: int = Query(default=15, ge=1, le=60, description="Bucket size in minutes"),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        limit_days = _parse_days_param(days)

        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
            min_day, max_day = con.execute("SELECT min(date), max(date) FROM notes").fetchone()
            if not min_day or not max_day:
                return {"tz": tz_name_eff, "rows": [], "bucket_labels": [], "days": []}

            end_day = date.fromisoformat(max_day)
            if limit_days is None:
                start_day = date.fromisoformat(min_day)
            else:
                start_day = end_day - timedelta(days=limit_days - 1)
                min_possible = date.fromisoformat(min_day)
                if start_day < min_possible:
                    start_day = min_possible

            # Define the bucket labels as offsets from sunrise, so different sunrise clock
            # times still line up for comparison.
            n_buckets = int(((before_min + after_min) + bucket_min - 1) // bucket_min)
            bucket_labels = []
            for i in range(n_buckets):
                offset = -before_min + (i * bucket_min)
                sign = "+" if offset > 0 else ""
                bucket_labels.append(f"{sign}{offset}m")

            days_list: list[str] = [
                (start_day + timedelta(days=i)).isoformat()
                for i in range((end_day - start_day).days + 1)
            ]

            # Build a per-day row: {day, buckets:[...]} where buckets align with bucket_labels.
            rows_out: list[dict] = []
            for d in days_list:
                counts = _dawn_bucket_offsets_for_day(
                    con=con,
                    day=d,
                    tz=tz,
                    before_min=before_min,
                    after_min=after_min,
                    bucket_min=bucket_min,
                    min_confidence=min_confidence,
                )
                buckets = [counts.get(i, 0) for i in range(n_buckets)]
                rows_out.append({"day": d, "buckets": buckets, "total": sum(buckets)})

        return {
            "tz": tz_name_eff,
            "days": days_list,
            "before_min": before_min,
            "after_min": after_min,
            "bucket_min": bucket_min,
            "bucket_labels": bucket_labels,
            "min_confidence": min_confidence,
            "rows": rows_out,
        }

    @app.get("/api/dayparts/daily")
    def api_dayparts_daily(
        days: str = Query(default="30", description="Number of days back (e.g. 30) or 'all'"),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
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

            # Weather: daily precipitation totals (mm), cached locally.
            # We fetch for the same date window we return.
            cache_path = Path(os.getenv("BIRDNET_ANALYTICS_WEATHER_CACHE", "_data/weather/weather.sqlite"))
            with open_cache(cache_path) as wcon:
                cached = get_cached_range(wcon, start_day, end_day)
                missing_days = [
                    start_day + timedelta(days=i)
                    for i in range((end_day - start_day).days + 1)
                    if (start_day + timedelta(days=i)) not in cached
                ]
                if missing_days:
                    lat, lon = guess_lat_lon(con)
                    fetched = fetch_open_meteo_daily_precip_mm(
                        latitude=lat,
                        longitude=lon,
                        start=missing_days[0],
                        end=missing_days[-1],
                        tz_name=tz_name_eff,
                    )
                    if fetched:
                        upsert_many(wcon, fetched)
                        cached.update(get_cached_range(wcon, start_day, end_day))

            # Iterate per day, bucket notes.begin_time into fixed local-time dayparts.
            cur_day = start_day
            while cur_day <= end_day:
                day_s = cur_day.isoformat()
                buckets = {name: 0 for (name, _, _) in parts}
                uniq: set[str] = set()

                for (bt, conf, sci) in con.execute(
                    "SELECT begin_time, confidence, scientific_name FROM notes WHERE date = ? AND begin_time IS NOT NULL",
                    (day_s,),
                ):
                    if conf is None or float(conf) < min_confidence:
                        continue
                    if sci:
                        uniq.add(str(sci))
                    dt = _parse_begin_time(bt).astimezone(tz)
                    h = dt.hour
                    for name, h0, h1 in parts:
                        if h0 <= h < h1:
                            buckets[name] += 1
                            break

                row = {
                    "date": day_s,
                    "unique_species": len(uniq),
                    "precip_mm": float(cached.get(cur_day, 0.0)),
                }
                row.update(buckets)
                rows_out.append(row)
                cur_day += timedelta(days=1)

        return {
            "tz": tz_name_eff,
            "min_confidence": min_confidence,
            "parts": [p[0] for p in _dayparts()],
            "rows": rows_out,
        }

    @app.get("/api/species/search")
    def api_species_search(
        q: str = Query(default="", description="Query substring"),
        limit: int = Query(default=20, ge=1, le=200),
    ):
        q = (q or "").strip()
        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
            if q == "":
                rows = con.execute(
                    """
                    SELECT common_name, COUNT(*) AS c
                    FROM notes
                    WHERE common_name IS NOT NULL AND common_name != ''
                    GROUP BY common_name
                    ORDER BY c DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                like = f"%{q}%"
                rows = con.execute(
                    """
                    SELECT common_name, COUNT(*) AS c
                    FROM notes
                    WHERE common_name LIKE ?
                    GROUP BY common_name
                    ORDER BY c DESC
                    LIMIT ?
                    """,
                    (like, limit),
                ).fetchall()

        return {"q": q, "limit": limit, "rows": [{"name": n, "count": c} for (n, c) in rows]}

    def _hourly_buckets(*, con, tz: ZoneInfo, start_day: date, end_day: date, species: str, min_confidence: float):
        buckets = {h: 0 for h in range(24)}
        if species:
            q = (
                "SELECT begin_time, confidence FROM notes "
                "WHERE common_name = ? AND date >= ? AND date <= ? AND begin_time IS NOT NULL"
            )
            params = (species, start_day.isoformat(), end_day.isoformat())
        else:
            q = (
                "SELECT begin_time, confidence FROM notes "
                "WHERE date >= ? AND date <= ? AND begin_time IS NOT NULL"
            )
            params = (start_day.isoformat(), end_day.isoformat())

        for (bt, conf) in con.execute(q, params):
            if conf is None or float(conf) < min_confidence:
                continue
            dt = _parse_begin_time(bt).astimezone(tz)
            buckets[dt.hour] += 1
        return buckets

    @app.get("/api/activity/hourly_stats")
    def api_activity_hourly_stats(
        species: str = Query(default="", description="Optional common name filter"),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        mode: str = Query(default="pct", description="pct|minmax"),
    ):
        """Hourly activity stats across days: min/max/mean per hour + today's per-hour counts.

        - min/max/mean are computed over DAILY counts for each hour-of-day.
        - today is the latest `notes.date` in the DB.
        """
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        species = (species or "").strip()

        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
            if species:
                min_day, max_day = con.execute(
                    "SELECT min(date), max(date) FROM notes WHERE common_name = ?", (species,)
                ).fetchone()
            else:
                min_day, max_day = con.execute("SELECT min(date), max(date) FROM notes").fetchone()

            if not min_day or not max_day:
                return {"species": species or None, "tz": tz_name_eff, "rows": []}

            start_day = date.fromisoformat(min_day)
            end_day = date.fromisoformat(max_day)
            today_day = end_day

            # Per-hour list of daily counts so we can compute percentiles.
            values_by_hour: dict[int, list[int]] = {h: [] for h in range(24)}
            mins = {h: None for h in range(24)}
            maxs = {h: 0 for h in range(24)}
            sums = {h: 0 for h in range(24)}
            nonzero_days = {h: 0 for h in range(24)}
            day_count = 0

            cur = start_day
            while cur <= end_day:
                buckets = _hourly_buckets(
                    con=con,
                    tz=tz,
                    start_day=cur,
                    end_day=cur,
                    species=species,
                    min_confidence=min_confidence,
                )
                day_count += 1
                for h in range(24):
                    v = int(buckets[h])
                    values_by_hour[h].append(v)
                    if mins[h] is None or v < mins[h]:
                        mins[h] = v
                    if v > maxs[h]:
                        maxs[h] = v
                    sums[h] += v
                    if v > 0:
                        nonzero_days[h] += 1
                cur += timedelta(days=1)

            today_buckets = _hourly_buckets(
                con=con,
                tz=tz,
                start_day=today_day,
                end_day=today_day,
                species=species,
                min_confidence=min_confidence,
            )

        def percentile(sorted_vals: list[int], p: float) -> float:
            if not sorted_vals:
                return 0.0
            # nearest-rank
            k = int(round((p / 100.0) * (len(sorted_vals) - 1)))
            k = max(0, min(len(sorted_vals) - 1, k))
            return float(sorted_vals[k])

        rows = []
        for h in range(24):
            mean = (sums[h] / day_count) if day_count else 0.0
            vals = sorted(values_by_hour[h])
            p10 = percentile(vals, 10)
            p50 = percentile(vals, 50)
            p90 = percentile(vals, 90)
            active_rate = (nonzero_days[h] / day_count) if day_count else 0.0
            rows.append(
                {
                    "hour": h,
                    # keep min/max for rollback
                    "min": int(mins[h] or 0),
                    "max": int(maxs[h]),
                    "mean": float(mean),
                    # new percentiles
                    "p10": p10,
                    "p50": p50,
                    "p90": p90,
                    "active_rate": active_rate,
                    # today
                    "today": int(today_buckets[h]),
                }
            )

        mode_eff = (mode or "pct").strip().lower()
        if mode_eff not in ("pct", "minmax"):
            mode_eff = "pct"

        return {
            "species": species or None,
            "tz": tz_name_eff,
            "min_confidence": min_confidence,
            "mode": mode_eff,
            "all": {"start": start_day.isoformat(), "end": end_day.isoformat(), "days": day_count},
            "today": {"date": today_day.isoformat()},
            "rows": rows,
        }

    @app.get("/api/topshare/daily")
    def api_topshare_daily(
        days: str = Query(default="30", description="Number of days back (e.g. 30) or 'all'"),
        top_k: int = Query(default=3, ge=1, le=10),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)
        limit_days = _parse_days_param(days)

        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
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

            rows_out: list[dict] = []
            cur_day = start_day
            while cur_day <= end_day:
                day_s = cur_day.isoformat()

                # Count detections by species for that day.
                counts: dict[str, int] = {}
                total = 0
                for bt, conf, name in con.execute(
                    """
                    SELECT begin_time, confidence, common_name
                    FROM notes
                    WHERE date = ? AND begin_time IS NOT NULL
                    """,
                    (day_s,),
                ):
                    if conf is None or float(conf) < min_confidence:
                        continue
                    total += 1
                    if name:
                        counts[str(name)] = counts.get(str(name), 0) + 1

                if total == 0:
                    rows_out.append({"date": day_s, "total": 0, "top": [], "other": 0})
                    cur_day += timedelta(days=1)
                    continue

                top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
                top_total = sum(c for _, c in top)
                other = total - top_total
                rows_out.append(
                    {
                        "date": day_s,
                        "total": total,
                        "top": [{"name": n, "count": c, "share": c / total} for (n, c) in top],
                        "other": other,
                        "other_share": other / total,
                    }
                )
                cur_day += timedelta(days=1)

        return {"tz": tz_name_eff, "min_confidence": min_confidence, "top_k": top_k, "rows": rows_out}

    @app.get("/api/wow")
    def api_wow(
        weeks: int = Query(default=8, ge=2, le=104),
        tz_name: str = Query(default="", description="IANA tz name (blank = config default)"),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    ):
        tz_name_eff = tz_name.strip() or settings.tz_name
        tz = ZoneInfo(tz_name_eff)

        db = BirdnetDb(_resolve_birdnet_db_path())
        with db.connect_ro() as con:
            # Find latest begin_time (to anchor the current week)
            row = con.execute(
                "SELECT begin_time FROM notes WHERE begin_time IS NOT NULL ORDER BY begin_time DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"tz": tz_name_eff, "rows": []}

            latest = _parse_begin_time(row[0]).astimezone(tz)
            latest_date = latest.date()
            end_week_start = latest_date - timedelta(days=latest_date.weekday())  # Monday
            start_week_start = end_week_start - timedelta(days=(weeks - 1) * 7)
            start_date = start_week_start
            end_date = end_week_start + timedelta(days=6)

            # Prepare buckets
            buckets: dict[str, dict] = {}
            week = start_week_start
            while week <= end_week_start:
                k = week.isoformat()
                buckets[k] = {"week_start": k, "detections": 0, "species": set()}
                week += timedelta(days=7)

            # Coarse filter by notes.date (assumed local date). Then parse begin_time to get local week.
            for bt, conf, sci in con.execute(
                """
                SELECT begin_time, confidence, scientific_name
                FROM notes
                WHERE begin_time IS NOT NULL
                  AND date >= ? AND date <= ?
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ):
                if conf is None or float(conf) < min_confidence:
                    continue
                dt = _parse_begin_time(bt).astimezone(tz)
                d = dt.date()
                wk = d - timedelta(days=d.weekday())
                wk_key = wk.isoformat()
                b = buckets.get(wk_key)
                if b is None:
                    continue
                b["detections"] += 1
                if sci:
                    b["species"].add(sci)

            rows = []
            for k in sorted(buckets.keys()):
                b = buckets[k]
                rows.append(
                    {
                        "week_start": b["week_start"],
                        "detections": b["detections"],
                        "unique_species": len(b["species"]),
                    }
                )

        return {"tz": tz_name_eff, "min_confidence": min_confidence, "rows": rows}

    @app.get("/", response_class=HTMLResponse)
    def index():
        # Minimal single-file HTML (public-ish safe; does not show raw lat/lon)
        return HTMLResponse(
            _INDEX_HTML.replace("__DEFAULT_TZ__", settings.tz_name)
            .replace("__REFRESH_SECONDS__", str(settings.refresh_seconds)),
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
    :root { --page-max: 980px; }
    body {
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      margin: 24px;
      max-width: var(--page-max);
    }
    h1 { margin: 0 0 12px 0; }
    h2 { font-size: 18px; }

    .row { display: flex; gap: 16px; align-items: end; flex-wrap: wrap; }
    label { display: block; font-size: 12px; color: #444; margin-bottom: 6px; }
    input, select { padding: 8px; font-size: 14px; max-width: 100%; }
    button { padding: 9px 12px; font-size: 14px; cursor: pointer; }

    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-top: 16px; }
    .muted { color: #666; font-size: 13px; }

    /* Chart container: explicit height so Chart.js can size correctly (with maintainAspectRatio=false). */
    .chartWrap { width: 100%; height: 360px; position: relative; }
    .chartWrap--short { height: 300px; }
    .chartWrap--tall { height: 540px; }

    /* Don't force canvas bitmap scaling; let Chart.js manage size. */
    canvas { display: block; width: 100%; }

    pre { background: #f6f6f6; padding: 12px; border-radius: 8px; overflow: auto; }

    /* Mobile portrait tweaks */
    @media (max-width: 520px) {
      body { margin: 12px; }
      h1 { font-size: 22px; }
      h2 { font-size: 16px; }
      .card { padding: 12px; }
      .row { gap: 10px; }
      button { width: 100%; }
      .chartWrap { height: 260px; }
      .chartWrap--short { height: 210px; }
      .chartWrap--tall { height: 620px; }
    }

    /* Ultra narrow */
    @media (max-width: 380px) {
      .chartWrap { height: 220px; }
    }
  </style>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js\"></script>
</head>
<body>
  <h1>birdnet-analytics</h1>
  <div class=\"muted\">BirdNET-GO analytics. v0. Auto-refresh: <span id=\"refresh\">__REFRESH_SECONDS__</span>s. Last updated: <span id=\"updated\">—</span>.</div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Controls</h2>
    <div class=\"row\">
      <div>
        <label for=\"tz\">Timezone</label>
        <input id=\"tz\" type=\"text\" value=\"__DEFAULT_TZ__\" />
      </div>
      <div>
        <label for=\"min_conf\">Min confidence</label>
        <input id=\"min_conf\" type=\"number\" value=\"0.0\" min=\"0\" max=\"1\" step=\"0.01\" />
      </div>
      <div>
        <label for=\"top_mode\">Species list</label>
        <select id=\"top_mode\">
          <option value=\"top\" selected>Top N</option>
          <option value=\"all\">All</option>
        </select>
      </div>
      <div>
        <label for=\"top_n\">Top N</label>
        <input id=\"top_n\" type=\"number\" value=\"10\" min=\"1\" max=\"200\" />
      </div>
      <div>
        <label for=\"hourly_mode\">Hourly baseline</label>
        <select id=\"hourly_mode\">
          <option value=\"pct\" selected>P10/P50/P90</option>
          <option value=\"minmax\">Min/Mean/Max</option>
        </select>
      </div>
    </div>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Dawn chorus (detections / 15 min)</h2>
    <div class=\"row\">
      <div>
        <label for=\"day\">Date</label>
        <input id=\"day\" type=\"date\" />
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

    <div class=\"chartWrap\" style=\"margin-top: 16px\">
      <canvas id=\"chart_dawn\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_dawn\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Dawn chorus by day (stacked: 15-min slices)</h2>
    <div class=\"row\">
      <div>
        <label for=\"dawn_days\">Days</label>
        <input id=\"dawn_days\" type=\"number\" value=\"30\" min=\"2\" max=\"3650\" />
      </div>
      <div>
        <label for=\"dawn_before\">Minutes before sunrise</label>
        <input id=\"dawn_before\" type=\"number\" value=\"90\" min=\"0\" />
      </div>
      <div>
        <label for=\"dawn_after\">Minutes after sunrise</label>
        <input id=\"dawn_after\" type=\"number\" value=\"150\" min=\"0\" />
      </div>
      <div>
        <button id=\"run_dawn_by_day\">Run</button>
      </div>
    </div>

    <div class=\"chartWrap chartWrap--tall\" style=\"margin-top: 16px\">
      <canvas id=\"chart_dawn_by_day\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_dawn_by_day\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Week over week (total detections + unique species)</h2>
    <div class=\"row\">
      <div>
        <label for=\"wow_weeks\">Weeks</label>
        <input id=\"wow_weeks\" type=\"number\" value=\"8\" min=\"2\" max=\"104\" />
      </div>
      <div>
        <button id=\"run_wow\">Run</button>
      </div>
    </div>

    <div class=\"chartWrap\" style=\"margin-top: 16px\">
      <canvas id=\"chart_wow\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_wow\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Top-3 share (100% stacked)</h2>
    <div class=\"row\">
      <div>
        <label for=\"topshare_days\">Days</label>
        <input id=\"topshare_days\" type=\"number\" value=\"30\" min=\"1\" />
      </div>
      <div>
        <button id=\"run_topshare\">Run</button>
      </div>
    </div>

    <div class=\"chartWrap\" style=\"margin-top: 16px\">
      <canvas id=\"chart_topshare\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_topshare\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Day parts (fixed: 00-06, 06-12, 12-18, 18-24) + precip (mm)</h2>
    <div class=\"row\">
      <div>
        <label for=\"dayparts_days\">Days</label>
        <input id=\"dayparts_days\" type=\"number\" value=\"30\" min=\"1\" />
      </div>
      <div>
        <button id=\"run_dayparts\">Run</button>
      </div>
    </div>

    <div class=\"chartWrap\" style=\"margin-top: 16px\">
      <canvas id=\"chart_dayparts\"></canvas>
    </div>

    <details style=\"margin-top: 12px\">
      <summary class=\"muted\">Raw JSON</summary>
      <pre id=\"raw_dayparts\"></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2 style=\"margin:0 0 8px 0\">Detections by hour of day</h2>
    <div class=\"row\">
      <div style=\"min-width: 280px\">
        <label for=\"species\">Species (optional; blank = all)</label>
        <input id=\"species\" type=\"text\" placeholder=\"(all species)\" list=\"species_list\" autocomplete=\"off\" />
        <datalist id=\"species_list\"></datalist>
      </div>
      <!-- days filter hidden for now; defaults to all -->
      <div>
        <button id=\"run_species\">Run</button>
      </div>
    </div>

    <div class=\"chartWrap chartWrap--tall\" style=\"margin-top: 16px\">
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
  const minConfInput = document.getElementById('min_conf');
  const topModeInput = document.getElementById('top_mode');
  const topNInput = document.getElementById('top_n');
  const hourlyModeInput = document.getElementById('hourly_mode');

  const beforeInput = document.getElementById('before');
  const afterInput = document.getElementById('after');

  const dawnDaysInput = document.getElementById('dawn_days');
  const dawnBeforeInput = document.getElementById('dawn_before');
  const dawnAfterInput = document.getElementById('dawn_after');

  const rawDawn = document.getElementById('raw_dawn');
  const rawDawnByDay = document.getElementById('raw_dawn_by_day');
  const rawWow = document.getElementById('raw_wow');
  const rawTopshare = document.getElementById('raw_topshare');
  const rawDayparts = document.getElementById('raw_dayparts');
  const rawSpecies = document.getElementById('raw_species');

  const runBtn = document.getElementById('run');
  const runDawnByDayBtn = document.getElementById('run_dawn_by_day');
  const runWowBtn = document.getElementById('run_wow');
  const runTopshareBtn = document.getElementById('run_topshare');
  const runDaypartsBtn = document.getElementById('run_dayparts');
  const runSpeciesBtn = document.getElementById('run_species');

  const wowWeeks = document.getElementById('wow_weeks');
  const topshareDays = document.getElementById('topshare_days');
  const daypartsDays = document.getElementById('dayparts_days');
  const speciesInput = document.getElementById('species');
  // days filter hidden for now; default to all
  const speciesDays = { value: 'all' };

  // default date = today
  const today = new Date();
  dayInput.value = today.toISOString().slice(0,10);

  let chartDawn;
  let chartDawnByDay;
  let chartWow;
  let chartTopshare;
  let chartDayparts;
  let chartSpecies;

  async function runDawn() {
    const day = dayInput.value;
    const tz = tzInput.value;
    const minConf = minConfInput.value;
    const before = beforeInput.value;
    const after = afterInput.value;

    const url = `/api/dawn/hourly?day=${encodeURIComponent(day)}&tz_name=${encodeURIComponent(tz)}&min_confidence=${encodeURIComponent(minConf)}&before_min=${encodeURIComponent(before)}&after_min=${encodeURIComponent(after)}&bucket_min=15`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawDawn.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => r.time);
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
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: { beginAtZero: true }
        }
      }
    });
  }

  async function runDawnByDay() {
    const tz = tzInput.value;
    const minConf = minConfInput.value;
    const days = dawnDaysInput.value;
    const before = dawnBeforeInput.value;
    const after = dawnAfterInput.value;

    const url = `/api/dawn/by_day?days=${encodeURIComponent(days)}&tz_name=${encodeURIComponent(tz)}&min_confidence=${encodeURIComponent(minConf)}&before_min=${encodeURIComponent(before)}&after_min=${encodeURIComponent(after)}&bucket_min=15`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawDawnByDay.textContent = JSON.stringify(data, null, 2);

    const labels = data.days;
    const bucketLabels = data.bucket_labels;

    // Build datasets as "15-min slice" series across days.
    // Normalize each day to 100% so you can compare shape (not volume).
    // If a day has 0 total detections, it will render as 0% across the board.
    const totals = data.rows.map(r => r.total || 0);

    const datasets = bucketLabels.map((bl, i) => {
      const hue = Math.round((i * 360) / Math.max(1, bucketLabels.length));
      return {
        label: bl,
        data: data.rows.map((r, di) => {
          const t = totals[di] || 0;
          const v = (r.buckets[i] || 0);
          return t > 0 ? (100.0 * v / t) : 0;
        }),
        backgroundColor: `hsla(${hue}, 70%, 55%, 0.65)`,
        borderColor: `hsla(${hue}, 70%, 40%, 1)`,
        borderWidth: 1,
        stack: 'dawn',
      };
    });

    const ctx = document.getElementById('chart_dawn_by_day');
    if (chartDawnByDay) chartDawnByDay.destroy();
    chartDawnByDay = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom' },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: { stacked: true, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 15 } },
          y: {
            stacked: true,
            beginAtZero: true,
            suggestedMax: 100,
            ticks: {
              callback: (v) => v + '%'
            }
          },
        }
      }
    });
  }

  async function runWow() {
    const tz = tzInput.value;
    const minConf = minConfInput.value;
    const weeks = wowWeeks.value;
    const url = `/api/wow?weeks=${encodeURIComponent(weeks)}&tz_name=${encodeURIComponent(tz)}&min_confidence=${encodeURIComponent(minConf)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawWow.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => r.week_start);
    const det = data.rows.map(r => r.detections);
    const uniq = data.rows.map(r => r.unique_species);

    const ctx = document.getElementById('chart_wow');
    if (chartWow) chartWow.destroy();
    chartWow = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Detections',
            data: det,
            backgroundColor: 'rgba(54, 162, 235, 0.6)',
            yAxisID: 'y',
          },
          {
            label: 'Unique species',
            data: uniq,
            type: 'line',
            borderColor: 'rgba(255, 99, 132, 1)',
            backgroundColor: 'rgba(255, 99, 132, 0.2)',
            yAxisID: 'y1',
            tension: 0.2,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { beginAtZero: true, position: 'left' },
          y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false } },
        }
      }
    });
  }

  async function runTopshare() {
    const tz = tzInput.value;
    const minConf = minConfInput.value;
    const days = topshareDays.value;
    const url = `/api/topshare/daily?days=${encodeURIComponent(days)}&top_k=3&tz_name=${encodeURIComponent(tz)}&min_confidence=${encodeURIComponent(minConf)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawTopshare.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => r.date);

    // We want top-3 for THAT DAY. Use rank buckets (Top1/Top2/Top3),
    // and show the actual species name in the tooltip.
    const top1Share = data.rows.map(r => (r.top?.[0]?.share || 0) * 100.0);
    const top2Share = data.rows.map(r => (r.top?.[1]?.share || 0) * 100.0);
    const top3Share = data.rows.map(r => (r.top?.[2]?.share || 0) * 100.0);
    const otherShare = data.rows.map(r => (r.other_share || 0) * 100.0);

    const top1Name = data.rows.map(r => (r.top?.[0]?.name || '—'));
    const top2Name = data.rows.map(r => (r.top?.[1]?.name || '—'));
    const top3Name = data.rows.map(r => (r.top?.[2]?.name || '—'));

    const datasets = [
      {
        label: 'Other',
        data: otherShare,
        backgroundColor: 'rgba(200, 200, 200, 0.75)',
        borderWidth: 0,
        stack: 'stack1',
      },
      {
        label: 'Top 3',
        data: top3Share,
        backgroundColor: 'rgba(255, 159, 64, 0.65)',
        borderWidth: 0,
        stack: 'stack1',
      },
      {
        label: 'Top 2',
        data: top2Share,
        backgroundColor: 'rgba(255, 99, 132, 0.65)',
        borderWidth: 0,
        stack: 'stack1',
      },
      {
        label: 'Top 1',
        data: top1Share,
        backgroundColor: 'rgba(54, 162, 235, 0.65)',
        borderWidth: 0,
        stack: 'stack1',
      },
    ];

    const ctx = document.getElementById('chart_topshare');
    if (chartTopshare) chartTopshare.destroy();
    chartTopshare = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          tooltip: {
            callbacks: {
              label: function(context) {
                const i = context.dataIndex;
                const lbl = context.dataset.label;
                const val = context.parsed.y;
                if (lbl === 'Top 1') return `Top 1: ${top1Name[i]} (${val.toFixed(1)}%)`;
                if (lbl === 'Top 2') return `Top 2: ${top2Name[i]} (${val.toFixed(1)}%)`;
                if (lbl === 'Top 3') return `Top 3: ${top3Name[i]} (${val.toFixed(1)}%)`;
                return `Other (${val.toFixed(1)}%)`;
              }
            }
          }
        },
        scales: {
          x: { stacked: true },
          y: {
            stacked: true,
            beginAtZero: true,
            max: 100,
            ticks: { callback: (v) => v + '%' }
          }
        }
      }
    });
  }

  async function runDayparts() {
    const tz = tzInput.value;
    const minConf = minConfInput.value;
    const days = daypartsDays.value;
    const url = `/api/dayparts/daily?days=${encodeURIComponent(days)}&tz_name=${encodeURIComponent(tz)}&min_confidence=${encodeURIComponent(minConf)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawDayparts.textContent = JSON.stringify(data, null, 2);

    const parts = data.parts;
    const labels = data.rows.map(r => r.date);
    const barDatasets = [
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
      yAxisID: 'y',
    }));

    const uniqDataset = {
      label: 'Unique species/day',
      data: data.rows.map(r => r.unique_species ?? 0),
      type: 'line',
      yAxisID: 'y1',
      borderColor: 'rgba(0, 0, 0, 0.7)',
      backgroundColor: 'rgba(0, 0, 0, 0.15)',
      tension: 0.2,
      pointRadius: 1,
    };

    const precipDataset = {
      label: 'Precip (mm)',
      data: data.rows.map(r => r.precip_mm ?? 0),
      type: 'line',
      yAxisID: 'y2',
      borderColor: 'rgba(59, 130, 246, 0.95)',
      backgroundColor: 'rgba(59, 130, 246, 0.10)',
      tension: 0.2,
      pointRadius: 1,
    };

    const ctx = document.getElementById('chart_dayparts');
    if (chartDayparts) chartDayparts.destroy();
    chartDayparts = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets: [...barDatasets, uniqDataset, precipDataset] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true },
          y: { stacked: true, beginAtZero: true, position: 'left' },
          y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false } },
          y2: {
            beginAtZero: true,
            position: 'right',
            grid: { drawOnChartArea: false },
            title: { display: true, text: 'mm' },
          },
        }
      }
    });
  }

  let searchTimer;

  async function updateSpeciesDatalist() {
    const q = speciesInput.value.trim();
    const mode = topModeInput.value;
    const limit = (mode === 'all') ? 200 : (parseInt(topNInput.value, 10) || 10);
    // If blank, show most frequent species (top-N or all-ish).
    const url = `/api/species/search?q=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`;
    const resp = await fetch(url);
    const data = await resp.json();
    const dl = document.getElementById('species_list');
    dl.innerHTML = '';
    for (const row of data.rows) {
      const opt = document.createElement('option');
      opt.value = row.name;
      dl.appendChild(opt);
    }
  }

  speciesInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      await updateSpeciesDatalist();
      // Also update the chart as the species changes.
      // (Debounced to avoid hammering the DB while typing.)
      await runSpecies();
      setUpdated();
    }, 250);
  });

  // When user picks a datalist option, 'change' fires.
  speciesInput.addEventListener('change', async () => {
    await runSpecies();
    setUpdated();
  });

  async function runSpecies() {
    const tz = tzInput.value;
    const minConf = minConfInput.value;
    const species = speciesInput.value.trim();

    const mode = hourlyModeInput.value;
    const url = `/api/activity/hourly_stats?species=${encodeURIComponent(species)}&tz_name=${encodeURIComponent(tz)}&min_confidence=${encodeURIComponent(minConf)}&mode=${encodeURIComponent(mode)}`;
    const resp = await fetch(url);
    const data = await resp.json();

    rawSpecies.textContent = JSON.stringify(data, null, 2);

    const labels = data.rows.map(r => String(r.hour).padStart(2,'0') + ':00');
    const vMin = data.rows.map(r => r.min);
    const vMax = data.rows.map(r => r.max);
    const vMean = data.rows.map(r => r.mean);

    const vP10 = data.rows.map(r => r.p10);
    const vP50 = data.rows.map(r => r.p50);
    const vP90 = data.rows.map(r => r.p90);
    const vActiveRate = data.rows.map(r => (r.active_rate || 0) * 100.0);

    const vToday = data.rows.map(r => r.today);

    const ctx = document.getElementById('chart_species');
    if (chartSpecies) chartSpecies.destroy();
    const baselineMode = data.mode || 'pct';

    const datasets = [];

    if (baselineMode === 'minmax') {
      datasets.push({
        type: 'line',
        label: 'Min (all-time, per-day)',
        data: vMin,
        borderColor: 'rgba(0,0,0,0.25)',
        backgroundColor: 'rgba(0,0,0,0.05)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y',
      });
      datasets.push({
        type: 'line',
        label: 'Mean (all-time, per-day)',
        data: vMean,
        borderColor: 'rgba(99, 102, 241, 0.95)',
        backgroundColor: 'rgba(99, 102, 241, 0.10)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y',
      });
      datasets.push({
        type: 'line',
        label: 'Max (all-time, per-day)',
        data: vMax,
        borderColor: 'rgba(0,0,0,0.55)',
        backgroundColor: 'rgba(0,0,0,0.08)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y',
      });
    } else {
      datasets.push({
        type: 'line',
        label: 'P10 (all-time, per-day)',
        data: vP10,
        borderColor: 'rgba(0,0,0,0.25)',
        backgroundColor: 'rgba(0,0,0,0.05)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y',
      });
      datasets.push({
        type: 'line',
        label: 'P50 (median, all-time)',
        data: vP50,
        borderColor: 'rgba(99, 102, 241, 0.95)',
        backgroundColor: 'rgba(99, 102, 241, 0.10)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y',
      });
      datasets.push({
        type: 'line',
        label: 'P90 (all-time, per-day)',
        data: vP90,
        borderColor: 'rgba(0,0,0,0.55)',
        backgroundColor: 'rgba(0,0,0,0.08)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y',
      });
      datasets.push({
        type: 'line',
        label: '% days active',
        data: vActiveRate,
        borderColor: 'rgba(34, 197, 94, 0.9)',
        backgroundColor: 'rgba(34, 197, 94, 0.12)',
        tension: 0.2,
        pointRadius: 0,
        yAxisID: 'y1',
      });
    }

    datasets.push({
      type: 'bar',
      label: `Today (${data.today.date})`,
      data: vToday,
      backgroundColor: 'rgba(255, 159, 64, 0.55)',
      borderWidth: 0,
      yAxisID: 'y',
    });

    const nowHour = (new Date()).getHours();

    const playheadPlugin = {
      id: 'playhead',
      afterDraw(chart, args, opts) {
        const {ctx, chartArea, scales} = chart;
        if (!chartArea) return;
        const xScale = scales.x;
        if (!xScale) return;

        // Labels are 'HH:00'
        const label = String(nowHour).padStart(2,'0') + ':00';
        const x = xScale.getPixelForValue(label);
        if (!isFinite(x)) return;

        ctx.save();
        ctx.strokeStyle = 'rgba(239, 68, 68, 0.9)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();

        ctx.fillStyle = 'rgba(239, 68, 68, 0.9)';
        ctx.font = '12px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif';
        ctx.fillText('now', x + 4, chartArea.top + 12);
        ctx.restore();
      }
    };

    chartSpecies = new Chart(ctx, {
      data: { labels, datasets },
      options: {
        responsive: true,
        scales: {
          y: { beginAtZero: true, position: 'left' },
          y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, suggestedMax: 100 },
        }
      },
      plugins: [playheadPlugin],
    });
  }

  function setUpdated() {
    document.getElementById('updated').textContent = new Date().toLocaleString();
  }

  async function refreshAll() {
    await runDawn();
    await runDawnByDay();
    await runWow();
    await runTopshare();
    await runDayparts();
    await runSpecies();
    setUpdated();
  }

  runBtn.addEventListener('click', async () => { await runDawn(); setUpdated(); });
  runDawnByDayBtn.addEventListener('click', async () => { await runDawnByDay(); setUpdated(); });
  runWowBtn.addEventListener('click', async () => { await runWow(); setUpdated(); });
  runTopshareBtn.addEventListener('click', async () => { await runTopshare(); setUpdated(); });
  runDaypartsBtn.addEventListener('click', async () => { await runDayparts(); setUpdated(); });
  runSpeciesBtn.addEventListener('click', async () => { await runSpecies(); setUpdated(); });

  // Recompute on control changes
  for (const el of [tzInput, minConfInput, topModeInput, topNInput, hourlyModeInput]) {
    el.addEventListener('change', () => {
      if (topModeInput.value === 'all') {
        topNInput.disabled = true;
      } else {
        topNInput.disabled = false;
      }
      refreshAll();
    });
  }
  // initialize
  if (topModeInput.value === 'all') topNInput.disabled = true;

  // Initial
  updateSpeciesDatalist();
  refreshAll();

  // Auto-refresh
  const REFRESH_SECONDS = parseInt(document.getElementById('refresh').textContent, 10) || 30;
  setInterval(refreshAll, REFRESH_SECONDS * 1000);
</script>
</body>
</html>"""


app = create_app()
