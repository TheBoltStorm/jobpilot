"""A tailored resume version for one job: the same content, in a different order.

What tailoring may do:
- move the bullets inside each job or project so the ones that show what this posting asks for come first;
- move projects (not jobs: those stay in date order) so the most relevant come first;
- move the skills the posting asks for to the front of the skills list.
What it never does: add, remove, reword or merge anything. Before a file is written, the
tailored version's lines are checked against the original's (same lines, same number of
times). If they differ, nothing is written.

You see the changes as a diff on the job's review page and approve them before a file
(a plain DOCX built from your profile) is created. The new version is then picked for that
application; your other resume versions are untouched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from jobpilot.core.db import now_iso
from jobpilot.onboarding.profile import Profile
from jobpilot.review.status import PRE_APPROVAL, GateError, log_event

WEIGHT = {"required": 2, "preferred": 1, "mentioned": 0}
EDITABLE = (*PRE_APPROVAL, "approved", "filled")


# ---------------------------------------------------------------- content

def blocks(p: Profile) -> list[tuple[str, str]]:
    """The resume as (kind, text) lines, in order. kind: name | contact | section | head | bullet | skill | cert."""
    c, ln = p.contact, p.links
    out: list[tuple[str, str]] = [("name", p.full_name)] if p.full_name else []
    place = ", ".join(x for x in (c.city, c.state) if x)
    contact = " | ".join(x for x in (place, c.phone, c.email, ln.github, ln.linkedin, ln.portfolio, *ln.other) if x)
    if contact:
        out.append(("contact", contact))
    if p.education:
        out.append(("section", "Education"))
        for e in p.education:
            out.append(("head", " | ".join(x for x in (e.school, _degree(e), _dates(e.start, e.end)) if x)))
            if e.gpa:
                out.append(("bullet", f"GPA: {e.gpa}"))
            if e.coursework:
                out.append(("bullet", "Relevant Coursework: " + ", ".join(e.coursework)))
    if p.experience:
        out.append(("section", "Experience"))
        for x in p.experience:
            out.append(("head", " | ".join(v for v in (x.title, x.company, x.location, _dates(x.start, x.end)) if v)))
            out += [("bullet", b) for b in x.bullets]
    if p.projects:
        out.append(("section", "Projects"))
        for pr in p.projects:
            out.append(("head", " | ".join(v for v in (pr.name, pr.organization, _dates(pr.start, pr.end)) if v)))
            out += [("bullet", b) for b in pr.bullets]
    if p.skills:
        out.append(("section", "Skills"))
        out += [("skill", s) for s in p.skills]
    if p.certifications:
        out.append(("section", "Certifications"))
        out += [("cert", s) for s in p.certifications]
    return out


def _degree(e) -> str:
    return " in ".join(x for x in (e.degree, e.field_of_study) if x)


def _dates(start: str, end: str) -> str:
    return " - ".join(x for x in (start, end) if x)


def same_content(a: Profile, b: Profile) -> bool:
    return Counter(blocks(a)) == Counter(blocks(b))


# ---------------------------------------------------------------- reordering

def _wanted(skills: list[dict]) -> dict[str, int]:
    """skill key -> weight, for the skills this posting asks for that you have (or partly have)."""
    out: dict[str, int] = {}
    for s in skills:
        if s.get("status") in ("have", "partial") and WEIGHT.get(s.get("importance", ""), 0):
            out[s["key"]] = max(out.get(s["key"], 0), WEIGHT[s["importance"]])
    return out


def _score(text: str, wanted: dict[str, int], taxonomy) -> int:
    return sum(wanted.get(k, 0) for k in taxonomy.find(text))


def _stable(items: list, key) -> list:
    # Highest score first; ties keep their original order.
    return [x for _, x in sorted(enumerate(items), key=lambda t: (-key(t[1]), t[0]))]


def tailor(profile: Profile, skills: list[dict], taxonomy) -> Profile:
    wanted = _wanted(skills)
    t = profile.model_copy(deep=True)
    for x in t.experience:
        x.bullets = _stable(x.bullets, lambda b: _score(b, wanted, taxonomy))
    for pr in t.projects:
        pr.bullets = _stable(pr.bullets, lambda b: _score(b, wanted, taxonomy))
    t.projects = _stable(t.projects, lambda pr: _score(" ".join([pr.name, *pr.bullets]), wanted, taxonomy))
    t.skills = _stable(t.skills, lambda s: _score(s, wanted, taxonomy))
    if not same_content(profile, t):  # can't happen; checked anyway
        raise GateError("tailoring changed the resume's content; nothing was written")
    return t


@dataclass
class Move:
    section: str
    text: str
    old: int  # 1-based position within its group (a job's bullets, the projects, the skills)
    new: int
    group: str = ""


@dataclass
class Diff:
    moves: list[Move] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.moves


def diff(before: Profile, after: Profile) -> Diff:
    d = Diff()

    def compare(section, group, a: list[str], b: list[str]):
        for new, item in enumerate(b, 1):
            old = a.index(item) + 1
            if old != new:
                d.moves.append(Move(section, item, old, new, group))

    for xa, xb in zip(before.experience, after.experience):
        compare("Experience", f"{xa.title} @ {xa.company}".strip(" @"), xa.bullets, xb.bullets)
    compare("Projects", "project order", [p.name for p in before.projects], [p.name for p in after.projects])
    by_name = {p.name: p for p in before.projects}
    for pb in after.projects:
        compare("Projects", pb.name, by_name[pb.name].bullets, pb.bullets)
    compare("Skills", "skills list", before.skills, after.skills)
    return d


# ---------------------------------------------------------------- DOCX

def write_docx(p: Profile, path: Path) -> Path:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    skills: list[str] = []
    certs: list[str] = []

    def flush():
        if skills:
            doc.add_paragraph(", ".join(skills))
            skills.clear()
        if certs:
            for c in certs:
                doc.add_paragraph(c, style="List Bullet")
            certs.clear()

    for kind, text in blocks(p):
        if kind == "name":
            doc.add_heading(text, level=0)
        elif kind == "contact":
            doc.add_paragraph(text)
        elif kind == "section":
            flush()
            doc.add_heading(text, level=1)
        elif kind == "head":
            doc.add_paragraph().add_run(text).bold = True
        elif kind == "bullet":
            doc.add_paragraph(text, style="List Bullet")
        elif kind == "skill":
            skills.append(text)
        elif kind == "cert":
            certs.append(text)
    flush()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


# ---------------------------------------------------------------- stored proposals

def _job_skills(conn, job_id: int) -> list[dict]:
    m = conn.execute("SELECT skills_json FROM matches WHERE job_id = ?", (job_id,)).fetchone()
    return json.loads(m["skills_json"]).get("skills", []) if m else []


def load(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tailored_resumes WHERE job_id = ?", (job_id,)).fetchone()


def _check(conn, job_id: int):
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise GateError("no such job")
    if job["status"] not in EDITABLE:
        raise GateError("this application can't change any more")
    return job


def propose(conn: sqlite3.Connection, job_id: int, profile: Profile, taxonomy) -> Diff:
    """Work out the reordering and store it for your approval. Writes no file."""
    _check(conn, job_id)
    after = tailor(profile, _job_skills(conn, job_id), taxonomy)
    d = diff(profile, after)
    if d.empty:
        raise GateError("your resume is already in the best order for this job; nothing to change")
    app = conn.execute("SELECT resume_label FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    base = (app["resume_label"] if app and app["resume_label"] else "") or profile.default_resume or "resume"
    base = base.split("-for-job-")[0]
    conn.execute(
        """INSERT INTO tailored_resumes (job_id, base_label, label, profile_hash, tailored_json, diff_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET base_label=excluded.base_label, label=excluded.label,
             profile_hash=excluded.profile_hash, tailored_json=excluded.tailored_json, diff_json=excluded.diff_json,
             created_at=excluded.created_at, approved_at=NULL, path=NULL""",
        (job_id, base, f"{base}-for-job-{job_id}", profile_hash(profile), after.model_dump_json(),
         json.dumps([m.__dict__ for m in d.moves]), now_iso()))
    log_event(conn, job_id, "user", "tailored resume suggested")
    return d


def moves(row) -> list[Move]:
    return [Move(**m) for m in json.loads(row["diff_json"])] if row else []


def profile_hash(p: Profile) -> str:
    return hashlib.sha256(json.dumps(blocks(p)).encode()).hexdigest()[:16]


def approve(conn: sqlite3.Connection, job_id: int, profile: Profile, resumes_dir: Path) -> str:
    """You approved the diff: write the DOCX, add it as a resume version, pick it for this application."""
    from jobpilot.review import plan

    _check(conn, job_id)
    row = load(conn, job_id)
    if not row:
        raise GateError("there's no tailored version to approve; suggest one first")
    if row["profile_hash"] != profile_hash(profile):
        raise GateError("your profile changed since this was suggested; suggest it again")
    after = Profile.model_validate_json(row["tailored_json"])
    if not same_content(profile, after):
        raise GateError("the tailored version doesn't have exactly your resume's content; nothing was written")
    path = write_docx(after, resumes_dir / f"{row['label']}.docx")
    text = "\n".join(t for _, t in blocks(after))
    db_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.execute("""INSERT INTO resumes (label, path, sha256, text, parsed_json, is_default, added_at)
                    VALUES (?, ?, ?, ?, '{}', 0, ?)
                    ON CONFLICT(label) DO UPDATE SET path=excluded.path, sha256=excluded.sha256, text=excluded.text,
                      added_at=excluded.added_at""", (row["label"], str(path), db_sha, text, now_iso()))
    conn.execute("UPDATE tailored_resumes SET approved_at = ?, path = ? WHERE job_id = ?", (now_iso(), str(path), job_id))
    log_event(conn, job_id, "user", "tailored resume approved", note=row["label"])
    plan.save_answers(conn, job_id, profile, {}, resume_label=row["label"])
    return row["label"]


def discard(conn: sqlite3.Connection, job_id: int) -> None:
    row = load(conn, job_id)
    if row and row["approved_at"]:
        raise GateError("this tailored version is already a resume version; pick another resume instead")
    conn.execute("DELETE FROM tailored_resumes WHERE job_id = ?", (job_id,))
    log_event(conn, job_id, "user", "tailored resume discarded")
