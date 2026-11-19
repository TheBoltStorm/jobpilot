"""The application plan: what JobPilot would put in each field, and where each value came from.

Rules (from the spec, enforced here):
- Values only come from profile.yaml, your resume versions, or what you type on the review page.
- Anything without a value is NEEDS_HUMAN. Nothing is made up.
- Protected questions (work authorization, sponsorship, EEO/demographic, veteran,
  disability, salary, background check, legal) never get a value from the profile.
  You answer them per application; those answers stay on that one application.
- Written answers are DRAFTs until you approve the application.

Phase 4 plans the standard fields every ATS asks for. Phase 5 adds the questions
read from each real form and maps them onto the same keys.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field

from jobpilot.core import db
from jobpilot.core.db import now_iso
from jobpilot.core.protected import protected_category
from jobpilot.onboarding.profile import Profile
from jobpilot.review.status import PRE_APPROVAL, PREPARED, GateError, log_event, transition

SUPPORTED_ATS = ("greenhouse", "lever", "ashby")

FILLED, NEEDS_HUMAN, HUMAN, DRAFT = "filled", "needs_human", "human", "draft"


@dataclass
class PlanField:
    key: str
    label: str
    value: str = ""
    source: str = ""
    state: str = NEEDS_HUMAN  # filled | needs_human | human
    protected: str = ""  # protected category, if any
    optional: bool = False  # e.g. GPA, portfolio: fine to leave blank
    field_id: str = ""  # the form element (real forms only)
    kind: str = "text"  # text | textarea | file | select | multi_select | search_select | location
    options: list[str] = field(default_factory=list)
    section: str = ""
    maps_to: str = ""  # profile key, "protected:<category>" or "short:<question>"

    @property
    def unanswered(self) -> bool:
        return self.state == NEEDS_HUMAN and not self.optional


@dataclass
class Draft:
    question: str
    text: str = ""
    note: str = ""  # where the facts came from
    state: str = DRAFT


# (key, label, getter, source, optional)
def _standard(profile: Profile, resume_label: str) -> list[PlanField]:
    c, links = profile.contact, profile.links
    ed = profile.education[0] if profile.education else None
    loc = ", ".join(p for p in (c.city, c.state) if p)
    rows = [
        ("first_name", "First name", c.first_name, "profile: contact.first_name", False),
        ("last_name", "Last name", c.last_name, "profile: contact.last_name", False),
        ("preferred_name", "Preferred name", c.preferred_name, "profile: contact.preferred_name", True),
        ("email", "Email", c.email, "profile: contact.email", False),
        ("phone", "Phone", c.phone, "profile: contact.phone", False),
        ("location", "Current location", loc, "profile: contact.city, contact.state", False),
        ("country", "Country", c.country, "profile: contact.country", True),
        ("resume", "Resume", resume_label, "resume versions (default unless you pick another)", False),
        ("linkedin", "LinkedIn", links.linkedin, "profile: links.linkedin", True),
        ("github", "GitHub", links.github, "profile: links.github", True),
        ("portfolio", "Website / portfolio", links.portfolio, "profile: links.portfolio", True),
        ("school", "School", ed.school if ed else "", "profile: education[0].school", False),
        ("degree", "Degree", ed.degree if ed else "", "profile: education[0].degree", False),
        ("field_of_study", "Major / field of study", ed.field_of_study if ed else "",
         "profile: education[0].field_of_study", False),
        ("graduation", "Graduation date", ed.end if ed else "", "profile: education[0].end", False),
        ("gpa", "GPA", ed.gpa if ed else "", "profile: education[0].gpa", True),
        ("earliest_start", "Earliest start date", profile.targets.earliest_start, "profile: targets.earliest_start",
         True),
    ]
    return [PlanField(k, label, v, src if v else "not in your profile", FILLED if v else NEEDS_HUMAN, "", opt,
                      maps_to=k) for k, label, v, src, opt in rows]


# Questions nearly every application asks that only you may answer. Phase 5 swaps these
# for the real form's wording; they stay NEEDS_HUMAN either way.
TYPICAL_PROTECTED = [
    ("work_authorization", "Are you legally authorized to work in this job's country?", False),
    ("sponsorship", "Will you now or in the future require visa sponsorship?", False),
    ("salary", "Salary expectations (if asked)", True),
    ("eeo_demographic", "Voluntary self-identification: gender, race/ethnicity (EEO)", True),
    ("veteran", "Veteran status (voluntary)", True),
    ("disability", "Disability status (voluntary)", True),
    ("background_check", "Background-check consent (if asked)", True),
]


def build_fields(profile: Profile, resume_label: str) -> list[PlanField]:
    fields = _standard(profile, resume_label)
    for q, a in profile.short_answers.items():
        fields.append(PlanField(f"short:{q}", q, a, "profile: short_answers", FILLED if a else NEEDS_HUMAN,
                                maps_to=f"short:{q}"))
    for cat, label, optional in TYPICAL_PROTECTED:
        fields.append(PlanField(f"protected:{cat}", label, "", "only you can answer this", NEEDS_HUMAN, cat, optional,
                                maps_to=f"protected:{cat}"))
    return fields


def plan_fields(conn: sqlite3.Connection, job: sqlite3.Row, profile: Profile, resume_label: str) -> list[PlanField]:
    """The real form's questions when we have them, else the standard fields every ATS asks for."""
    from jobpilot.forms import load_form
    from jobpilot.review.mapping import plan_from_form

    from jobpilot.review.drafts import fill_template, is_template

    form = load_form(conn, job["id"])
    m = conn.execute("SELECT skills_json FROM matches WHERE job_id = ?", (job["id"],)).fetchone()
    skills = json.loads(m["skills_json"]).get("skills", []) if m else []
    if not form:
        fields = build_fields(profile, resume_label)
        for f in fields:  # saved answers written as templates become a DRAFT for this job
            if f.maps_to.startswith("short:") and is_template(f.value):
                f.value, f.state = fill_template(f.value, job, skills), DRAFT
                f.source = "your template (profile: short_answers), filled in for this job. Check it."
        return fields
    return plan_from_form(form[1], profile, resume_label, job, skills)


def merge_human(fresh: list[PlanField], saved: list[dict], keep_extra: bool = True) -> list[PlanField]:
    """Keep what you typed on the review page; refresh everything else from the profile.

    Answers carry over by question, or by what the question means (maps_to) when the plan
    switched from standard fields to the real form. A carried-over answer to a question
    with fixed options is only kept if it clearly matches one of them.
    """
    from jobpilot.review.mapping import fit_option

    human = {f["key"]: f for f in saved if f.get("state") == HUMAN}
    # Plans saved before maps_to existed used the meaning as the key ("email", "protected:sponsorship").
    by_meaning = {f.get("maps_to") or f["key"]: f for f in human.values()}
    out = []
    for f in fresh:
        h = human.pop(f.key, None)
        if h is None and f.maps_to and f.maps_to in by_meaning:
            h = by_meaning[f.maps_to]
            human.pop(h["key"], None)
        if h is not None:
            value = h["value"]
            if f.options and f.kind in ("select", "multi_select", "radio"):
                parts = [fit_option(v, f.options) for v in value.split("; ")] if value else []
                value = "; ".join(p for p in parts if p) if parts and all(parts) else ""
            if value or not h["value"]:
                f.value, f.source, f.state = value, h["source"], HUMAN
        out.append(f)
    if keep_extra:  # answers to questions that aren't in the fresh plan (e.g. added later)
        for h in human.values():
            out.append(PlanField(**{k: h[k] for k in PlanField.__dataclass_fields__ if k in h}))
    return out


@dataclass
class Application:
    job_id: int
    resume_label: str
    fields: list[PlanField]
    drafts: list[Draft] = field(default_factory=list)
    notes: str = ""
    approved_at: str | None = None
    approved_snapshot: str | None = None
    filled_at: str | None = None
    submitted_at: str | None = None
    rejected_at: str | None = None
    skipped_at: str | None = None
    prepared_at: str | None = None

    @property
    def unanswered(self) -> list[PlanField]:
        return [f for f in self.fields if f.unanswered]

    def snapshot(self) -> str:
        return json.dumps({"resume": self.resume_label, "fields": [asdict(f) for f in self.fields],
                           "drafts": [asdict(d) for d in self.drafts]}, sort_keys=True)

    def snapshot_hash(self) -> str:
        return hashlib.sha256(self.snapshot().encode()).hexdigest()[:12]


def _row_to_app(row: sqlite3.Row) -> Application:
    return Application(
        job_id=row["job_id"], resume_label=row["resume_label"],
        fields=[PlanField(**f) for f in json.loads(row["fields_json"])],
        drafts=[Draft(**d) for d in json.loads(row["drafts_json"])], notes=row["notes"],
        approved_at=row["approved_at"], approved_snapshot=row["approved_snapshot"], filled_at=row["filled_at"],
        submitted_at=row["submitted_at"], rejected_at=row["rejected_at"], skipped_at=row["skipped_at"],
        prepared_at=row["prepared_at"],
    )


def load_application(conn: sqlite3.Connection, job_id: int) -> Application | None:
    row = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_app(row) if row else None


def save_application(conn: sqlite3.Connection, app: Application) -> None:
    conn.execute(
        """INSERT INTO applications (job_id, resume_label, fields_json, drafts_json, notes, prepared_at, approved_at,
                                     approved_snapshot, filled_at, submitted_at, rejected_at, skipped_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET resume_label=excluded.resume_label, fields_json=excluded.fields_json,
             drafts_json=excluded.drafts_json, notes=excluded.notes, prepared_at=excluded.prepared_at,
             approved_at=excluded.approved_at, approved_snapshot=excluded.approved_snapshot,
             filled_at=excluded.filled_at, submitted_at=excluded.submitted_at, rejected_at=excluded.rejected_at,
             skipped_at=excluded.skipped_at, updated_at=excluded.updated_at""",
        (app.job_id, app.resume_label, json.dumps([asdict(f) for f in app.fields]),
         json.dumps([asdict(d) for d in app.drafts]), app.notes, app.prepared_at, app.approved_at,
         app.approved_snapshot, app.filled_at, app.submitted_at, app.rejected_at, app.skipped_at, now_iso()),
    )


def default_resume_label(conn: sqlite3.Connection, profile: Profile) -> str:
    if profile.default_resume and db.get_resume(conn, profile.default_resume):
        return profile.default_resume
    r = db.get_resume(conn)
    return r["label"] if r else ""


def bucket_for(job: sqlite3.Row, app: Application) -> str:
    """Which pre-approval bucket a prepared application belongs in."""
    if job["ats"] not in SUPPORTED_ATS:
        return "custom_site"
    return "needs_human" if app.unanswered else "ready_to_fill"


def prepare(conn: sqlite3.Connection, job_id: int, profile: Profile) -> Application:
    """Build (or refresh) the plan for a job. Moves matched/prepared jobs into their bucket.

    Found jobs keep their status (they weren't a match) but still get a plan you can review.
    Approved / filled / submitted / rejected jobs are left exactly as they are.
    """
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise GateError(f"no job {job_id}")
    app = load_application(conn, job_id)
    if app and job["status"] not in PRE_APPROVAL:
        return app
    label = app.resume_label if app and app.resume_label else default_resume_label(conn, profile)
    fresh = plan_fields(conn, job, profile, label)
    has_form = any(f.field_id for f in fresh)
    fields = merge_human(fresh, [asdict(f) for f in app.fields], keep_extra=not has_form) if app else fresh
    if app:
        app.fields, app.resume_label = fields, label
    else:
        app = Application(job_id, label, fields)
    app.prepared_at = now_iso()
    save_application(conn, app)
    if job["status"] in ("matched", *PREPARED):
        transition(conn, job_id, bucket_for(job, app), actor="system", action="prepared",
                   note=f"{len(app.unanswered)} questions need you")
    return app


def prepare_matched(conn: sqlite3.Connection, profile: Profile) -> int:
    ids = [r[0] for r in conn.execute("SELECT id FROM jobs WHERE status = 'matched'")]
    with conn:
        for job_id in ids:
            prepare(conn, job_id, profile)
    return len(ids)


# ---------------------------------------------------------------- your actions (actor="user")

def save_answers(conn: sqlite3.Connection, job_id: int, profile: Profile, values: dict[str, str],
                 drafts: list[Draft] | None = None, notes: str | None = None,
                 resume_label: str | None = None) -> Application:
    """Save edits from the review page. Changing anything withdraws an approval."""
    app = prepare(conn, job_id, profile)
    before = app.snapshot_hash()
    for f in app.fields:
        if f.key not in values:
            continue
        new = values[f.key].strip()
        if new == f.value:
            continue
        if new and f.options and f.kind in ("select", "multi_select", "radio"):
            bad = [v for v in new.split("; ") if v not in f.options]
            if bad or (f.kind != "multi_select" and "; " in new):
                raise GateError(f"'{f.label}': pick from the form's options")
        if new:
            f.value, f.state, f.source = new, HUMAN, f"you, on the review page ({now_iso()[:10]})"
        else:  # cleared: fall back to the profile value (rebuilt below), or NEEDS_HUMAN
            f.value, f.state = "", NEEDS_HUMAN
    if drafts is not None:
        app.drafts = [d for d in drafts if d.question.strip()]
    if notes is not None:
        app.notes = notes
    if resume_label is not None and resume_label != app.resume_label:
        if resume_label and not db.get_resume(conn, resume_label):
            raise GateError(f"no resume version called {resume_label!r}")
        app.resume_label = resume_label
        for f in app.fields:
            if f.key == "resume":
                f.value, f.state, f.source = resume_label, HUMAN, "you picked this resume version"
    # Refill cleared non-protected fields from the profile.
    job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    fresh = {f.key: f for f in plan_fields(conn, job_row, profile, app.resume_label)}
    for i, f in enumerate(app.fields):
        if f.state == NEEDS_HUMAN and f.key in fresh and fresh[f.key].state == FILLED:
            app.fields[i] = fresh[f.key]
    changed = app.snapshot_hash() != before
    save_application(conn, app)
    if changed:
        log_event(conn, job_id, "user", "edited")
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job["status"] in ("approved", "filled"):
            app.approved_at, app.approved_snapshot = None, None
            save_application(conn, app)
            transition(conn, job_id, bucket_for(job, app), actor="system", action="approval withdrawn",
                       note="answers changed after approval; approve again")
        elif job["status"] in PREPARED:
            transition(conn, job_id, bucket_for(job, app), actor="system", action="prepared",
                       note=f"{len(app.unanswered)} questions need you")
    return app


def approve(conn: sqlite3.Connection, job_id: int, profile: Profile) -> Application:
    app = prepare(conn, job_id, profile)
    note = f"{len(app.unanswered)} questions left blank for you to answer on the form" if app.unanswered else ""
    transition(conn, job_id, "approved", actor="user", action="approved", note=note)
    app.approved_at, app.approved_snapshot = now_iso(), app.snapshot()
    save_application(conn, app)
    return app


def reject(conn: sqlite3.Connection, job_id: int, reason: str = "") -> None:
    transition(conn, job_id, "rejected", actor="user", action="rejected", note=reason)
    conn.execute("UPDATE applications SET rejected_at = ? WHERE job_id = ?", (now_iso(), job_id))


def reopen(conn: sqlite3.Connection, job_id: int, profile: Profile, min_score: int) -> None:
    m = conn.execute("SELECT score FROM matches m JOIN jobs j ON j.id = m.job_id WHERE job_id = ? AND j.description != ''",
                     (job_id,)).fetchone()
    to = "matched" if m and m["score"] >= min_score else "found"
    transition(conn, job_id, to, actor="user", action="reopened")
    conn.execute("UPDATE applications SET rejected_at = NULL WHERE job_id = ?", (job_id,))
    if to == "matched":
        prepare(conn, job_id, profile)


def skip(conn: sqlite3.Connection, job_id: int) -> None:
    """Come back to it later: no status change, it just drops to the end of the queue."""
    if not conn.execute("SELECT 1 FROM applications WHERE job_id = ?", (job_id,)).fetchone():
        raise GateError("open the job once before skipping it")
    conn.execute("UPDATE applications SET skipped_at = ? WHERE job_id = ?", (now_iso(), job_id))
    log_event(conn, job_id, "user", "skipped")


def mark_submitted(conn: sqlite3.Connection, job_id: int) -> None:
    """You clicked Submit on the real site. JobPilot can't and won't do this part."""
    transition(conn, job_id, "submitted", actor="user", action="you confirmed you submitted")
    conn.execute("UPDATE applications SET submitted_at = ? WHERE job_id = ?", (now_iso(), job_id))


QUEUE_STATUSES = ("ready_to_fill", "needs_human", "matched", "custom_site", "account_wall")


def queue(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """What to review next: best matches first, skipped ones last."""
    return conn.execute(
        f"""SELECT j.id, j.title, j.company, j.status, m.score, a.skipped_at FROM jobs j
            LEFT JOIN matches m ON m.job_id = j.id LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status IN ({','.join('?' * len(QUEUE_STATUSES))})
            ORDER BY a.skipped_at IS NOT NULL, a.skipped_at,
                     EXISTS (SELECT 1 FROM application_events e WHERE e.job_id = j.id AND e.action = 'kept') DESC,
                     m.score DESC, j.id LIMIT ?""",
        (*QUEUE_STATUSES, limit)).fetchall()


def protected_label(label: str) -> str:
    """Category for a free-form question label, used when you add your own question."""
    return protected_category(label) or ""


def load_real_form(conn: sqlite3.Connection, job_id: int, profile: Profile, http, gh_tokens: dict[str, str],
                   llm_map=None) -> Application:
    """Fetch a job's real questions, then re-plan. An approval covered different questions, so it's withdrawn."""
    from jobpilot.forms import fetch_form

    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise GateError(f"no job {job_id}")
    try:
        fetch_form(conn, http, job, gh_tokens, llm_map, profile)
    except Exception as exc:  # HTTP errors, robots.txt refusals, bad JSON, unsupported sites
        raise GateError(f"Couldn't load the form: {exc}") from exc
    log_event(conn, job_id, "system", "loaded the real form questions")
    if job["status"] in ("approved", "filled"):
        app = load_application(conn, job_id)
        if app:
            app.approved_at, app.approved_snapshot = None, None
            save_application(conn, app)
        transition(conn, job_id, "needs_human", actor="system", action="approval withdrawn",
                   note="the real form's questions were loaded; review and approve again")
    return prepare(conn, job_id, profile)


def applied_elsewhere(conn: sqlite3.Connection, job_id: int, profile: Profile | None, on: str | None = None) -> None:
    """You applied to this job yourself, outside JobPilot. Record it so it's tracked like any other.

    Both steps are yours (actor="user"): the gate still only lets a person approve and mark submitted.
    """
    from jobpilot.review.outcomes import _valid_date

    job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise GateError(f"no job {job_id}")
    if job["status"] == "submitted":
        return
    when = (_valid_date(on) + "T12:00:00+00:00") if on else now_iso()
    app = load_application(conn, job_id)
    if app is None:
        if profile is not None and job["status"] in PRE_APPROVAL:
            app = prepare(conn, job_id, profile)
        else:
            app = Application(job_id, default_resume_label(conn, profile) if profile else "", [])
            save_application(conn, app)
    if job["status"] == "rejected":
        transition(conn, job_id, "found", actor="user", action="reopened")
    if job["status"] not in ("approved", "filled"):
        transition(conn, job_id, "approved", actor="user", action="you applied outside JobPilot")
        app = load_application(conn, job_id)
        app.approved_at, app.approved_snapshot = when, app.snapshot()
        save_application(conn, app)
    transition(conn, job_id, "submitted", actor="user", action="you applied on the site yourself")
    conn.execute("UPDATE applications SET submitted_at = ? WHERE job_id = ?", (when, job_id))


def rememberable(f: PlanField) -> bool:
    """Answers you can save for future applications: not protected, not a file, not a profile fact."""
    from jobpilot.review.mapping import PROFILE_KEYS

    return (not f.protected and not protected_category(f.label) and f.kind not in ("file",)
            and f.maps_to not in PROFILE_KEYS and len(f.label) <= 200)


def remember_answers(profile: Profile, answers: dict[str, str]) -> int:
    """Save answers (question -> answer) to your profile's reusable short answers. Returns how many changed."""
    n = 0
    for q, a in answers.items():
        q, a = q.strip(), a.strip()
        if not q or not a or protected_category(q):
            continue
        if profile.short_answers.get(q) != a:
            profile.short_answers[q] = a
            n += 1
    return n
