from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import sqlite3

log = logging.getLogger(__name__)

# BirdNET v2.4's non-bird sound classes. BirdNET-Go stores them as species of
# class Aves, so the DB's label metadata can't filter them out. Used only when
# taxonomy.csv is unavailable, so a missing mount degrades rather than empties
# the charts (non-bird animals from other models get through in that case).
NON_BIRD_SOUND_CLASSES = frozenset({
    "Engine", "Dog", "Fireworks", "Gun", "Siren", "Power tools",
    "Human vocal", "Human non-vocal", "Human whistle",
})


@lru_cache(maxsize=4)
def load_bird_names(taxonomy_path: Path | None) -> frozenset[str] | None:
    """
    Scientific names whose class_name is "aves" in BirdNET-Go's taxonomy.csv,
    or None if the file is missing or unreadable. Cached: the file only
    changes when BirdNET-Go is upgraded, and that restart reloads it.
    """
    if taxonomy_path is None:
        return None
    try:
        with open(taxonomy_path, newline="", encoding="utf-8") as f:
            return frozenset(
                row["sci_name"] for row in csv.DictReader(f)
                if row.get("class_name") == "aves"
            )
    except (OSError, KeyError, csv.Error) as exc:
        log.warning("Taxonomy unavailable (%s); falling back to sound-class denylist", exc)
        return None


def is_bird(scientific_name: str, bird_names: frozenset[str] | None) -> bool:
    if bird_names is None:
        return scientific_name not in NON_BIRD_SOUND_CLASSES
    return scientific_name in bird_names


@dataclass(frozen=True)
class BirdnetDb:
    path: Path
    taxonomy_path: Path | None = None

    def connect_ro(self) -> sqlite3.Connection:
        """
        Open the DB read-only and add a temp view, bird_detections: the rows of
        detections whose label is a bird. Query it instead of detections —
        BirdNET-Go also reports sound classes (Engine, Dog, ...), mammals and
        frogs. The temp objects live in this connection only; the DB file is
        never written.
        """
        # immutable=1 bypasses WAL/shm locking — safe because BirdNET-Go
        # is the sole writer and we never modify the DB.
        con = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True)
        bird_names = load_bird_names(self.taxonomy_path)
        con.execute("PRAGMA temp_store = MEMORY")
        # Measured 2026-10-22: IN (bird ids) costs ~12% over unfiltered on the
        # per-day full scans; NOT IN (non-bird ids) was slower, ~40%.
        con.execute("CREATE TEMP TABLE bird_labels (id INTEGER PRIMARY KEY)")
        con.executemany(
            "INSERT INTO temp.bird_labels VALUES (?)",
            [(label_id,) for label_id, name in con.execute("SELECT id, scientific_name FROM main.labels")
             if name and is_bird(name, bird_names)],
        )
        con.execute(
            "CREATE TEMP VIEW bird_detections AS "
            "SELECT * FROM main.detections WHERE label_id IN (SELECT id FROM temp.bird_labels)"
        )
        return con


def guess_lat_lon(con: sqlite3.Connection) -> tuple[float, float]:
    cur = con.cursor()
    row = cur.execute(
        "SELECT latitude, longitude FROM detections WHERE latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("No latitude/longitude found in detections; cannot compute sunrise")
    return float(row[0]), float(row[1])
