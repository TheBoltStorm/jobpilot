"""Sort jobs fast: one card at a time, a key per decision (Keep, Later, Pass).

Keeping a job only marks it; it stays in your Apply queue and kept jobs come first there.
Passing rejects it (yours to undo from its page). Nothing is approved or filled here.
"""

from __future__ import annotations

import sqlite3

from jobpilot.onboarding.profile import Profile
from jobpilot.review import fresh, plan
from jobpilot.review.status import log_event

KEPT = "kept"


def to_sort(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Queue jobs you haven't sorted yet: new postings first, then best match."""
    rows = conn.execute(
        f"""SELECT j.*, m.score FROM jobs j LEFT JOIN matches m ON m.job_id = j.id
            LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status IN ({','.join('?' * len(plan.QUEUE_STATUSES))}) AND a.skipped_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM application_events e WHERE e.job_id = j.id AND e.action = ?)""",
        (*plan.QUEUE_STATUSES, KEPT)).fetchall()
    return sorted(rows, key=lambda r: (not fresh.is_new(r), -(r["score"] or 0), r["id"]))


def kept_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        f"""SELECT COUNT(DISTINCT e.job_id) FROM application_events e JOIN jobs j ON j.id = e.job_id
            WHERE e.action = ? AND j.status IN ({','.join('?' * len(plan.QUEUE_STATUSES))})""",
        (KEPT, *plan.QUEUE_STATUSES)).fetchone()[0]


def decide(conn: sqlite3.Connection, job_id: int, action: str, profile: Profile | None) -> None:
    if action == "pass":
        plan.reject(conn, job_id, "passed while sorting")
        return
    if profile is not None and not plan.load_application(conn, job_id):
        plan.prepare(conn, job_id, profile)
    if action == "keep":
        log_event(conn, job_id, "user", KEPT)
    elif action == "later":
        plan.skip(conn, job_id)
    else:
        raise plan.GateError(f"unknown action {action!r}")
