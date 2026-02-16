from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun


@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime
    sunset: datetime


def compute_sun_times(*, on_date: date, latitude: float, longitude: float, tz_name: str) -> SunTimes:
    """Compute sunrise/sunset for a given date/location in the given timezone."""
    tz = ZoneInfo(tz_name)
    loc = LocationInfo(name="BirdNET", region="", timezone=tz_name, latitude=latitude, longitude=longitude)
    s = sun(loc.observer, date=on_date, tzinfo=tz)
    return SunTimes(sunrise=s["sunrise"], sunset=s["sunset"])


def dawn_window(*, sunrise: datetime, before: timedelta, after: timedelta) -> tuple[datetime, datetime]:
    return (sunrise - before, sunrise + after)
