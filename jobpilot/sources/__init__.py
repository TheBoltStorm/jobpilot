"""Job discovery sources. Each module defines one JobSource subclass; see docs/ADDING_A_SOURCE.md."""

from jobpilot.sources.ashby import AshbySource
from jobpilot.sources.base import JobSource, RawJob, ScopeResult
from jobpilot.sources.github_lists import GitHubListsSource
from jobpilot.sources.greenhouse import GreenhouseSource
from jobpilot.sources.lever import LeverSource
from jobpilot.sources.rss import RSSSource
from jobpilot.sources.usajobs import USAJobsSource

# Order matters a little: richer sources first so merged records keep their descriptions.
ALL_SOURCES: list[type[JobSource]] = [
    GreenhouseSource, LeverSource, AshbySource, USAJobsSource, RSSSource, GitHubListsSource,
]
SOURCES_BY_NAME = {s.name: s for s in ALL_SOURCES}

__all__ = ["ALL_SOURCES", "SOURCES_BY_NAME", "JobSource", "RawJob", "ScopeResult"]
