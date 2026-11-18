"""Setup interview: confirms what the parser found and asks for what it couldn't.

Rules: show the parsed value as the default, never invent one. Pressing Enter
keeps the shown value; typing "-" clears it. Protected questions (work
authorization, EEO, salary, ...) are never asked or stored.
"""

from __future__ import annotations

import re
from typing import Callable, Protocol, get_args

from pydantic import BaseModel, ValidationError

from jobpilot.core.protected import protected_category
from jobpilot.onboarding.profile import (
    Contact, Education, Experience, Links, Profile, Project, RemotePref, RoleType, Targets,
)
from jobpilot.onboarding.resume_parser import ParsedResume

CLEAR = "-"


class IO(Protocol):
    def ask(self, prompt: str, default: str = "") -> str: ...
    def say(self, msg: str = "") -> None: ...


class ConsoleIO:
    def __init__(self, input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print):
        self._in, self._out = input_fn, print_fn

    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            answer = self._in(f"  {prompt}{suffix}: ").strip()
        except EOFError:
            answer = ""
        return default if answer == "" else ("" if answer == CLEAR else answer)

    def say(self, msg: str = "") -> None:
        self._out(msg)


# ---------------------------------------------------------------- helpers

def _yes(io: IO, prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        ans = io.ask(f"{prompt} ({d})", "").strip().lower()
        if ans == "":
            return default
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no"}:
            return False
        io.say("    Please answer y or n.")


def _list(io: IO, prompt: str, current: list[str]) -> list[str]:
    shown = ", ".join(current)
    raw = io.ask(f"{prompt} (comma-separated)", shown)
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []


def _choices(io: IO, prompt: str, options: tuple[str, ...], current: list[str]) -> list[str]:
    while True:
        picked = _list(io, f"{prompt} - options: {', '.join(options)}", current)
        bad = [p for p in picked if p.lower() not in options]
        if not bad:
            return [p.lower() for p in picked]
        io.say(f"    Not an option: {', '.join(bad)}")


def _confirm_fields(io: IO, model: BaseModel, labels: dict[str, str]) -> None:
    for name, label in labels.items():
        setattr(model, name, io.ask(label, getattr(model, name) or ""))


def _split_location(loc: str) -> tuple[str, str]:
    if "," in loc:
        city, _, state = loc.partition(",")
        return city.strip(), state.strip()
    return loc.strip(), ""


# ---------------------------------------------------------------- seed profile from resume

def profile_from_resume(parsed: ParsedResume) -> Profile:
    """Map parser output onto the profile schema, copying only what was found."""
    c = parsed.contact
    names = c.name.split()
    city, state = _split_location(c.location)
    p = Profile(
        contact=Contact(
            first_name=names[0] if names else "",
            last_name=names[-1] if len(names) > 1 else "",
            email=c.email, phone=c.phone, city=city, state=state,
        ),
        links=Links(linkedin=c.linkedin, github=c.github, portfolio=c.portfolio, other=list(c.other_links)),
        skills=list(parsed.skills),
        certifications=list(parsed.certifications),
    )
    for e in parsed.education:
        p.education.append(Education(
            school=e.school, degree=e.degree, field_of_study=e.field_of_study,
            start=e.start, end=e.end, gpa=e.gpa, coursework=list(e.coursework),
        ))
    for e in parsed.experience:
        p.experience.append(Experience(
            title=e.title, company=e.organization, location=e.location,
            start=e.start, end=e.end, bullets=list(e.bullets),
        ))
    for e in parsed.projects:
        p.projects.append(Project(
            name=e.title, organization=e.organization, start=e.start, end=e.end, bullets=list(e.bullets),
        ))
    return p


def merge_profiles(base: Profile, existing: Profile | None) -> Profile:
    """On re-runs, values the user already confirmed win over fresh parser output."""
    if existing is None:
        return base
    merged = existing.model_copy(deep=True)
    for section in ("contact", "links"):
        cur, new = getattr(merged, section), getattr(base, section)
        for k, v in new.model_dump().items():
            if not getattr(cur, k):
                setattr(cur, k, v)
    for section in ("education", "experience", "projects"):
        if not getattr(merged, section):
            setattr(merged, section, getattr(base, section))
    merged.skills = list(dict.fromkeys(merged.skills + base.skills))
    merged.certifications = list(dict.fromkeys(merged.certifications + base.certifications))
    return merged


# ---------------------------------------------------------------- interview

def run_interview(io: IO, parsed: ParsedResume | None, existing: Profile | None = None) -> Profile:
    seed = profile_from_resume(parsed) if parsed else Profile()
    p = merge_profiles(seed, existing)

    io.say("\nJobPilot setup. Press Enter to keep the value in [brackets], type '-' to clear it.")
    if parsed and parsed.warnings:
        io.say("The resume parser flagged:")
        for w in parsed.warnings:
            io.say(f"  ! {w}")

    io.say("\n== Contact ==")
    _confirm_fields(io, p.contact, {
        "first_name": "First name", "last_name": "Last name",
        "preferred_name": "Preferred name (optional)", "email": "Email", "phone": "Phone",
        "city": "City", "state": "State/province", "country": "Country",
    })

    io.say("\n== Links ==")
    _confirm_fields(io, p.links, {"linkedin": "LinkedIn URL", "github": "GitHub URL", "portfolio": "Portfolio/website"})

    _review_list(io, "Education", p.education, Education, lambda e: f"{e.degree} {e.field_of_study} @ {e.school} ({e.end})", {
        "school": "School", "degree": "Degree", "field_of_study": "Field of study", "minor": "Minor (optional)",
        "start": "Start", "end": "End / expected graduation", "gpa": "GPA (optional)",
    })
    _review_list(io, "Experience", p.experience, Experience, lambda e: f"{e.title} @ {e.company} ({e.start} - {e.end})", {
        "title": "Job title", "company": "Company", "location": "Location", "start": "Start", "end": "End",
    })
    _review_list(io, "Projects", p.projects, Project, lambda e: f"{e.name} ({e.start} - {e.end})".replace(" ( - )", ""), {
        "name": "Project name",
    })

    io.say("\n== Skills ==")
    io.say("  Only list skills you can back up from your resume. JobPilot never adds skills for you.")
    p.skills = _list(io, "Skills", p.skills)

    io.say("\n== What you're looking for ==")
    t: Targets = p.targets
    t.fields = _list(io, "Target fields (e.g. software engineering, data science)", t.fields)
    t.roles = _list(io, "Target role titles (e.g. backend intern)", t.roles)
    t.role_types = _choices(io, "Role types", get_args(RoleType), list(t.role_types))  # type: ignore[arg-type]
    t.locations = _list(io, "Preferred locations (cities/states, or 'anywhere')", t.locations)
    options = get_args(RemotePref)
    while True:
        pref = io.ask(f"Remote preference ({'/'.join(options)})", t.remote_preference).lower()
        if pref in options:
            t.remote_preference = pref  # type: ignore[assignment]
            break
        io.say("    Pick one of the listed options.")
    t.earliest_start = io.ask("Earliest start date (e.g. May 2026)", t.earliest_start)

    _short_answers(io, p)

    try:
        return Profile.model_validate(p.model_dump())
    except ValidationError as exc:  # pragma: no cover - guarded above, but never save bad data
        raise SystemExit(f"Profile failed validation:\n{exc}") from exc


def _review_list(io: IO, title: str, items: list, model_cls: type[BaseModel], summary: Callable, labels: dict[str, str]) -> None:
    io.say(f"\n== {title} ==")
    kept = []
    for item in items:
        io.say(f"  Found: {summary(item)}")
        if not _yes(io, "Keep this entry?"):
            continue
        _confirm_fields(io, item, labels)
        kept.append(item)
    while _yes(io, f"Add another {title.lower()} entry?", default=False):
        item = model_cls()
        _confirm_fields(io, item, labels)
        if any(getattr(item, k) for k in labels):
            kept.append(item)
    items[:] = kept


def _short_answers(io: IO, p: Profile) -> None:
    io.say("\n== Reusable short answers ==")
    io.say("  Answers to common questions you're happy to reuse (e.g. 'Willing to relocate?').")
    io.say("  Not allowed here: work authorization, sponsorship, demographics, veteran/disability,")
    io.say("  salary, background checks or legal consent. You answer those yourself on each application.")
    for q, a in list(p.short_answers.items()):
        new = io.ask(q, a)
        if new:
            p.short_answers[q] = new
        else:
            del p.short_answers[q]
    while True:
        q = io.ask("New question (blank to finish)", "")
        if not q:
            break
        if cat := protected_category(q):
            io.say(f"    That's a protected question ({cat.replace('_', ' ')}). JobPilot will always leave it for you.")
            continue
        a = io.ask("Your answer", "")
        if a:
            p.short_answers[re.sub(r"\s+", " ", q).strip()] = a
