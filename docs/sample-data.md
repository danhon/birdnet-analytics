# Sample data

Do **not** commit a real `birdnet.db`.

Rationale:
- `notes` contains precise latitude/longitude and detailed timestamps.
- If this repo is ever published, that can reveal location + activity patterns.

Recommended:
- Keep real DB copies under `_data/` (gitignored).
- If we need an in-repo sample, generate a sanitized sample DB (scrub lat/lon + truncate).
