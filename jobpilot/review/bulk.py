"""Answer once: the same open question across many waiting applications, answered in one go.

Each job you tick gets your answer as if you'd typed it on its review page (actor "you"),
so nothing changes about who answers what:
- Protected questions (work authorization, sponsorship, salary, EEO, veteran, disability)
  start with every job unticked. You tick each job the answer is true for.
- Legal and background-check consent never appear here. You tick those on each form.
- Files are never answered here.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field

from jobpilot.onboarding.profile import Profile
from jobpilot.review import plan
from jobpilot.review.mapping import NEVER_AUTOFILL

WAITING = ("ready_to_fill", "needs_human", "matched", "custom_site", "account_wall")


def norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).strip()


@dataclass
class Spot:
    job_id: int
    key: str
    title: str
    company: str
    score: int | None


@dataclass
class Question:
    gid: str
    label: str
    kind: str
    options: list[str]
    protected: str
    rememberable: bool
    spots: list[Spot] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.spots)


def _gid(label: str, options: list[str]) -> str:
    return hashlib.sha1((norm(label) + "|" + "|".join(sorted(options))).encode()).hexdigest()[:12]


def shared_questions(conn: sqlite3.Connection, min_jobs: int = 2) -> list[Question]:
    """Unanswered questions that come up in at least `min_jobs` waiting applications, most common first."""
    rows = conn.execute(
        f"""SELECT j.id, j.title, j.company, m.score FROM jobs j JOIN applications a ON a.job_id = j.id
            LEFT JOIN matches m ON m.job_id = j.id
            WHERE j.status IN ({','.join('?' * len(WAITING))}) ORDER BY m.score DESC, j.id""", WAITING).fetchall()
    groups: dict[str, Question] = {}
    for r in rows:
        app = plan.load_application(conn, r["id"])
        if not app:
            continue
        seen = set()
        for f in app.fields:
            if (not f.unanswered or f.kind == "file" or "resume" in (f.maps_to, f.key)
                    or f.protected in NEVER_AUTOFILL):
                continue  # only questions that hold an application up; optional ones can stay blank
            gid = _gid(f.label, f.options)
            if gid in seen:
                continue
            seen.add(gid)
            q = groups.setdefault(gid, Question(gid, f.label, f.kind, list(f.options), f.protected,
                                                plan.rememberable(f)))
            q.spots.append(Spot(r["id"], f.key, r["title"], r["company"], r["score"]))
    out = [q for q in groups.values() if q.count >= min_jobs]
    return sorted(out, key=lambda q: (-q.count, bool(q.protected), q.label.lower()))


def find(conn: sqlite3.Connection, gid: str) -> Question | None:
    return next((q for q in shared_questions(conn, 1) if q.gid == gid), None)


def answer(conn: sqlite3.Connection, profile: Profile, gid: str, value: str, job_ids: set[int],
           remember: bool = False) -> int:
    """Put your answer into each ticked job. Returns how many applications got it."""
    q = find(conn, gid)
    if q is None:
        raise plan.GateError("That question has already been answered everywhere.")
    value = value.strip()
    if not value:
        raise plan.GateError("Type an answer first.")
    if q.options and q.kind in ("select", "radio", "multi_select"):
        parts = value.split("; ")
        if any(p not in q.options for p in parts) or (q.kind != "multi_select" and len(parts) > 1):
            raise plan.GateError("Pick one of the form's options.")
    n = 0
    for s in q.spots:
        if s.job_id in job_ids:
            plan.save_answers(conn, s.job_id, profile, {s.key: value})
            n += 1
    if not n:
        raise plan.GateError("Tick at least one job.")
    if remember and q.rememberable and not q.protected:
        plan.remember_answers(profile, {q.label: value})
    return n
