"""Plans for forms on any website, for the JobPilot browser button.

The browser button reads the form on the page you have open (labels, field types, options),
sends that to the dashboard on your computer, and gets back what it could fill. It shows you
every value and fills only after you click. The same rules as everywhere else apply:

- Values come only from your profile, your saved answers, or what you typed for this job.
- Protected questions (work authorization, sponsorship, EEO, veteran, disability, salary,
  background check) are filled only with an answer you gave for this one job, and legal or
  consent questions never are. Files and single checkboxes are left to you.
- Written DRAFT answers are offered unticked; you choose to use them.
- Sign-in and sign-up pages (any password field) are refused. JobPilot never submits.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from pathlib import Path

from jobpilot.forms.greenhouse import FormQuestion
from jobpilot.onboarding.profile import Profile
from jobpilot.review.mapping import NEVER_AUTOFILL, plan_from_form
from jobpilot.review.plan import DRAFT, FILLED, HUMAN, default_resume_label, load_application
from jobpilot.review.status import log_event
from jobpilot.sources.link import find_job

KINDS = {"text", "textarea", "select", "radio", "multi_select", "checkbox", "file", "password"}
NEVER_KINDS = {"file", "checkbox", "password"}
MAX_FIELDS = 300

SIGN_IN = ("This looks like a sign-in or sign-up page. JobPilot doesn't fill those or create accounts. "
           "Sign in yourself, then open the application form and try again.")


def extension_key(home: Path) -> str:
    """The pairing key the browser button sends with every request. Made once, kept in your data folder."""
    p = home / "extension_key.txt"
    if p.is_file():
        key = p.read_text(encoding="utf-8").strip()
        if len(key) >= 20:
            return key
    home.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(24)
    p.write_text(key, encoding="utf-8")
    return key


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).strip()


def _clean(text, n: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def questions_from_page(fields: list[dict]) -> list[FormQuestion]:
    out = []
    for i, f in enumerate(fields[:MAX_FIELDS]):
        kind = str(f.get("kind", "text"))
        out.append(FormQuestion(
            key=_clean(f.get("key") or f"f{i}", 80), field_id=_clean(f.get("name") or f.get("id"), 200),
            label=_clean(f.get("label"), 300).rstrip(" *") or "(no label)",
            kind=kind if kind in KINDS else "text", required=bool(f.get("required")),
            options=[_clean(o, 200) for o in (f.get("options") or [])[:200] if _clean(o, 200)]))
    return out


def plan_page(conn: sqlite3.Connection, profile: Profile, url: str, title: str, fields: list[dict],
              job_id: int | None = None) -> dict:
    """What may be filled on this page. Pure planning: nothing is stored.

    Fields that already have something in them are marked `present` and never filled again.
    `job_id` ties the page to a job you're filling from the dashboard (its URL may have moved on).
    """
    if any(str(f.get("kind")) == "password" for f in fields):
        return {"refused": SIGN_IN, "job": None, "fields": []}
    questions = questions_from_page(fields)
    if job_id is not None:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    else:
        job = find_job(conn, url) if url else None
    skills: list[dict] = []
    human: dict[str, str] = {}
    if job:
        m = conn.execute("SELECT skills_json FROM matches WHERE job_id = ?", (job["id"],)).fetchone()
        skills = json.loads(m["skills_json"]).get("skills", []) if m else []
        app = load_application(conn, job["id"])
        if app:
            human = {_norm(f.label): f.value for f in app.fields if f.state == HUMAN and f.value}
    ctx = dict(job) if job else {"title": _clean(title, 200) or "this role", "company": ""}
    planned = plan_from_form(questions, profile, default_resume_label(conn, profile), ctx, skills)
    out = []
    for raw, q, f in zip(fields, questions, planned):
        value, state, source = f.value, f.state, f.source
        mine = human.get(_norm(q.label))
        if mine and (not q.options or all(v in q.options for v in mine.split("; "))):
            value, state, source = mine, HUMAN, "your answer on this job's page in JobPilot"
        never = (q.kind in NEVER_KINDS or f.maps_to == "resume" or f.protected in NEVER_AUTOFILL
                 or (f.protected and state != HUMAN))
        if never:
            value = ""
            if q.kind == "file":
                source = "attach this yourself"
            elif f.protected:
                source = ("legal or consent question: you tick or answer it yourself" if f.protected in NEVER_AUTOFILL
                          else "only you can answer this (answer it on this job's page in JobPilot to reuse it)")
        here = bool(raw.get("present"))
        if here:
            source = "already filled on the page; left as it is"
        out.append({"key": q.key, "label": q.label, "kind": q.kind, "value": value, "source": source,
                    "state": state if value else "needs_human", "protected": f.protected,
                    "can_fill": bool(value), "fill": bool(value) and state in (FILLED, HUMAN) and not here,
                    "draft": bool(value) and state == DRAFT, "optional": not q.required, "present": here})
    jinfo = {"id": job["id"], "title": job["title"], "company": job["company"], "status": job["status"]} if job else None
    return {"refused": "", "job": jinfo, "fields": out}


def record_fill(conn: sqlite3.Connection, url: str, filled: int, left: int) -> int | None:
    """Note in the job's history that you filled a form with the browser button."""
    job = find_job(conn, url) if url else None
    if not job:
        return None
    log_event(conn, job["id"], "user", "filled the form with the browser button",
              note=f"{filled} filled, {left} left for you; you submit it yourself")
    return job["id"]
