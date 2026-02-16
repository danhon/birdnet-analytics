from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import sqlite3
import urllib.parse
import urllib.request
import json


@dataclass(frozen=True)
class DailyPrecip:
    day: date
    precip_mm: float


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_precip (
          day TEXT PRIMARY KEY,
          precip_mm REAL NOT NULL,
          fetched_at TEXT NOT NULL
        )
        """
    )
    con.commit()


def open_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    _ensure_schema(con)
    return con


def get_cached_range(con: sqlite3.Connection, start: date, end: date) -> dict[date, float]:
    rows = con.execute(
        "SELECT day, precip_mm FROM daily_precip WHERE day >= ? AND day <= ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    out: dict[date, float] = {}
    for d, mm in rows:
        out[date.fromisoformat(d)] = float(mm)
    return out


def upsert_many(con: sqlite3.Connection, items: list[DailyPrecip]) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    con.executemany(
        "INSERT OR REPLACE INTO daily_precip(day, precip_mm, fetched_at) VALUES (?,?,?)",
        [(it.day.isoformat(), float(it.precip_mm), now) for it in items],
    )
    con.commit()


def fetch_open_meteo_daily_precip_mm(
    *, latitude: float, longitude: float, start: date, end: date, tz_name: str
) -> list[DailyPrecip]:
    """Fetch daily precipitation totals (liquid-equivalent) in mm from Open-Meteo Archive API."""
    base = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": tz_name,
    }
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "birdnet-analytics/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    daily = data.get("daily") or {}
    times = daily.get("time") or []
    sums = daily.get("precipitation_sum") or []
    out: list[DailyPrecip] = []
    for t, mm in zip(times, sums, strict=False):
        if mm is None:
            continue
        out.append(DailyPrecip(day=date.fromisoformat(t), precip_mm=float(mm)))
    return out
