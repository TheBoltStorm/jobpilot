"""Local (free, offline) matcher: skill evidence from your profile vs. skills a job asks for.

Score (0-100) = 70% skills + 15% role/field fit + 15% location fit, minus a penalty when the
posting asks for a degree level you don't have. Every number traces back to text:
job snippets for what the job asks, profile text for what you have.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from jobpilot.matching.skills import Taxonomy, split_sections
from jobpilot.onboarding.profile import Profile
from jobpilot.sources.fields import job_tags, tags_for

# Bump when scoring rules change so stored scores get redone.
SCORER_VERSION = 5
W_SKILLS, W_ROLE, W_LOCATION = 70, 15, 15
GRAD_DEGREE_PENALTY = 25


@dataclass
class Evidence:
    """Where in the profile a skill shows up."""

    strong: dict[str, str] = field(default_factory=dict)  # used in experience/projects: key -> quote
    listed: dict[str, str] = field(default_factory=dict)  # in skills list / certifications
    coursework: dict[str, str] = field(default_factory=dict)


def profile_evidence(profile: Profile, tax: Taxonomy) -> Evidence:
    ev = Evidence()
    for exp in profile.experience:
        where = f"{exp.title} @ {exp.company}".strip(" @")
        for b in exp.bullets + [exp.title]:
            for key in tax.find(b):
                ev.strong.setdefault(key, f"{where}: {b}")
    for proj in profile.projects:
        for b in proj.bullets + [proj.name]:
            for key in tax.find(b):
                ev.strong.setdefault(key, f"Project {proj.name}: {b}")
    for s in profile.skills + profile.certifications:
        for key in tax.find(s):
            ev.listed.setdefault(key, f"Skills: {s}")
    for ed in profile.education:
        for c in ed.coursework:
            for key in tax.find(c):
                ev.coursework.setdefault(key, f"Coursework: {c}")
        for key in tax.find(ed.field_of_study):
            ev.coursework.setdefault(key, f"Degree: {ed.degree} {ed.field_of_study}")
    # Implied skills (PostgreSQL -> SQL) count at the same strength as what implies them.
    for bucket in (ev.strong, ev.listed, ev.coursework):
        for key in list(bucket):
            for imp in tax.expand_implied({key}) - {key}:
                bucket.setdefault(imp, f"{bucket[key]} (implies {tax.skills[imp].name})")
    return ev


@dataclass
class SkillLine:
    key: str
    name: str
    status: str  # have | partial | missing
    importance: str  # required | preferred | mentioned
    job_snippet: str
    evidence: str = ""


@dataclass
class MatchResult:
    score: int
    confidence: str
    reason: str
    tags: list[str]
    skills: list[SkillLine]

    def skills_json(self) -> dict:
        return {"skills": [asdict(s) for s in self.skills]}

    def by_status(self, status: str) -> list[SkillLine]:
        return [s for s in self.skills if s.status == status]


def _status(key: str, ev: Evidence, tax: Taxonomy) -> tuple[str, str]:
    if key in ev.strong:
        return "have", ev.strong[key]
    if key in ev.listed:
        return "have", ev.listed[key]
    if key in ev.coursework:
        return "partial", ev.coursework[key]
    for sib in sorted(tax.siblings(key)):
        for bucket in (ev.strong, ev.listed):
            if sib in bucket:
                return "partial", f"Related: {tax.skills[sib].name} ({bucket[sib]})"
    return "missing", ""


_GRAD_ONLY = re.compile(r"\b(ph\.?d|doctoral|ms/phd|masters?/ph\.?d|master'?s|m\.s\.)\b", re.IGNORECASE)
_HAS_UNDERGRAD = re.compile(r"\b(bachelor'?s?|b\.?s\.?|b\.?a\.?|undergrad\w*|bs/ms)\b", re.IGNORECASE)
_GRAD_DEGREE = re.compile(r"\b(master|m\.?s\.?|m\.?eng|mba|ph\.?d|doctor)", re.IGNORECASE)


def _degree_mismatch(title: str, required_text: str, profile: Profile) -> str:
    if any(_GRAD_DEGREE.search(e.degree or "") for e in profile.education):
        return ""
    if _GRAD_ONLY.search(title) and not _HAS_UNDERGRAD.search(title):
        return "title asks for a grad degree"
    if re.search(r"\b(currently )?(pursuing|enrolled in) an? (ph\.?d|master'?s)", required_text, re.IGNORECASE):
        return "requires a grad program"
    return ""


def _location_fit(job_location: str, job_remote: str, profile: Profile) -> tuple[float, str]:
    pref = profile.targets.remote_preference
    wanted = [w.lower() for w in profile.targets.locations]
    loc = job_location.lower()
    if job_remote == "remote" or "remote" in loc:
        if pref in ("remote", "any", "hybrid") or "remote" in wanted:
            return 1.0, "remote"
        return 0.5, "remote (you prefer on-site)"
    if any(w in ("anywhere", "any") for w in wanted):
        return 1.0, ""
    if wanted:
        hit = next((w for w in wanted if w != "remote" and w in loc), None)
        if hit:
            return 1.0, f"in {hit.title()}"
        if pref == "remote":
            return 0.0, "not remote"
        return 0.3, "outside your locations" if loc else "location unknown"
    return 0.7, ""


def score_job(job, profile: Profile, tax: Taxonomy, ev: Evidence | None = None) -> MatchResult:
    """job: a sqlite Row / mapping with title, field, description, location, remote."""
    ev = ev or profile_evidence(profile, tax)
    desc = job["description"] or ""
    sections = split_sections(desc) if desc else {"required": "", "preferred": "", "other": ""}

    found_req = tax.find(sections["required"])
    found_pref = {k: v for k, v in tax.find(sections["preferred"]).items() if k not in found_req}
    found_other = {k: v for k, v in tax.find(sections["other"] + "\n" + job["title"]).items()
                   if k not in found_req and k not in found_pref}
    if not found_req and not found_pref:
        # No headings we recognize: treat everything mentioned as required.
        found_req, found_other = found_other, {}

    lines: list[SkillLine] = []
    for importance, found in (("required", found_req), ("preferred", found_pref), ("mentioned", found_other)):
        for key, snippet in found.items():
            status, evidence = _status(key, ev, tax)
            lines.append(SkillLine(key, tax.skills[key].name, status, importance, snippet, evidence))

    def coverage(imp: str) -> float | None:
        group = [s for s in lines if s.importance == imp]
        if not group:
            return None
        return sum(1.0 if s.status == "have" else 0.5 if s.status == "partial" else 0.0 for s in group) / len(group)

    req, pref = coverage("required"), coverage("preferred")
    n = len([s for s in lines if s.importance != "mentioned"])
    if req is None and pref is None:
        skill_part, confidence = 0.5, "low"
    else:
        skill_part = (0.75 * req + 0.25 * pref) if (req is not None and pref is not None) else (req if req is not None else pref)
        confidence = "high" if n >= 5 and len(desc) > 800 else "medium" if n >= 3 else "low"
        # One or two skills found say little: pull the skills part toward the middle until there are 4+.
        skill_part = 0.5 + (skill_part - 0.5) * min(1.0, n / 4)
    if not desc:
        # Title-only: a skill word in the title says little. Keep the estimate near the middle (0.4-0.6).
        skill_part, confidence = 0.4 + 0.2 * skill_part, "low"

    wanted_tags = tags_for(profile.targets.fields + profile.targets.roles)
    jtags = job_tags(job["title"], job["field"] or "")
    role_part = 1.0 if not wanted_tags or (jtags & wanted_tags) else 0.3
    loc_part, loc_note = _location_fit(job["location"] or "", job["remote"] or "", profile)

    score = W_SKILLS * skill_part + W_ROLE * role_part + W_LOCATION * loc_part
    degree_note = _degree_mismatch(job["title"], sections["required"], profile)
    if degree_note:
        score -= GRAD_DEGREE_PENALTY
    score = int(round(max(0, min(100, score))))

    # Reason: one short sentence, facts only.
    req_lines = [s for s in lines if s.importance == "required"]
    parts = []
    if not desc:
        parts.append("No description yet, so this is a rough estimate from the title")
    elif req_lines:
        have = sum(s.status == "have" for s in req_lines)
        missing = [s.name for s in req_lines if s.status == "missing"]
        parts.append(f"You have {have}/{len(req_lines)} required skills"
                     + (f" (missing {', '.join(missing[:3])}{'...' if len(missing) > 3 else ''})" if missing else ""))
    elif any(s.importance == "preferred" for s in lines):
        pref_lines = [s for s in lines if s.importance == "preferred"]
        have = sum(s.status == "have" for s in pref_lines)
        parts.append(f"No required skills listed; you have {have}/{len(pref_lines)} preferred ones")
    else:
        parts.append("Couldn't find specific skills in the description")
    if role_part < 1:
        parts.append("role is outside your target fields")
    if loc_note:
        parts.append(loc_note)
    if degree_note:
        parts.append(degree_note)
    reason = "; ".join(parts) + "."

    return MatchResult(score, confidence, reason[0].upper() + reason[1:], sorted(jtags), lines)
