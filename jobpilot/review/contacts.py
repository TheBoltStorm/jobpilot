"""People you know at a company, and whether you've asked them for a referral.

Referrals turn into interviews far more often than cold applications, so JobPilot keeps a short
list per company and writes a DRAFT message you can copy. It never contacts anyone: you send it
yourself, then click "I asked".
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from jobpilot.core.db import now_iso
from jobpilot.sources.dedupe import company_key


@dataclass(frozen=True)
class State:
    key: str
    label: str


STATES = (State("to_ask", "To ask"), State("asked", "Asked"), State("referred", "Referred"),
          State("no", "Not this time"))
STATE_KEYS = {s.key for s in STATES}
LABEL = {s.key: s.label for s in STATES}


class ContactError(ValueError):
    pass


def _clean(s, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _link(s: str) -> str:
    s = _clean(s, 500)
    if not s:
        return ""
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s):
        return "mailto:" + s
    if not s.startswith(("http://", "https://")):
        raise ContactError("The link should be a web address (https://...) or an email address.")
    return s


def add(conn: sqlite3.Connection, company: str, name: str, role: str = "", link: str = "", note: str = "",
        job_id: int | None = None) -> int:
    company, name = _clean(company, 200), _clean(name, 200)
    if not company or not name:
        raise ContactError("Give the person's name and the company.")
    cur = conn.execute(
        """INSERT INTO contacts (company_key, company, name, role, link, note, job_id, added_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (company_key(company), company, name, _clean(role, 200), _link(link), _clean(note, 1000), job_id, now_iso()))
    return cur.lastrowid


def set_state(conn: sqlite3.Connection, contact_id: int, state: str) -> None:
    if state not in STATE_KEYS:
        raise ContactError(f"unknown state {state!r}")
    conn.execute("UPDATE contacts SET status = ?, asked_at = CASE WHEN ? = 'asked' THEN ? ELSE asked_at END "
                 "WHERE id = ?", (state, state, now_iso(), contact_id))


def remove(conn: sqlite3.Connection, contact_id: int) -> None:
    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))


def at_company(conn: sqlite3.Connection, company: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM contacts WHERE company_key = ? ORDER BY id",
                        (company_key(company),)).fetchall()


def everyone(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All contacts, with how many open (not yet submitted or rejected) jobs JobPilot has at their company."""
    return conn.execute(
        """SELECT c.*, (SELECT COUNT(*) FROM jobs j WHERE j.company_key = c.company_key
                        AND j.status NOT IN ('submitted', 'rejected', 'posting_closed')) open_jobs
           FROM contacts c ORDER BY c.status = 'to_ask' DESC, c.company COLLATE NOCASE, c.name""").fetchall()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT status, COUNT(*) FROM contacts GROUP BY status").fetchall())


def ask_draft(contact, title: str, company: str, url: str, profile) -> str:
    """A short referral ask. Facts only: the role, the link, your name. Labeled DRAFT on screen."""
    first = (contact["name"] or "").split()[0] if contact["name"] else "there"
    me = profile.full_name if profile else ""
    role = f"the {title} role" if title else "an open role"
    link = f" ({url})" if url else ""
    return (f"Hi {first},\n\nI'm applying for {role} at {company}{link} and I'd love to hear what it's like "
            f"on the team. If you think I'd be a good fit, would you be open to referring me? I'm happy to send "
            f"my resume and anything else that helps.\n\nThank you!\n{me}").strip()
