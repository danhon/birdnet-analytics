#!/usr/bin/env python3
"""Compute dawn-chorus detections/hour from BirdNET-GO birdnet.db.

Reads BirdNET-GO DB read-only, computes sunrise using Astral (unless daily_events provides nonzero sunrise),
then counts detections in a dawn window grouped by hour.

Usage:
  python scripts/dawn_chorus_hourly.py _data/sample/birdnet.db --tz America/Los_Angeles

Defaults:
  dawn window = sunrise-90min .. sunrise+150min
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import sys

# Allow running without installing the package
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from birdnet_analytics.db import BirdnetDb, guess_lat_lon
from birdnet_analytics.sun import compute_sun_times, dawn_window


def _daily_events_sunrise(con: sqlite3.Connection, day: str) -> int | None:
    # BirdNET-GO schema: daily_events(date TEXT, sunrise INTEGER, sunset INTEGER)
    row = con.execute("SELECT sunrise FROM daily_events WHERE date = ?", (day,)).fetchone()
    if not row:
        return None
    v = row[0]
    if v is None:
        return None
    try:
        iv = int(v)
    except Exception:
        return None
    if iv == 0:
        return None
    return iv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("--tz", default="America/Los_Angeles")
    ap.add_argument("--before-min", type=int, default=90)
    ap.add_argument("--after-min", type=int, default=150)
    args = ap.parse_args()

    db = BirdnetDb(args.db)
    before = timedelta(minutes=args.before_min)
    after = timedelta(minutes=args.after_min)

    with db.connect_ro() as con:
        lat, lon = guess_lat_lon(con)

        # get distinct dates from notes.date (YYYY-MM-DD)
        days = [r[0] for r in con.execute("SELECT DISTINCT date FROM notes ORDER BY date")]

        print("date\thour_local\tdetections")
        for day in days:
            sunrise_int = _daily_events_sunrise(con, day)
            if sunrise_int is not None:
                # Can't decode units yet; treat as unsupported until we see nonzero data.
                # Fall back to Astral.
                sunrise_dt = None
            else:
                sunrise_dt = None

            if sunrise_dt is None:
                sun_times = compute_sun_times(on_date=datetime.fromisoformat(day).date(), latitude=lat, longitude=lon, tz_name=args.tz)
                sunrise_dt = sun_times.sunrise

            start, end = dawn_window(sunrise=sunrise_dt, before=before, after=after)

            # SQLite's datetime()/strftime() handling of offsets is inconsistent.
            # Do timestamp parsing + tz conversion in Python for correctness.
            from zoneinfo import ZoneInfo
            import re

            tz = ZoneInfo(args.tz)

            def parse_begin_time(s: str) -> datetime:
                # Example in this DB: '2026-02-16 09:33:43.731828028-08:00'
                s = s.replace(" ", "T", 1)
                # Truncate fractional seconds to microseconds (datetime only supports 6 digits)
                s = re.sub(r"\.(\d{6})\d+", r".\1", s)
                return datetime.fromisoformat(s)

            buckets: dict[int, int] = {}
            for (bt,) in con.execute(
                "SELECT begin_time FROM notes WHERE date = ? AND begin_time IS NOT NULL", (day,)
            ):
                dt = parse_begin_time(bt).astimezone(tz)
                if start <= dt < end:
                    buckets[dt.hour] = buckets.get(dt.hour, 0) + 1

            for hour in sorted(buckets):
                print(f"{day}\t{hour:02d}\t{buckets[hour]}")


if __name__ == "__main__":
    main()
