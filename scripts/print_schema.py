#!/usr/bin/env python3
"""Print SQLite schema for a given DB file.

Usage:
  python scripts/print_schema.py /path/to/birdnet.db
"""

import sqlite3
import sys


def main(path: str) -> None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
        print("Tables:")
        for t in tables:
            print(f"- {t}")

        print("\nSchema:")
        for (sql,) in cur.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type DESC, name"
        ):
            print(sql + ";\n")
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Expected 1 arg: path to sqlite db")
    main(sys.argv[1])
