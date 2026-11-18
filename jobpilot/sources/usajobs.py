"""USAJOBS Search API (US federal jobs). Needs a free key from developer.usajobs.gov."""

from __future__ import annotations

import os
from typing import Iterator

from jobpilot.sources.base import JobSource, RawJob, ScopeResult
from jobpilot.sources.normalize import classify_remote, classify_role_type, to_date

API = "https://data.usajobs.gov/api/search"


class USAJobsSource(JobSource):
    name = "usajobs"
    label = "USAJOBS (US federal)"
    terms_note = "Official public API from the US Office of Personnel Management; requires a free API key."

    def enabled(self) -> tuple[bool, str]:
        ok, why = super().enabled()
        if not ok:
            return ok, why
        if not (os.environ.get("USAJOBS_API_KEY") and os.environ.get("USAJOBS_EMAIL")):
            return False, "set USAJOBS_API_KEY and USAJOBS_EMAIL in .env"
        if not self._keywords():
            return False, "no keywords (set sources.usajobs.keywords or profile target roles)"
        return True, ""

    def _keywords(self) -> list[str]:
        kw = self.settings.sources.usajobs.keywords
        if not kw and self.profile:
            kw = self.profile.targets.roles or self.profile.targets.fields
        return kw

    def fetch(self) -> Iterator[ScopeResult]:
        headers = {
            "Authorization-Key": os.environ["USAJOBS_API_KEY"],
            "User-Agent": os.environ["USAJOBS_EMAIL"],  # USAJOBS requires your email here
            "Host": "data.usajobs.gov",
        }
        for kw in self._keywords():
            try:
                params = {"Keyword": kw, "ResultsPerPage": self.settings.sources.usajobs.results_per_keyword}
                data = self.http.get_json(API, params=params, headers=headers)
                items = (data.get("SearchResult") or {}).get("SearchResultItems") or []
                # A keyword search is not a full listing, so don't close jobs that drop out of it.
                yield ScopeResult(f"keyword:{kw}", [self._job(i["MatchedObjectDescriptor"]) for i in items], complete=False)
            except Exception as exc:
                yield ScopeResult(f"keyword:{kw}", [], complete=False, error=f"{type(exc).__name__}: {exc}"[:300])

    @staticmethod
    def _job(d: dict) -> RawJob:
        title = d.get("PositionTitle", "").strip()
        locs = [loc.get("LocationName", "") for loc in d.get("PositionLocation") or [] if loc.get("LocationName")]
        if not locs and d.get("PositionLocationDisplay"):
            locs = [d["PositionLocationDisplay"]]
        details = ((d.get("UserArea") or {}).get("Details") or {})
        schedule = ", ".join(s.get("Name", "") for s in d.get("PositionSchedule") or [])
        desc = "\n\n".join(x for x in (details.get("JobSummary"), d.get("QualificationSummary")) if x)
        apply = (d.get("ApplyURI") or [""])[0]
        return RawJob(
            source_id=str(d.get("PositionID") or d.get("PositionURI")),
            title=title,
            company=" - ".join(x for x in (d.get("OrganizationName"), d.get("DepartmentName")) if x),
            url=d.get("PositionURI", ""),
            apply_url=apply,
            locations=locs,
            description=desc,
            date_posted=to_date(d.get("PublicationStartDate")),
            role_type=classify_role_type(title, schedule),
            remote=classify_remote(locs, "remote" if details.get("RemoteIndicator") else ""),
            field=", ".join(c.get("Name", "") for c in d.get("JobCategory") or []),
        )
