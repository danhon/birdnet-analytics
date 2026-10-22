"""Birds-only filter: non-bird labels must not reach any query."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from birdnet_analytics.db import BirdnetDb, is_bird, load_bird_names

# (label id, scientific_name, model_id). BirdNET-Go keeps one label per
# (scientific_name, model_id), and stores BirdNET v2.4's sound classes
# (Engine, Dog, ...) as Aves, so the DB can't tell what is a bird.
LABELS = [
    (1, "Corvus brachyrhynchos", 1),
    (2, "Corvus brachyrhynchos", 2),
    (3, "Engine", 1),
    (4, "Dog", 1),
    (5, "Power tools", 1),
    (6, "Canis latrans", 2),
    (7, "Haemorhous mexicanus", 4),
]


@pytest.fixture
def taxonomy_path(tmp_path) -> Path:
    # Same header shape as BirdNET-Go's config/models/shared/taxonomy.csv
    path = tmp_path / "taxonomy.csv"
    path.write_text(
        "idx,sci_name,com_name,species_code,class_name,common_name_de\n"
        "0,Corvus brachyrhynchos,American Crow,amecro,aves,Amerikanerkrähe\n"
        "1,Canis latrans,Coyote,coyote,mammalia,Kojote\n"
        '2,"Haemorhous mexicanus",House Finch,houfin,aves,"Hausgimpel, mexikanisch"\n'
    )
    return path


@pytest.fixture
def db_path(tmp_path) -> Path:
    path = tmp_path / "birdnet.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE labels (id INTEGER PRIMARY KEY, scientific_name TEXT, model_id INTEGER);
        CREATE TABLE detections (
            id INTEGER PRIMARY KEY, model_id INTEGER, label_id INTEGER,
            detected_at INTEGER, confidence REAL, latitude REAL, longitude REAL
        );
    """)
    con.executemany("INSERT INTO labels VALUES (?, ?, ?)", LABELS)
    con.executemany(
        "INSERT INTO detections (label_id, detected_at, confidence) VALUES (?, ?, 0.9)",
        [(label_id, 1_792_700_000 + label_id) for label_id, _, _ in LABELS],
    )
    con.commit()
    con.close()
    return path


def _names(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT l.scientific_name FROM bird_detections d JOIN labels l ON d.label_id = l.id"
    )}


class TestLoadBirdNames:
    def test_keeps_only_aves(self, taxonomy_path):
        assert load_bird_names(taxonomy_path) == {"Corvus brachyrhynchos", "Haemorhous mexicanus"}

    def test_missing_file_returns_none(self, tmp_path):
        assert load_bird_names(tmp_path / "nope.csv") is None

    def test_none_path_returns_none(self):
        assert load_bird_names(None) is None


class TestIsBird:
    def test_fallback_rejects_sound_classes(self):
        for name in ["Engine", "Dog", "Power tools", "Human vocal"]:
            assert not is_bird(name, None)

    def test_fallback_keeps_birds(self):
        assert is_bird("Corvus brachyrhynchos", None)


class TestBirdDetections:
    def test_keeps_only_birds_from_every_model(self, db_path, taxonomy_path):
        with BirdnetDb(db_path, taxonomy_path).connect_ro() as con:
            assert _names(con) == {"Corvus brachyrhynchos", "Haemorhous mexicanus"}
            assert con.execute("SELECT COUNT(*) FROM bird_detections").fetchone()[0] == 3

    def test_without_taxonomy_drops_sound_classes_only(self, db_path, tmp_path):
        with BirdnetDb(db_path, tmp_path / "missing.csv").connect_ro() as con:
            assert _names(con) == {"Corvus brachyrhynchos", "Haemorhous mexicanus", "Canis latrans"}


class TestEndpoints:
    def test_species_search_lists_birds_only(self, db_path, taxonomy_path, monkeypatch):
        monkeypatch.setenv("BIRDNET_DB_PATH", str(db_path))
        monkeypatch.setenv("BIRDNET_TAXONOMY_PATH", str(taxonomy_path))
        from birdnet_analytics.web import create_app

        app = create_app()
        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/species/search")
        result = endpoint(q="", limit=20)
        names = {row["name"] for row in result["rows"]}
        assert names == {"Corvus brachyrhynchos", "Haemorhous mexicanus"}
