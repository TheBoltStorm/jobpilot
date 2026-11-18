"""Import jobs from a CSV you exported yourself (Handshake, a spreadsheet, a career fair list...).

Column names are matched loosely; only title, company and url are required.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from jobpilot.sources.base import RawJob, ScopeResult
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date

ALIASES = {
    "title": ["title", "job title", "position", "role", "job name", "posting title"],
    "company": ["company", "employer", "organization", "company name", "employer name"],
    "url": ["url", "link", "job url", "posting url", "apply url", "application link", "job link"],
    "location": ["location", "locations", "city", "job location"],
    "description": ["description", "job description", "details", "summary"],
    "date_posted": ["date posted", "posted", "posted date", "created", "date"],
    "type": ["type", "job type", "employment type", "role type"],
}


def parse_csv(path: str | Path) -> ScopeResult:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols: dict[str, str] = {}
        norm = {h.strip().lower().replace("_", " "): h for h in reader.fieldnames or []}
        for key, aliases in ALIASES.items():
            for a in aliases:
                if a in norm:
                    cols[key] = norm[a]
                    break
        missing = [k for k in ("title", "company", "url") if k not in cols]
        if missing:
            raise ValueError(f"CSV is missing column(s): {', '.join(missing)}. Found: {', '.join(reader.fieldnames or [])}")
        jobs = []
        for row in reader:
            get = lambda k: (row.get(cols[k]) or "").strip() if k in cols else ""  # noqa: E731
            title, url = get("title"), get("url")
            if not (title and url):
                continue
            locs = [x.strip() for x in get("location").replace(";", "|").split("|") if x.strip()]
            jobs.append(RawJob(
                source_id=hashlib.sha1(url.encode()).hexdigest()[:16], title=title, company=get("company"), url=url,
                locations=locs, description=html_to_text(get("description")), date_posted=to_date(get("date_posted")),
                role_type=classify_role_type(title, get("type")), remote=classify_remote(locs),
            ))
    return ScopeResult(path.name, jobs, complete=False)
