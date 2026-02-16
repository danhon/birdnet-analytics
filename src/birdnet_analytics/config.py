from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_dir: Path | None
    db_path: Path | None
    host: str
    port: int
    root_path: str


def _getenv(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v


def load_settings() -> Settings:
    db_dir = _getenv("BIRDNET_DB_DIR")
    db_path = _getenv("BIRDNET_DB_PATH")

    host = _getenv("BIRDNET_ANALYTICS_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(_getenv("BIRDNET_ANALYTICS_PORT", "8787") or "8787")
    root_path = _getenv("BIRDNET_ANALYTICS_ROOT_PATH", "") or ""

    return Settings(
        db_dir=Path(db_dir) if db_dir else None,
        db_path=Path(db_path) if db_path else None,
        host=host,
        port=port,
        root_path=root_path,
    )
