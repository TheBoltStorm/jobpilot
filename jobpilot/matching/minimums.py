"""Do you meet a posting's minimum requirements? Checked from the posting's own text.

For each job with a description, JobPilot looks at the *required* part of the posting and checks:
- required skills: each one is in your profile (in experience, projects, skills or coursework);
- degree level: the posting doesn't require a grad degree you don't have;
- years of experience: "3+ years of experience" vs. the time in your experience entries;
- graduation date: "graduating between Dec 2026 and Jun 2027" vs. your expected graduation;
- GPA: "minimum GPA of 3.0" vs. your GPA.

Verdicts:
- qualify: every requirement it could find is met;
- close: exactly one isn't;
- not: two or more aren't;
- unknown: no description, or no specific requirements to check.

It never decides anything that's yours alone. If a posting says it won't sponsor visas or needs a
security clearance or citizenship, that's shown as a note for you; it doesn't change the verdict.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date

from jobpilot.matching.local import _degree_mismatch
from jobpilot.matching.skills import split_sections
from jobpilot.onboarding.profile import Profile

QUALIFY, CLOSE, NOT, UNKNOWN = "qualify", "close", "not", "unknown"
RULES_VERSION = 2  # bump when the checks change, so stored verdicts are redone

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_SEASON = {"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 12}
_YEARS = re.compile(
    r"(?<![\d.])(\d{1,2})\s*\+?\s*(?:(?:-|to)\s*\d{1,2}\s*)?\+?\s*years?(?:['’]s?)?\s+(?:of\s+)?"
    r"(?:(?:professional|relevant|industry|work|full[- ]time|hands[- ]on|practical|related|commercial|"
    r"software|engineering|development|programming)\s+)*(?:work\s+)?experience", re.IGNORECASE)
_GRAD = re.compile(r"graduat\w*[^.;\n]{0,90}", re.IGNORECASE)
_YEAR = re.compile(r"\b(20[2-3]\d)\b")
_GPA = re.compile(r"(?:gpa|grade point average)[^.;\n\d]{0,40}(\d\.\d{1,2})|(\d\.\d{1,2})\s*(?:/\s*4\.0\s*)?"
                  r"(?:cumulative\s+)?(?:gpa|grade point)", re.IGNORECASE)
_NO_SPONSOR = re.compile(r"\b(?:(?:unable|not able|will not|won['’]t|cannot|can['’]t|do(?:es)? not|not)\s+(?:to\s+)?"
                         r"(?:provide\s+|offer\s+)?(?:visa\s+)?sponsor|no\s+(?:visa\s+)?sponsorship)", re.IGNORECASE)
_CLEARANCE = re.compile(r"\b(security clearance|clearance required|ts/sci|secret clearance|"
                        r"must be a u\.?s\.? citizen|u\.?s\.? citizenship (?:is )?required|itar)\b", re.IGNORECASE)


@dataclass
class Check:
    what: str
    ok: bool | None  # None: couldn't tell
    detail: str


@dataclass
class Verdict:
    status: str
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def unmet(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    def to_json(self) -> str:
        return json.dumps({"checks": [asdict(c) for c in self.checks], "notes": self.notes})


# ---------------------------------------------------------------- your side

def _month(text: str) -> tuple[int, int] | None:
    """'May 2027' / '05/2027' / 'Spring 2027' / '2027' -> (year, month)."""
    t = (text or "").strip().lower()
    if t in ("present", "current", "now"):
        d = date.today()
        return d.year, d.month
    y = _YEAR.search(t)
    if not y:
        return None
    for k, v in {**_MONTHS, **_SEASON}.items():
        if k in t:
            return int(y.group()), v
    m = re.search(r"\b(\d{1,2})\s*/\s*20\d\d", t)
    return int(y.group()), int(m.group(1)) if m and 1 <= int(m.group(1)) <= 12 else 6


def experience_years(profile: Profile) -> float:
    """Total time in your experience entries (overlaps counted once)."""
    months: set[tuple[int, int]] = set()
    for x in profile.experience:
        a, b = _month(x.start), _month(x.end or "present")
        if not a or not b:
            continue
        y, m = a
        while (y, m) <= b and len(months) < 600:
            months.add((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return len(months) / 12


def grad_date(profile: Profile) -> tuple[int, int] | None:
    ends = [_month(e.end) for e in profile.education if e.end]
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def _gpa(profile: Profile) -> float | None:
    for e in profile.education:
        m = re.search(r"\d\.\d+", e.gpa or "")
        if m:
            return float(m.group())
    return None


# ---------------------------------------------------------------- the check

def evaluate(job, profile: Profile, skills: list[dict]) -> Verdict:
    desc = job["description"] or ""
    if not desc.strip():
        return Verdict(UNKNOWN, notes=["No description yet, so there's nothing to check."])
    sections = split_sections(desc)
    req_text = sections["required"] or desc
    v = Verdict(UNKNOWN)

    for s in skills:
        if s.get("importance") != "required":
            continue
        ev = s.get("evidence") or ""
        if s.get("status") == "missing":
            v.checks.append(Check(s["name"], False, "not in your profile"))
        elif s.get("status") == "partial" and ev.startswith("Related:"):
            # Knowing a similar skill (Java for Kotlin) isn't meeting the requirement.
            v.checks.append(Check(s["name"], False, "you have a related skill: " + ev[len("Related: "):][:60]))
        else:
            v.checks.append(Check(s["name"], True, "from your coursework" if s.get("status") == "partial"
                                  else "you have it"))

    note = _degree_mismatch(job["title"], sections["required"], profile)
    if note:
        v.checks.append(Check("Degree level", False, note))

    years = [int(m.group(1)) for m in _YEARS.finditer(req_text) if 0 < int(m.group(1)) <= 20]
    if years:
        need, have = min(years), experience_years(profile)
        v.checks.append(Check(f"{need}+ years of experience", have >= need,
                              f"you have about {have:.1f}" if have else "no dated experience in your profile"))

    for m in _GRAD.finditer(req_text):
        ys = sorted({int(y) for y in _YEAR.findall(m.group())})
        if not ys:
            continue
        mine = grad_date(profile)
        if mine is None:
            v.checks.append(Check("Graduation date", None, "add your expected graduation to your profile"))
        else:
            ok = ys[0] <= mine[0] <= ys[-1]
            v.checks.append(Check("Graduation date", ok, f"posting: {'-'.join(map(str, sorted({ys[0], ys[-1]})))}; "
                                                         f"you: {mine[0]}"))
        break

    gm = _GPA.search(req_text)
    if gm:
        need = float(gm.group(1) or gm.group(2))
        if 2.0 <= need <= 4.0:
            mine = _gpa(profile)
            v.checks.append(Check(f"GPA {need:g}+", None if mine is None else mine >= need,
                                  "no GPA in your profile" if mine is None else f"yours: {mine:g}"))

    if _NO_SPONSOR.search(desc):
        v.notes.append("The posting says it won't sponsor visas. Only you know whether that matters.")
    if _CLEARANCE.search(desc):
        v.notes.append("The posting mentions citizenship or a security clearance. Only you can judge that.")

    known = [c for c in v.checks if c.ok is not None]
    if not known:
        v.status = UNKNOWN
        v.notes.insert(0, "Couldn't find specific requirements in the description.")
    elif len(known) == 1 and not v.unmet:
        # One recognized requirement is too little to say you meet the minimums.
        v.status = UNKNOWN
        v.notes.insert(0, "Only one specific requirement found, so read the posting to be sure.")
    else:
        v.status = {0: QUALIFY, 1: CLOSE}.get(len(v.unmet), NOT)
    return v


# ---------------------------------------------------------------- stored verdicts

def _basis(profile: Profile) -> str:
    rel = profile.model_dump(include={"education", "experience", "projects", "skills", "certifications"})
    rel["_rules"] = RULES_VERSION
    return hashlib.sha256(json.dumps(rel, sort_keys=True).encode()).hexdigest()[:12]


def refresh(conn: sqlite3.Connection, profile: Profile | None) -> int:
    """Re-check jobs whose score or your profile changed since the last check."""
    if profile is None:
        return 0
    b = _basis(profile)
    rows = conn.execute(
        """SELECT j.id, j.title, j.description, m.skills_json, m.scored_at FROM jobs j JOIN matches m ON m.job_id = j.id
           LEFT JOIN qualifications q ON q.job_id = j.id
           WHERE q.job_id IS NULL OR q.basis != ? || ':' || m.scored_at""", (b,)).fetchall()
    with conn:
        for r in rows:
            v = evaluate(r, profile, json.loads(r["skills_json"]).get("skills", []))
            conn.execute(
                """INSERT INTO qualifications (job_id, status, unmet, detail_json, basis) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, unmet=excluded.unmet,
                     detail_json=excluded.detail_json, basis=excluded.basis""",
                (r["id"], v.status, len(v.unmet), v.to_json(), f"{b}:{r['scored_at']}"))
    return len(rows)


def load(conn: sqlite3.Connection, job_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM qualifications WHERE job_id = ?", (job_id,)).fetchone()
    if not r:
        return None
    d = json.loads(r["detail_json"])
    return {"status": r["status"], "checks": d["checks"], "notes": d["notes"]}
