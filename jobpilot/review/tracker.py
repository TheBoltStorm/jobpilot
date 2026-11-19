"""The Applications page: your submitted applications as a pipeline, plus the numbers behind it.

Stages are still yours to set (see outcomes.py). Dragging a card to another column is just a
quicker way to pick a stage.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from jobpilot.review.outcomes import BY_KEY, _day


@dataclass(frozen=True)
class Column:
    key: str  # the stage a card dropped here gets
    label: str
    stages: tuple[str, ...]


COLUMNS = (
    Column("waiting", "Applied", ("waiting",)),
    Column("screen", "Screen", ("screen",)),
    Column("assessment", "Assessment", ("assessment",)),
    Column("interview", "Interviewing", ("interview", "final")),
    Column("offer", "Offer", ("offer", "accepted")),
    Column("rejected", "Closed", ("rejected", "declined", "withdrawn", "no_response")),
)
COLUMN_OF = {s: c.key for c in COLUMNS for s in c.stages}

SORTS = {
    "recent": "COALESCE(MAX(o.stage_at, COALESCE(a.submitted_at, '')), a.submitted_at) DESC",
    "applied": "a.submitted_at DESC",
    "company": "j.company COLLATE NOCASE, j.title",
}

# A few calm colors for the company initials (no logos: they'd come from a third-party service).
AVATAR_COLORS = ("#2357c6", "#1d9a5b", "#c2410c", "#7c5cc4", "#0e7490", "#b42318", "#8a5a00", "#be185d")


def avatar(company: str) -> tuple[str, str]:
    name = (company or "?").strip()
    letters = "".join(w[0] for w in name.split()[:2] if w[:1].isalnum()).upper()[:2] or "?"
    return letters, AVATAR_COLORS[sum(map(ord, name.lower())) % len(AVATAR_COLORS)]


def applications(conn: sqlite3.Connection, q: str = "", field: str = "", role_type: str = "",
                 sort: str = "recent") -> list[sqlite3.Row]:
    where, params = ["j.status = 'submitted'"], []
    if q:
        where.append("(j.title LIKE ? OR j.company LIKE ? OR COALESCE(a.notes, '') LIKE ? OR COALESCE(o.next_note, '') LIKE ?)")
        params += [f"%{q}%"] * 4
    if field:
        where.append("(',' || COALESCE(m.tags, '') || ',') LIKE ?")
        params.append(f"%,{field},%")
    if role_type:
        where.append("j.role_type = ?")
        params.append(role_type)
    return conn.execute(
        f"""SELECT o.*, j.title, j.company, j.role_type, j.ats, a.submitted_at, a.resume_label,
                   (SELECT GROUP_CONCAT(area, ' · ') FROM job_places p WHERE p.job_id = j.id) areas
            FROM outcomes o JOIN jobs j ON j.id = o.job_id LEFT JOIN applications a ON a.job_id = j.id
            LEFT JOIN matches m ON m.job_id = j.id
            WHERE {' AND '.join(where)} ORDER BY {SORTS.get(sort, SORTS['recent'])}""", params).fetchall()


def board(rows: list[sqlite3.Row]) -> list[tuple[Column, list[sqlite3.Row]]]:
    return [(c, [r for r in rows if COLUMN_OF.get(r["stage"]) == c.key]) for c in COLUMNS]


def tiles(conn: sqlite3.Connection) -> dict[str, int]:
    by = dict(conn.execute("SELECT stage, COUNT(*) FROM outcomes o JOIN jobs j ON j.id = o.job_id "
                           "WHERE j.status = 'submitted' GROUP BY stage").fetchall())
    return {"applications": sum(by.values()), "assessments": by.get("assessment", 0),
            "interviews": by.get("interview", 0) + by.get("final", 0) + by.get("screen", 0),
            "offers": by.get("offer", 0) + by.get("accepted", 0)}


def applied_on(conn: sqlite3.Connection, day: date) -> int:
    return conn.execute("SELECT COUNT(*) FROM applications a JOIN jobs j ON j.id = a.job_id "
                        "WHERE j.status = 'submitted' AND substr(a.submitted_at, 1, 10) = ?",
                        (day.isoformat(),)).fetchone()[0]


def applied_since(conn: sqlite3.Connection, day: date) -> int:
    return conn.execute("SELECT COUNT(*) FROM applications a JOIN jobs j ON j.id = a.job_id "
                        "WHERE j.status = 'submitted' AND substr(a.submitted_at, 1, 10) >= ?",
                        (day.isoformat(),)).fetchone()[0]


def week(conn: sqlite3.Connection, today: date | None = None, due_ids: set[int] | None = None) -> dict:
    """This week (Sunday to Saturday) with a dot on days that have a next step, plus what's coming up."""
    today = today or date.today()
    start = today - timedelta(days=(today.weekday() + 1) % 7)
    days = [start + timedelta(days=i) for i in range(7)]
    events = conn.execute(
        """SELECT o.job_id, o.next_at, o.next_note, o.stage, j.title, j.company FROM outcomes o JOIN jobs j ON j.id = o.job_id
           WHERE o.next_at IS NOT NULL AND o.next_at >= ? AND j.status = 'submitted' ORDER BY o.next_at LIMIT 12""",
        ((today - timedelta(days=7)).isoformat(),)).fetchall()
    with_dot = {e["next_at"][:10] for e in events}
    items = []
    for e in events:
        d = _day(e["next_at"])
        delta = (d - today).days if d else 0
        when = ("Today" if delta == 0 else "Tomorrow" if delta == 1 else f"In {delta} days" if delta > 1
                else f"{-delta} day{'s' if delta < -1 else ''} ago")
        items.append({"job_id": e["job_id"], "company": e["company"], "title": e["title"],
                      "what": e["next_note"] or BY_KEY[e["stage"]].label, "when": when, "overdue": delta < 0,
                      "today": delta == 0})
    return {"days": [{"date": d, "dow": "SMTWTFS"[i], "today": d == today, "dot": d.isoformat() in with_dot}
                     for i, d in enumerate(days)], "events": items}


def weekly_series(conn: sqlite3.Connection, weeks: int = 12, today: date | None = None) -> list[tuple[str, int]]:
    """Applications submitted per week (Monday start), oldest first."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    rows = [r[0][:10] for r in conn.execute(
        "SELECT a.submitted_at FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE j.status = 'submitted' AND a.submitted_at IS NOT NULL AND substr(a.submitted_at, 1, 10) >= ?",
        (starts[0].isoformat(),))]
    out = []
    for s in starts:
        e = (s + timedelta(days=7)).isoformat()
        out.append((s.strftime("%b %d").replace(" 0", " "), sum(1 for d in rows if s.isoformat() <= d < e)))
    return out


def streak(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Days in a row (ending today or yesterday) on which you sent at least one application."""
    today = today or date.today()
    days = {r[0] for r in conn.execute(
        "SELECT DISTINCT substr(a.submitted_at, 1, 10) FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE j.status = 'submitted' AND a.submitted_at IS NOT NULL")}
    d = today if today.isoformat() in days else today - timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n
