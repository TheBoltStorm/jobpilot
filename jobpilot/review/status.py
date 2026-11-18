"""Job statuses and the human verification gate.

The gate is code, not configuration: there is no setting that changes it.
- Only a person (actor="user", which only the dashboard's buttons and explicit CLI
  commands pass) can approve, mark submitted, or reject.
- Autofill (Phase 5) may only move an *approved* job to *filled*, and it stops on
  the final page. "submitted" is only ever set when you say you clicked Submit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from jobpilot.core.db import now_iso


@dataclass(frozen=True)
class Status:
    key: str
    label: str
    hint: str


STATUSES: tuple[Status, ...] = (
    Status("found", "Found", "Discovered, not scored as a match yet"),
    Status("matched", "Matched", "Scored at or above your minimum, not reviewed yet"),
    Status("ready_to_fill", "Ready to fill", "Every field has an answer; waiting for your approval"),
    Status("needs_human", "Needs human", "Some questions only you can answer"),
    Status("account_wall", "Account wall", "The site wants you to sign in or make an account first"),
    Status("custom_site", "Custom site", "Not a Greenhouse/Lever/Ashby form; apply on the site yourself"),
    Status("posting_closed", "Posting closed", "No longer listed by its source"),
    Status("approved", "Approved", "You approved it; autofill may fill the form (it never submits)"),
    Status("filled", "Filled", "Form filled and left open on the last page for you to submit"),
    Status("submitted", "Submitted", "You confirmed you clicked Submit"),
    Status("rejected", "Rejected", "You passed on it"),
)
STATUS_KEYS = tuple(s.key for s in STATUSES)
LABELS = {s.key: s.label for s in STATUSES}

# Before you approve. Preparing and closing only ever touch these.
PRE_APPROVAL = ("found", "matched", "ready_to_fill", "needs_human", "account_wall", "custom_site")
PREPARED = ("ready_to_fill", "needs_human", "account_wall", "custom_site")
# Statuses only a person can set.
USER_ONLY = frozenset({"approved", "submitted", "rejected"})

ALLOWED: dict[str, frozenset[str]] = {
    # system moves
    **{s: frozenset({*PREPARED, "posting_closed", "approved", "rejected"}) for s in PRE_APPROVAL},
    # matched/found only flow into each other via scoring, which writes them directly
    "posting_closed": frozenset({"found", "rejected"}),
    "approved": frozenset({"filled", "submitted", "rejected", *PREPARED}),  # PREPARED = approval withdrawn
    "filled": frozenset({"submitted", "approved", "rejected", *PREPARED}),
    "submitted": frozenset(),  # final
    "rejected": frozenset({"found", "matched", *PREPARED}),  # reopen
}
# Which of those moves the system may make by itself. Everything else needs actor="user".
SYSTEM_ALLOWED: dict[str, frozenset[str]] = {
    **{s: frozenset({*PREPARED, "posting_closed"}) for s in PRE_APPROVAL},
    "posting_closed": frozenset({"found"}),
    "approved": frozenset({"filled", *PREPARED}),
    "filled": frozenset({*PREPARED}),
    "submitted": frozenset(),
    "rejected": frozenset(),
}


class GateError(Exception):
    """A status change the human verification gate doesn't allow."""


def check(current: str, to: str, actor: str) -> None:
    if actor not in ("user", "system"):
        raise GateError(f"unknown actor {actor!r}")
    if to not in STATUS_KEYS:
        raise GateError(f"unknown status {to!r}")
    if to in USER_ONLY and actor != "user":
        raise GateError(f"only you can mark a job '{LABELS[to]}'")
    if to not in ALLOWED.get(current, frozenset()):
        raise GateError(f"can't go from '{LABELS.get(current, current)}' to '{LABELS[to]}'")
    if actor == "system" and to not in SYSTEM_ALLOWED.get(current, frozenset()):
        raise GateError(f"'{LABELS.get(current, current)}' to '{LABELS[to]}' needs you to do it")


def log_event(conn: sqlite3.Connection, job_id: int, actor: str, action: str, from_status: str = "",
              to_status: str = "", note: str = "") -> None:
    conn.execute(
        """INSERT INTO application_events (job_id, at, actor, action, from_status, to_status, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", (job_id, now_iso(), actor, action, from_status, to_status, note))


def transition(conn: sqlite3.Connection, job_id: int, to: str, *, actor: str, action: str = "", note: str = "") -> str:
    """Move a job to a new status (checked against the gate) and log it. Returns the old status."""
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise GateError(f"no job {job_id}")
    current = row["status"]
    if current == to:
        return current
    check(current, to, actor)
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (to, job_id))
    log_event(conn, job_id, actor, action or to, current, to, note)
    return current
