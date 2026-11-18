"""User settings (data/settings.yaml). Created with defaults on first use; edit freely."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_GITHUB_LISTS = [
    # repo, branch, kind. Season repos are renamed each year; update these when a new season opens.
    {"repo": "SimplifyJobs/Summer2027-Internships", "branch": "dev", "kind": "internship"},
    {"repo": "SimplifyJobs/New-Grad-Positions", "branch": "dev", "kind": "new_grad"},
    {"repo": "vanshb03/Summer2027-Internships", "branch": "dev", "kind": "internship"},
    {"repo": "vanshb03/New-Grad-2027", "branch": "dev", "kind": "new_grad"},
]


class GitHubList(BaseModel):
    repo: str
    branch: str = "dev"
    path: str = ".github/scripts/listings.json"
    kind: str = "internship"  # internship | new_grad


class SourceToggle(BaseModel):
    enabled: bool = True


class GitHubListsSettings(SourceToggle):
    lists: list[GitHubList] = Field(default_factory=lambda: [GitHubList(**d) for d in DEFAULT_GITHUB_LISTS])


class USAJobsSettings(SourceToggle):
    enabled: bool = False  # needs USAJOBS_API_KEY + USAJOBS_EMAIL in .env
    keywords: list[str] = Field(default_factory=list)  # empty = use profile target roles/fields
    results_per_keyword: int = 250


class Feed(BaseModel):
    url: str
    company: str = ""


class RSSSettings(SourceToggle):
    feeds: list[Feed] = Field(default_factory=list)


class Sources(BaseModel):
    github_lists: GitHubListsSettings = Field(default_factory=GitHubListsSettings)
    greenhouse: SourceToggle = Field(default_factory=SourceToggle)
    lever: SourceToggle = Field(default_factory=SourceToggle)
    ashby: SourceToggle = Field(default_factory=SourceToggle)
    usajobs: USAJobsSettings = Field(default_factory=USAJobsSettings)
    rss: RSSSettings = Field(default_factory=RSSSettings)


class Settings(BaseModel):
    user_agent: str = "JobPilot/0.1 (personal job search assistant; respects robots.txt)"
    min_seconds_between_requests: float = 1.0  # per host; robots.txt Crawl-delay wins if larger
    request_timeout_seconds: float = 30.0
    max_age_days: int = 60  # skip postings older than this (when the source gives a date)
    filter_by_profile: bool = True  # only keep jobs matching your role types / fields
    sources: Sources = Field(default_factory=Sources)


HEADER = "# JobPilot settings. Safe to edit; delete the file to reset to defaults.\n\n"


def load_settings(path: Path) -> Settings:
    if path.is_file():
        return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    s = Settings()
    save_settings(s, path)
    return s


def save_settings(settings: Settings, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False, width=100)
    path.write_text(HEADER + body, encoding="utf-8")
