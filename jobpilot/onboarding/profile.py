"""profile.yaml schema. This is the user's confirmed data, and the only thing autofill may use.

Things deliberately NOT in this schema: work authorization, sponsorship, EEO /
demographic, veteran, disability, salary, background-check and legal answers.
Those are always answered by the human at review time (see core/protected.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from jobpilot.core.protected import protected_category

RoleType = Literal["internship", "co-op", "part-time", "full-time"]
RemotePref = Literal["remote", "hybrid", "onsite", "any"]

PROFILE_VERSION = 1


class Contact(BaseModel):
    first_name: str = ""
    last_name: str = ""
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    country: str = ""


class Links(BaseModel):
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    other: list[str] = Field(default_factory=list)


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    field_of_study: str = ""
    minor: str = ""
    start: str = ""
    end: str = ""
    gpa: str = ""
    coursework: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str = ""
    organization: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = Field(default_factory=list)


class Targets(BaseModel):
    fields: list[str] = Field(default_factory=list)  # e.g. "software engineering", "data science"
    roles: list[str] = Field(default_factory=list)  # e.g. "backend engineer intern"
    role_types: list[RoleType] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_preference: RemotePref = "any"
    earliest_start: str = ""


class Profile(BaseModel):
    version: int = PROFILE_VERSION
    contact: Contact = Field(default_factory=Contact)
    links: Links = Field(default_factory=Links)
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    targets: Targets = Field(default_factory=Targets)
    # Reusable answers to common, non-sensitive questions. Key = short question, value = your answer.
    short_answers: dict[str, str] = Field(default_factory=dict)
    default_resume: str = ""

    @field_validator("short_answers")
    @classmethod
    def _no_protected_answers(cls, v: dict[str, str]) -> dict[str, str]:
        bad = {k: cat for k in v if (cat := protected_category(k))}
        if bad:
            listing = ", ".join(f"'{k}' ({c})" for k, c in bad.items())
            raise ValueError(
                f"short_answers can't hold protected questions: {listing}. "
                "JobPilot always leaves these for you to answer on each application."
            )
        return v

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.contact.first_name, self.contact.last_name) if p)


def load_profile(path: Path) -> Profile | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)


HEADER = """\
# JobPilot profile. This file is yours: edit it any time (it's gitignored).
# Autofill only uses what's written here. Anything missing becomes NEEDS_HUMAN.
# Work authorization, sponsorship, EEO/demographics, veteran, disability, salary,
# background-check and legal questions are never stored here; you answer them per application.
"""


def save_profile(profile: Profile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
