"""The interface every job source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Iterator

from jobpilot.core.http import PoliteClient
from jobpilot.onboarding.profile import Profile
from jobpilot.settings import Settings


@dataclass
class RawJob:
    """One posting as a source reports it. Leave fields blank rather than guessing."""

    source_id: str  # stable id within the source + scope
    title: str
    company: str
    url: str
    locations: list[str] = field(default_factory=list)
    description: str = ""  # plain text
    apply_url: str = ""
    date_posted: str = ""  # YYYY-MM-DD
    role_type: str = ""  # internship | co-op | part-time | full-time | ''
    remote: str = ""  # remote | hybrid | onsite | ''
    field: str = ""  # source's own category, e.g. "Software", "AI/ML/Data"
    active: bool = True  # False = source says it's closed


@dataclass
class ScopeResult:
    """Jobs from one complete listing (one company board, one repo, one feed).

    If ``complete`` is True, any job previously seen in this scope but missing now
    is marked "posting closed". Set it False if you only fetched part of a listing.
    """

    scope: str
    jobs: list[RawJob]
    complete: bool = True
    error: str = ""


@dataclass
class Company:
    name: str
    ats: str
    token: str
    fields: list[str] = field(default_factory=list)


class JobSource(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    terms_note: ClassVar[str]  # why this source is OK to use (shown in `jobpilot sources`)

    def __init__(self, http: PoliteClient, settings: Settings, profile: Profile | None, companies: list[Company]):
        self.http = http
        self.settings = settings
        self.profile = profile
        self.companies = companies

    def enabled(self) -> tuple[bool, str]:
        """(is_enabled, reason). Override to add checks such as required API keys."""
        toggle = getattr(self.settings.sources, self.name, None)
        if toggle is not None and not toggle.enabled:
            return False, "disabled in settings.yaml"
        return True, ""

    @abstractmethod
    def fetch(self) -> Iterator[ScopeResult]:
        """Yield one ScopeResult per listing. Catch per-scope errors and report them in ScopeResult.error."""


class BoardSource(JobSource):
    """Base for ATS job-board APIs that are queried once per company."""

    ats: ClassVar[str]

    def my_companies(self) -> list[Company]:
        return [c for c in self.companies if c.ats == self.ats]

    def enabled(self) -> tuple[bool, str]:
        ok, why = super().enabled()
        if ok and not self.my_companies():
            return False, f"no {self.ats} companies in companies.yaml"
        return ok, why

    def fetch(self) -> Iterator[ScopeResult]:
        for company in self.my_companies():
            scope = company.token
            try:
                yield ScopeResult(scope, list(self.fetch_company(company)))
            except Exception as exc:  # one bad board shouldn't stop the run
                yield ScopeResult(scope, [], complete=False, error=f"{type(exc).__name__}: {exc}"[:300])

    @abstractmethod
    def fetch_company(self, company: Company) -> Iterator[RawJob]: ...
