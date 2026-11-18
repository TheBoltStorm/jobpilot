"""SQLite storage. Schema is versioned with PRAGMA user_version; later phases append migrations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

MIGRATIONS: list[str] = [
    # v1: resume versions
    """
    CREATE TABLE resumes (
        label       TEXT PRIMARY KEY,
        path        TEXT NOT NULL,
        sha256      TEXT NOT NULL,
        text        TEXT NOT NULL,
        parsed_json TEXT NOT NULL,
        is_default  INTEGER NOT NULL DEFAULT 0,
        added_at    TEXT NOT NULL
    );
    """,
]


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open, migrate, and always close (Windows keeps files locked otherwise)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- resumes -------------------------------------------------------------

def upsert_resume(conn, *, label: str, path: str, sha256: str, text: str, parsed: dict, make_default: bool) -> None:
    with conn:
        if make_default:
            conn.execute("UPDATE resumes SET is_default = 0")
        conn.execute(
            """INSERT INTO resumes (label, path, sha256, text, parsed_json, is_default, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(label) DO UPDATE SET path=excluded.path, sha256=excluded.sha256,
                 text=excluded.text, parsed_json=excluded.parsed_json,
                 is_default=MAX(resumes.is_default, excluded.is_default), added_at=excluded.added_at""",
            (label, path, sha256, text, json.dumps(parsed), int(make_default), now_iso()),
        )
        # Guarantee exactly one default if any resumes exist.
        if not conn.execute("SELECT 1 FROM resumes WHERE is_default = 1").fetchone():
            conn.execute("UPDATE resumes SET is_default = 1 WHERE label = ?", (label,))


def list_resumes(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT label, path, sha256, is_default, added_at FROM resumes ORDER BY added_at").fetchall()


def get_resume(conn, label: str | None = None) -> sqlite3.Row | None:
    if label:
        return conn.execute("SELECT * FROM resumes WHERE label = ?", (label,)).fetchone()
    return conn.execute("SELECT * FROM resumes WHERE is_default = 1").fetchone()


def set_default_resume(conn, label: str) -> bool:
    with conn:
        if not conn.execute("SELECT 1 FROM resumes WHERE label = ?", (label,)).fetchone():
            return False
        conn.execute("UPDATE resumes SET is_default = (label = ?)", (label,))
    return True
