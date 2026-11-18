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
    # v2: discovered jobs
    """
    CREATE TABLE jobs (
        id           INTEGER PRIMARY KEY,
        dedupe_key   TEXT NOT NULL UNIQUE,
        company_key  TEXT NOT NULL,
        url_key      TEXT NOT NULL,
        title        TEXT NOT NULL,
        company      TEXT NOT NULL,
        location     TEXT NOT NULL DEFAULT '',
        remote       TEXT NOT NULL DEFAULT '',
        role_type    TEXT NOT NULL DEFAULT '',
        field        TEXT NOT NULL DEFAULT '',
        url          TEXT NOT NULL,
        apply_url    TEXT NOT NULL DEFAULT '',
        ats          TEXT NOT NULL DEFAULT '',
        description  TEXT NOT NULL DEFAULT '',
        date_posted  TEXT NOT NULL DEFAULT '',
        date_found   TEXT NOT NULL,
        last_seen    TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'found'
    );
    CREATE INDEX jobs_company ON jobs(company_key);
    CREATE INDEX jobs_url ON jobs(url_key);
    CREATE INDEX jobs_status ON jobs(status);

    CREATE TABLE job_sources (
        job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        source     TEXT NOT NULL,
        scope      TEXT NOT NULL,
        source_id  TEXT NOT NULL,
        url        TEXT NOT NULL,
        active     INTEGER NOT NULL DEFAULT 1,
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL,
        PRIMARY KEY (source, scope, source_id)
    );
    CREATE INDEX job_sources_job ON job_sources(job_id);

    CREATE TABLE source_runs (
        id          INTEGER PRIMARY KEY,
        source      TEXT NOT NULL,
        scope       TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        fetched     INTEGER NOT NULL DEFAULT 0,
        kept        INTEGER NOT NULL DEFAULT 0,
        new         INTEGER NOT NULL DEFAULT 0,
        merged      INTEGER NOT NULL DEFAULT 0,
        closed      INTEGER NOT NULL DEFAULT 0,
        error       TEXT
    );
    """,
    # v3: description enrichment, match results, scoring runs
    """
    ALTER TABLE jobs ADD COLUMN enrich_status TEXT NOT NULL DEFAULT '';

    CREATE TABLE matches (
        job_id         INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        score          INTEGER NOT NULL,
        confidence     TEXT NOT NULL,          -- high | medium | low
        reason         TEXT NOT NULL,
        tags           TEXT NOT NULL DEFAULT '',  -- field tags, comma-separated
        skills_json    TEXT NOT NULL,          -- {"required": [...], "preferred": [...], "have": [...], ...}
        engine         TEXT NOT NULL,          -- local | claude:<model>
        profile_hash   TEXT NOT NULL,
        description_len INTEGER NOT NULL,
        scored_at      TEXT NOT NULL
    );
    CREATE INDEX matches_score ON matches(score);

    CREATE TABLE match_runs (
        id             INTEGER PRIMARY KEY,
        engine         TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        finished_at    TEXT,
        jobs_scored    INTEGER NOT NULL DEFAULT 0,
        input_tokens   INTEGER NOT NULL DEFAULT 0,
        output_tokens  INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd       REAL NOT NULL DEFAULT 0,
        error          TEXT
    );
    """,
    # v4: application review (what would be filled, your edits, approvals) and an event log
    """
    CREATE TABLE applications (
        job_id        INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        resume_label  TEXT NOT NULL DEFAULT '',
        fields_json   TEXT NOT NULL DEFAULT '[]',
        drafts_json   TEXT NOT NULL DEFAULT '[]',
        notes         TEXT NOT NULL DEFAULT '',
        prepared_at   TEXT,
        approved_at   TEXT,
        approved_snapshot TEXT,             -- exactly what you approved; autofill may only use this
        filled_at     TEXT,
        submitted_at  TEXT,
        rejected_at   TEXT,
        skipped_at    TEXT,
        updated_at    TEXT NOT NULL
    );

    CREATE TABLE application_events (
        id          INTEGER PRIMARY KEY,
        job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        at          TEXT NOT NULL,
        actor       TEXT NOT NULL,          -- user | system
        action      TEXT NOT NULL,
        from_status TEXT NOT NULL DEFAULT '',
        to_status   TEXT NOT NULL DEFAULT '',
        note        TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX application_events_job ON application_events(job_id);
    """,
    # v5: real application-form questions (from public ATS APIs) and autofill run reports
    """
    CREATE TABLE forms (
        job_id         INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        ats            TEXT NOT NULL,
        form_url       TEXT NOT NULL,          -- the page autofill opens
        questions_json TEXT NOT NULL,
        fetched_at     TEXT NOT NULL
    );

    CREATE TABLE autofill_runs (
        id           INTEGER PRIMARY KEY,
        job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        mode         TEXT NOT NULL,            -- dry_run | fill
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        outcome      TEXT NOT NULL DEFAULT '', -- stopped_before_submit | account_wall | captcha | error | ...
        report_json  TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX autofill_runs_job ON autofill_runs(job_id);
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
