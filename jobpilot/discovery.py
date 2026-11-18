"""Run sources, filter to the user's targets, dedupe into SQLite, and mark closed postings."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from jobpilot.core.db import now_iso
from jobpilot.onboarding.profile import Profile
from jobpilot.settings import Settings
from jobpilot.sources.base import JobSource, RawJob, ScopeResult
from jobpilot.sources.dedupe import canonical_url, company_key, dedupe_key, location_key, titles_match
from jobpilot.sources.fields import job_tags, tags_for
from jobpilot.sources.normalize import detect_ats

# Statuses discovery may move to "posting_closed". Anything the user has acted on is left alone.
CLOSABLE = ("found", "matched", "ready_to_fill", "needs_human", "account_wall", "custom_site")


@dataclass
class ScopeSummary:
    source: str
    scope: str
    fetched: int = 0
    kept: int = 0
    new: int = 0
    merged: int = 0
    updated: int = 0
    closed: int = 0
    error: str = ""


@dataclass
class Filter:
    role_types: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    min_date: str = ""

    @classmethod
    def build(cls, settings: Settings, profile: Profile | None, today: date | None = None) -> "Filter":
        today = today or date.today()
        f = cls(min_date=(today - timedelta(days=settings.max_age_days)).isoformat() if settings.max_age_days else "")
        if settings.filter_by_profile and profile:
            f.role_types = set(profile.targets.role_types)
            f.tags = tags_for(profile.targets.fields + profile.targets.roles)
        return f

    def keep(self, job: RawJob) -> bool:
        if not job.active or not job.title or not job.url:
            return False
        if self.min_date and job.date_posted and job.date_posted < self.min_date:
            return False
        if self.role_types and (job.role_type or "full-time") not in self.role_types:
            return False
        if self.tags and not (job_tags(job.title, job.field) & self.tags):
            return False
        return True


# ---------------------------------------------------------------- storage

def _fill_blanks(conn: sqlite3.Connection, job_id: int, raw: RawJob, now: str) -> None:
    """Merge a duplicate into an existing job: keep existing values, fill blanks, prefer longer descriptions."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    updates: dict[str, str] = {"last_seen": now}
    candidates = {
        "location": "; ".join(raw.locations), "remote": raw.remote, "role_type": raw.role_type,
        "field": raw.field, "apply_url": raw.apply_url, "date_posted": raw.date_posted,
    }
    for col, val in candidates.items():
        if val and not row[col]:
            updates[col] = val
    if raw.description and len(raw.description) > len(row["description"]):
        updates["description"] = raw.description
    # A direct ATS link beats an aggregator / redirect link.
    new_ats = detect_ats(raw.url)
    if row["ats"] in ("", "other") and new_ats not in ("", "other"):
        updates.update(url=raw.url, url_key=canonical_url(raw.url), ats=new_ats)
    if row["status"] == "posting_closed":
        updates["status"] = "found"
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*updates.values(), job_id))


def _find_existing(conn: sqlite3.Connection, raw: RawJob) -> int | None:
    ukey = canonical_url(raw.url)
    if ukey:
        row = conn.execute("SELECT id FROM jobs WHERE url_key = ?", (ukey,)).fetchone()
        if row:
            return row[0]
    dkey = dedupe_key(raw.company, raw.title, raw.locations)
    row = conn.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (dkey,)).fetchone()
    if row:
        return row[0]
    loc = location_key(raw.locations)
    for row in conn.execute(
        "SELECT id, title, dedupe_key FROM jobs WHERE company_key = ?", (company_key(raw.company),)
    ):
        other = row["dedupe_key"].rsplit("|", 1)[-1]
        if (other == loc or not other or not loc) and titles_match(row["title"], raw.title):
            return row["id"]
    return None


def upsert_job(conn: sqlite3.Connection, source: str, scope: str, raw: RawJob, now: str) -> str:
    """Store one posting. Returns 'new', 'merged' (duplicate of another source's job) or 'updated'."""
    link = conn.execute(
        "SELECT job_id FROM job_sources WHERE source = ? AND scope = ? AND source_id = ?",
        (source, scope, raw.source_id),
    ).fetchone()
    if link:
        _fill_blanks(conn, link[0], raw, now)
        conn.execute(
            "UPDATE job_sources SET last_seen = ?, active = 1, url = ? WHERE source = ? AND scope = ? AND source_id = ?",
            (now, raw.url, source, scope, raw.source_id),
        )
        return "updated"

    job_id = _find_existing(conn, raw)
    outcome = "merged"
    if job_id is None:
        outcome = "new"
        cur = conn.execute(
            """INSERT INTO jobs (dedupe_key, company_key, url_key, title, company, location, remote, role_type,
                   field, url, apply_url, ats, description, date_posted, date_found, last_seen, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'found')""",
            (
                dedupe_key(raw.company, raw.title, raw.locations), company_key(raw.company), canonical_url(raw.url),
                raw.title, raw.company, "; ".join(raw.locations), raw.remote, raw.role_type, raw.field, raw.url,
                raw.apply_url, detect_ats(raw.url), raw.description, raw.date_posted, now, now,
            ),
        )
        job_id = cur.lastrowid
    else:
        _fill_blanks(conn, job_id, raw, now)
    conn.execute(
        """INSERT INTO job_sources (job_id, source, scope, source_id, url, active, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (job_id, source, scope, raw.source_id, raw.url, now, now),
    )
    return outcome


def close_missing(conn: sqlite3.Connection, source: str, scope: str, seen: set[str]) -> int:
    """Deactivate links not in this complete listing; close jobs with no active links left."""
    stale = [
        (r["source_id"], r["job_id"])
        for r in conn.execute(
            "SELECT source_id, job_id FROM job_sources WHERE source = ? AND scope = ? AND active = 1", (source, scope)
        )
        if r["source_id"] not in seen
    ]
    closed = 0
    for source_id, job_id in stale:
        conn.execute(
            "UPDATE job_sources SET active = 0 WHERE source = ? AND scope = ? AND source_id = ?",
            (source, scope, source_id),
        )
        still_open = conn.execute(
            "SELECT 1 FROM job_sources WHERE job_id = ? AND active = 1", (job_id,)
        ).fetchone()
        if not still_open:
            cur = conn.execute(
                f"UPDATE jobs SET status = 'posting_closed' WHERE id = ? AND status IN ({','.join('?' * len(CLOSABLE))})",
                (job_id, *CLOSABLE),
            )
            closed += cur.rowcount
    return closed


def store_scope(conn: sqlite3.Connection, source: str, result: ScopeResult, flt: Filter) -> ScopeSummary:
    summary = ScopeSummary(source, result.scope, fetched=len(result.jobs), error=result.error)
    started = now_iso()
    seen: set[str] = set()
    with conn:
        for raw in result.jobs:
            if raw.active:
                seen.add(raw.source_id)
            if not flt.keep(raw):
                continue
            summary.kept += 1
            outcome = upsert_job(conn, source, result.scope, raw, started)
            setattr(summary, outcome, getattr(summary, outcome) + 1)
        if result.complete and not result.error:
            summary.closed = close_missing(conn, source, result.scope, seen)
        conn.execute(
            """INSERT INTO source_runs (source, scope, started_at, finished_at, fetched, kept, new, merged, closed, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source, result.scope, started, now_iso(), summary.fetched, summary.kept, summary.new, summary.merged,
             summary.closed, summary.error or None),
        )
    return summary


def run_discovery(
    conn: sqlite3.Connection,
    sources: list[JobSource],
    settings: Settings,
    profile: Profile | None,
    log: Callable[[str], None] = print,
) -> list[ScopeSummary]:
    flt = Filter.build(settings, profile)
    out: list[ScopeSummary] = []
    for src in sources:
        ok, why = src.enabled()
        if not ok:
            log(f"- {src.label}: skipped ({why})")
            continue
        log(f"- {src.label}:")
        for result in src.fetch():
            s = store_scope(conn, src.name, result, flt)
            out.append(s)
            if s.error:
                log(f"    {s.scope}: ERROR {s.error}")
            else:
                log(f"    {s.scope}: {s.fetched} listed, {s.kept} match you, {s.new} new, "
                    f"{s.merged} duplicates merged, {s.closed} closed")
    return out
