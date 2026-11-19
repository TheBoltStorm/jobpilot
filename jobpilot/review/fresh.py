"""Apply early, skip dead postings, and don't apply twice.

- age / is_new: how long a posting has been up (its posted date, else when JobPilot found it).
  Early applications get more replies, so new ones are flagged and can go first.
- still_open: before you spend time on a form, check the posting is still up. Greenhouse, Lever
  and Ashby are asked through their public APIs; other sites get one polite GET (robots.txt
  respected) and a look for "no longer accepting applications"-style wording.
- reposts: the same role at the same company listed again (new id, new season, new city).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

import httpx

from jobpilot.core.http import PoliteClient, RobotsDisallowed
from jobpilot.review.status import PRE_APPROVAL, log_event, transition
from jobpilot.sources.link import READABLE, read_posting
from jobpilot.sources.normalize import detect_ats

NEW_DAYS = 2

CLOSED_WORDS = re.compile(
    r"no longer (accepting (applications|applicants)|available|open|active|posted)|"
    r"(position|role|job) (has been|was) filled|"
    r"(job|posting|position|requisition) (has )?(expired|closed|been closed|been removed)|"
    r"this (job|position|posting) is (closed|no longer)|"
    r"applications? (are|is) (now )?closed|"
    r"(page|job) you('re| are) looking for (doesn't|does not|no longer) exist",
    re.IGNORECASE)


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def age_days(job, today: date | None = None) -> int | None:
    d = _date(job["date_posted"]) or _date(job["date_found"])
    return None if d is None else max(0, ((today or date.today()) - d).days)


def is_new(job, today: date | None = None) -> bool:
    a = age_days(job, today)
    return a is not None and a <= NEW_DAYS


def age_label(job, today: date | None = None) -> str:
    a = age_days(job, today)
    if a is None:
        return ""
    return "posted today" if a == 0 else f"posted {a} day{'s' if a != 1 else ''} ago"


# ---- still open?

def still_open(http: PoliteClient, job, gh_tokens: dict[str, str] | None = None) -> str:
    """'open', 'closed' or 'unknown'. Never guesses closed from an error it can't explain."""
    url = job["url"]
    try:
        if detect_ats(url) in READABLE:
            return "open" if read_posting(http, url, gh_tokens) is not None else "unknown"
        resp = http.get(job["apply_url"] or url)
        return "closed" if CLOSED_WORDS.search(resp.text[:300000]) else "open"
    except httpx.HTTPStatusError as exc:
        return "closed" if exc.response.status_code in (404, 410) else "unknown"
    except (RobotsDisallowed, httpx.HTTPError, ValueError, KeyError):
        return "unknown"


def record_check(conn: sqlite3.Connection, job_id: int, state: str) -> None:
    """Note the result. A closed posting you haven't approved moves to Posting closed."""
    if state == "unknown":
        log_event(conn, job_id, "system", "couldn't tell if the posting is still open")
        return
    if state == "open":
        log_event(conn, job_id, "system", "posting is still open")
        return
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
    if status in PRE_APPROVAL:
        transition(conn, job_id, "posting_closed", actor="system", action="posting is closed",
                   note="the site says it's no longer taking applications")
        conn.execute("UPDATE job_sources SET active = 0 WHERE job_id = ?", (job_id,))
    else:
        log_event(conn, job_id, "system", "posting looks closed", note="check the site before you spend time on it")


def last_check(conn: sqlite3.Connection, job_id: int) -> tuple[str, str] | None:
    """(result, when) of the most recent check, if any."""
    r = conn.execute(
        """SELECT action, at FROM application_events WHERE job_id = ? AND action IN
           ('posting is still open', 'posting is closed', 'posting looks closed', 'couldn''t tell if the posting is still open')
           ORDER BY id DESC LIMIT 1""", (job_id,)).fetchone()
    if not r:
        return None
    return ("open" if "still open" in r["action"] else "unknown" if "couldn" in r["action"] else "closed"), r["at"]


# ---- reposts

_NOISE = re.compile(r"\([^)]*\)|\[[^]]*\]|\b(19|20)\d{2}\b|\b(summer|fall|autumn|winter|spring)\b|"
                    r"\b(remote|hybrid|onsite|on-site)\b|[-–—,:/|].*$", re.IGNORECASE)


def role_key(title: str) -> str:
    t = _NOISE.sub(" ", title or "")
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def reposts(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    """Other listings of the same role at the same company, newest first."""
    job = conn.execute("SELECT id, title, company_key FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return []
    key = role_key(job["title"])
    if not key:
        return []
    rows = conn.execute(
        """SELECT j.id, j.title, j.company, j.status, j.date_found, a.submitted_at FROM jobs j
           LEFT JOIN applications a ON a.job_id = j.id WHERE j.company_key = ? AND j.id != ?
           ORDER BY j.id DESC""", (job["company_key"], job_id)).fetchall()
    return [r for r in rows if role_key(r["title"]) == key]


def applied_before(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return next((r for r in reposts(conn, job_id) if r["status"] == "submitted"), None)
