from __future__ import annotations

from typing import Iterator

from jobpilot.sources.base import BoardSource, Company, RawJob
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


class AshbySource(BoardSource):
    name = "ashby"
    label = "Ashby job boards"
    ats = "ashby"
    terms_note = "Public Job Posting API (documented by Ashby for embedding public job boards)."

    def fetch_company(self, company: Company) -> Iterator[RawJob]:
        data = self.http.get_json(API.format(token=company.token))
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j["location"]] if j.get("location") else []
            locs += [s.get("location") for s in j.get("secondaryLocations") or [] if s.get("location")]
            title = (j.get("title") or "").strip()
            remote_hint = j.get("workplaceType") or ("remote" if j.get("isRemote") else "")
            yield RawJob(
                source_id=j["id"],
                title=title,
                company=company.name,
                url=j.get("jobUrl", ""),
                apply_url=j.get("applyUrl", ""),
                locations=locs,
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                date_posted=to_date(j.get("publishedAt")),
                role_type=classify_role_type(title, j.get("employmentType", "")),
                remote=classify_remote(locs, remote_hint),
                field=j.get("department") or j.get("team") or "",
            )
