"""After you apply: what happened, when to follow up, and what's working.

- Outcome stages are yours to set (JobPilot never changes them): waiting, recruiter screen,
  assessment, interviewing, final round, offer, and the closed ones (accepted, declined,
  not selected, withdrawn, no response).
- Follow-ups: when an application has gone quiet for a while, it shows up as due with a short
  DRAFT message built only from facts (the role, the company, the date you applied, your name).
  JobPilot never sends anything. You send it yourself, then click "I sent it" so the date is recorded.
- Stats: response, interview and offer rates by source, field, score, resume and more.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from jobpilot.core.db import now_iso
from jobpilot.review.status import GateError, log_event


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    active: bool  # still in play (follow-ups can be due)
    responded: bool  # the employer got back to you
    interview: bool = False


STAGES = [
    Stage("waiting", "Waiting to hear back", True, False),
    Stage("screen", "Recruiter screen", True, True),
    Stage("assessment", "Assessment / take-home", True, True),
    Stage("interview", "Interviewing", True, True, True),
    Stage("final", "Final round", True, True, True),
    Stage("offer", "Offer", True, True, True),
    Stage("accepted", "Accepted", False, True, True),
    Stage("declined", "You declined", False, True),
    Stage("rejected", "Not selected", False, True),
    Stage("withdrawn", "You withdrew", False, False),
    Stage("no_response", "No response", False, False),
]
BY_KEY = {s.key: s for s in STAGES}
REACHED = {s.key: i for i, s in enumerate(STAGES)}
# Stages that mean "got at least this far", used for interview counts in stats.
INTERVIEW_STAGES = {"interview", "final", "offer", "accepted"}
OFFER_STAGES = {"offer", "accepted", "declined"}


@dataclass
class FollowupRules:
    first_after_days: int = 7
    then_every_days: int = 7
    max_followups: int = 2
    quiet_after_days: int = 30  # suggest marking "No response" after this long with nothing back

    @classmethod
    def from_settings(cls, s) -> "FollowupRules":
        f = s.followups
        return cls(f.first_after_days, f.then_every_days, f.max_followups, f.quiet_after_days)


def _day(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(iso[:10])
        except ValueError:
            return None


# ---------------------------------------------------------------- storage

def ensure(conn: sqlite3.Connection) -> int:
    """Every submitted application gets an outcome row (stage: waiting) the first time it's seen."""
    cur = conn.execute(
        """INSERT INTO outcomes (job_id, stage, stage_at)
           SELECT j.id, 'waiting', COALESCE(a.submitted_at, j.last_seen) FROM jobs j
           LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.status = 'submitted' AND j.id NOT IN (SELECT job_id FROM outcomes)""")
    return cur.rowcount


def load(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM outcomes WHERE job_id = ?", (job_id,)).fetchone()


def _submitted(conn, job_id: int) -> sqlite3.Row:
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise GateError("no such job")
    if job["status"] != "submitted":
        raise GateError("outcomes are for applications you've submitted; mark it submitted first")
    ensure(conn)
    return job


def set_stage(conn: sqlite3.Connection, job_id: int, stage: str, note: str = "", on: str | None = None) -> None:
    """You record what happened. `on` is the date it happened (YYYY-MM-DD), default today."""
    if stage not in BY_KEY:
        raise GateError(f"unknown stage {stage!r}")
    _submitted(conn, job_id)
    row = load(conn, job_id)
    when = _valid_date(on) if on else now_iso()
    if row["stage"] == stage and not note:
        return
    responded = row["responded_at"]
    if BY_KEY[stage].responded and not responded:
        responded = when
    conn.execute("UPDATE outcomes SET stage = ?, stage_at = ?, responded_at = ? WHERE job_id = ?",
                 (stage, when, responded, job_id))
    log_event(conn, job_id, "user", f"outcome: {BY_KEY[stage].label}", note=note)


def set_next(conn: sqlite3.Connection, job_id: int, on: str | None, note: str = "") -> None:
    """A date for the next step (interview, deadline...). Follow-ups wait until after it."""
    _submitted(conn, job_id)
    conn.execute("UPDATE outcomes SET next_at = ?, next_note = ? WHERE job_id = ?",
                 (_valid_date(on) if on else None, note.strip()[:200], job_id))


def followup_sent(conn: sqlite3.Connection, job_id: int, on: str | None = None) -> None:
    """You sent a follow-up yourself. JobPilot only records the date."""
    _submitted(conn, job_id)
    when = _valid_date(on) if on else now_iso()
    conn.execute("UPDATE outcomes SET followups = followups + 1, last_followup_at = ? WHERE job_id = ?",
                 (when, job_id))
    log_event(conn, job_id, "user", "you sent a follow-up")


def mute(conn: sqlite3.Connection, job_id: int, muted: bool = True) -> None:
    _submitted(conn, job_id)
    conn.execute("UPDATE outcomes SET muted = ? WHERE job_id = ?", (int(muted), job_id))


def _valid_date(text: str) -> str:
    try:
        return date.fromisoformat(text.strip()[:10]).isoformat()
    except ValueError:
        raise GateError(f"dates look like 2026-11-30, not {text!r}") from None


# ---------------------------------------------------------------- what's due

@dataclass
class Due:
    job_id: int
    title: str
    company: str
    stage: str
    kind: str  # followup | thank_you | quiet
    days: int  # days since the last thing happened
    submitted: date | None
    followups: int


def due(conn: sqlite3.Connection, rules: FollowupRules, today: date | None = None) -> list[Due]:
    today = today or date.today()
    ensure(conn)
    out: list[Due] = []
    rows = conn.execute(
        """SELECT o.*, j.title, j.company, a.submitted_at FROM outcomes o JOIN jobs j ON j.id = o.job_id
           LEFT JOIN applications a ON a.job_id = j.id WHERE o.muted = 0""").fetchall()
    for r in rows:
        st = BY_KEY.get(r["stage"])
        if not st or not st.active:
            continue
        nxt = _day(r["next_at"])
        if nxt and nxt >= today:
            continue  # something is scheduled; nothing to chase yet
        dates = [d for d in (_day(r["stage_at"]), _day(r["last_followup_at"]), nxt, _day(r["submitted_at"])) if d]
        last = max(dates) if dates else today
        days = (today - last).days
        sub = _day(r["submitted_at"]) or _day(r["stage_at"])
        base = Due(r["job_id"], r["title"], r["company"], r["stage"], "followup", days, sub, r["followups"])
        if st.key in ("interview", "final") and nxt and (today - nxt).days <= 2 and not r["last_followup_at"]:
            base.kind = "thank_you"  # interview just happened
            out.append(base)
            continue
        gap = rules.first_after_days if r["followups"] == 0 else rules.then_every_days
        if r["followups"] < rules.max_followups and days >= gap:
            out.append(base)
        elif st.key == "waiting" and sub and (today - sub).days >= rules.quiet_after_days:
            base.kind = "quiet"
            out.append(base)
    order = {"thank_you": 0, "followup": 1, "quiet": 2}
    return sorted(out, key=lambda d: (order[d.kind], -d.days))


def upcoming(conn: sqlite3.Connection, today: date | None = None) -> list[sqlite3.Row]:
    today = today or date.today()
    return conn.execute(
        """SELECT o.*, j.title, j.company FROM outcomes o JOIN jobs j ON j.id = o.job_id
           WHERE o.next_at >= ? ORDER BY o.next_at""", (today.isoformat(),)).fetchall()


# ---------------------------------------------------------------- drafts

def draft(kind: str, title: str, company: str, submitted: date | None, profile) -> tuple[str, str]:
    """(subject, body). Facts only: role, company, date, your name and email. Labeled DRAFT on screen."""
    name = profile.full_name if profile else ""
    email = profile.contact.email if profile else ""
    sign = "\n".join(x for x in (name, email) if x)
    when = f" on {submitted.strftime('%B')} {submitted.day}" if submitted else ""
    if kind == "thank_you":
        subject = f"Thank you: {title} interview"
        body = (f"Hi,\n\nThank you for taking the time to talk with me about the {title} role at {company}. "
                f"I appreciated learning more about the team and the work, and I'm still interested in the position. "
                f"Please let me know if there's anything else I can share.\n\nBest regards,\n{sign}")
    else:
        subject = f"Following up on my application: {title}"
        body = (f"Hi,\n\nI applied for the {title} role at {company}{when} and wanted to follow up. "
                f"I'm still interested in the position and happy to share anything else that would help.\n\n"
                f"Thank you for your time,\n{sign}")
    return subject, body


# ---------------------------------------------------------------- stats

@dataclass
class Rate:
    group: str
    applied: int = 0
    responded: int = 0
    interviews: int = 0
    offers: int = 0

    def pct(self, n: int) -> int:
        return round(100 * n / self.applied) if self.applied else 0


def _score_band(score) -> str:
    if score is None:
        return "not scored"
    return "75+" if score >= 75 else "60-74" if score >= 60 else "under 60"


def stats(conn: sqlite3.Connection) -> dict:
    """Rates overall and by source, ATS, field, score, resume and qualify verdict."""
    ensure(conn)
    rows = conn.execute(
        """SELECT o.stage, o.responded_at, o.stage_at, o.followups, a.submitted_at, a.resume_label, j.ats, m.score, m.tags,
                  q.status AS qual,
                  (SELECT source FROM job_sources s WHERE s.job_id = j.id ORDER BY s.first_seen LIMIT 1) AS source
           FROM outcomes o JOIN jobs j ON j.id = o.job_id LEFT JOIN applications a ON a.job_id = j.id
           LEFT JOIN matches m ON m.job_id = j.id LEFT JOIN qualifications q ON q.job_id = j.id""").fetchall()
    overall = Rate("All applications")
    groups: dict[str, dict[str, Rate]] = {k: {} for k in
                                          ("Source", "Site", "Field", "Match score", "Resume", "Minimums")}
    waits: list[int] = []
    for r in rows:
        resp = bool(BY_KEY.get(r["stage"]) and BY_KEY[r["stage"]].responded) or bool(r["responded_at"])
        inter = r["stage"] in INTERVIEW_STAGES
        offer = r["stage"] in OFFER_STAGES
        keys = {
            "Source": (r["source"] or "unknown").split(":")[0],
            "Site": r["ats"] or "other",
            "Field": (r["tags"] or "other").split(",")[0] or "other",
            "Match score": _score_band(r["score"]),
            "Resume": "tailored" if "-for-job-" in (r["resume_label"] or "") else "standard",
            "Minimums": {"qualify": "met all", "close": "one short", "not": "two+ short"}.get(r["qual"], "not checked"),
        }
        for rate in [overall] + [groups[g].setdefault(v, Rate(v)) for g, v in keys.items()]:
            rate.applied += 1
            rate.responded += resp
            rate.interviews += inter
            rate.offers += offer
        a, b = _day(r["submitted_at"]), _day(r["responded_at"])
        if a and b and b >= a:
            waits.append((b - a).days)
    waits.sort()
    by_stage = dict(conn.execute("SELECT stage, COUNT(*) FROM outcomes GROUP BY stage").fetchall())
    return {"overall": overall, "groups": {g: sorted(v.values(), key=lambda x: -x.applied) for g, v in groups.items()},
            "median_days_to_reply": waits[len(waits) // 2] if waits else None, "by_stage": by_stage}
