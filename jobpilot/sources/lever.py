from __future__ import annotations

from typing import Iterator

from jobpilot.sources.base import BoardSource, Company, RawJob
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date

API = "https://api.lever.co/v0/postings/{token}"


class LeverSource(BoardSource):
    name = "lever"
    label = "Lever job boards"
    ats = "lever"
    terms_note = "Public Postings API (documented by Lever); robots.txt allows all with Crawl-delay 1."

    def fetch_company(self, company: Company) -> Iterator[RawJob]:
        data = self.http.get_json(API.format(token=company.token), params={"mode": "json"})
        for j in data:
            cats = j.get("categories") or {}
            locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
            title = (j.get("text") or "").strip()
            parts = [j.get("openingPlain"), j.get("descriptionBodyPlain") or j.get("descriptionPlain")]
            for lst in j.get("lists") or []:
                parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}")
            parts.append(j.get("additionalPlain"))
            yield RawJob(
                source_id=j["id"],
                title=title,
                company=company.name,
                url=j.get("hostedUrl", ""),
                apply_url=j.get("applyUrl", ""),
                locations=locs,
                description="\n\n".join(p.strip() for p in parts if p and p.strip()),
                date_posted=to_date(j.get("createdAt")),
                role_type=classify_role_type(title, cats.get("commitment", "")),
                remote=classify_remote(locs, j.get("workplaceType", "")),
                field=cats.get("team", "") or cats.get("department", ""),
            )
