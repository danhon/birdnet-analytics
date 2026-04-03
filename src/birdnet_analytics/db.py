from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class BirdnetDb:
    path: Path

    def connect_ro(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)


def guess_lat_lon(con: sqlite3.Connection) -> tuple[float, float]:
    cur = con.cursor()
    row = cur.execute(
        "SELECT latitude, longitude FROM detections WHERE latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("No latitude/longitude found in detections; cannot compute sunrise")
    return float(row[0]), float(row[1])
