"""Direct SQL on a store database in tests (to tamper with or inspect it)."""

import sqlite3
from pathlib import Path
from typing import Any


def run_sql(db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Run one statement in its own committed transaction; always closes the connection."""
    conn = sqlite3.connect(db)
    try:
        with conn:  # commits (sqlite3's context manager does not close)
            return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
