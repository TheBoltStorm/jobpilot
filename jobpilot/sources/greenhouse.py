from __future__ import annotations

from typing import Iterator

from jobpilot.sources.base import BoardSource, Company, RawJob
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseSource(BoardSource):
    name = "greenhouse"
    label = "Greenhouse job boards"
    ats = "greenhouse"
    terms_note = "Public Job Board API (documented by Greenhouse for public job listings); robots.txt allows /v1."

    def fetch_company(self, company: Company) -> Iterator[RawJob]:
        data = self.http.get_json(API.format(token=company.token), params={"content": "true"})
        for j in data.get("jobs", []):
            locs = [j["location"]["name"]] if (j.get("location") or {}).get("name") else []
            meta = {m.get("name", "").lower(): m.get("value") for m in j.get("metadata") or [] if isinstance(m, dict)}
            hint = str(meta.get("employment type") or meta.get("employment_type") or "")
            title = j.get("title", "").strip()
            yield RawJob(
                source_id=str(j["id"]),
                title=title,
                company=j.get("company_name") or company.name,
                url=j.get("absolute_url", ""),
                locations=locs,
                description=html_to_text(j.get("content", "")),
                date_posted=to_date(j.get("first_published") or j.get("updated_at")),
                role_type=classify_role_type(title, hint),
                remote=classify_remote(locs),
                field=", ".join(d.get("name", "") for d in j.get("departments") or [] if d.get("name")),
            )
