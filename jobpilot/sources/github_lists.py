"""Community internship / new-grad lists on GitHub (SimplifyJobs and similar).

These repos publish a machine-readable listings.json alongside their README, so we
read that instead of scraping README tables. If a repo ever drops the JSON file,
we fall back to parsing the README's markdown/HTML table.
"""

from __future__ import annotations

import re
from typing import Iterator

from jobpilot.sources.base import JobSource, RawJob, ScopeResult
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date

RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"


class GitHubListsSource(JobSource):
    name = "github_lists"
    label = "GitHub internship / new-grad lists"
    terms_note = (
        "Public community-maintained repos read via raw.githubusercontent.com (no robots.txt restrictions). "
        "Data is used for personal search only and links back to the original posting."
    )

    def fetch(self) -> Iterator[ScopeResult]:
        for lst in self.settings.sources.github_lists.lists:
            try:
                yield ScopeResult(lst.repo, list(self._fetch_list(lst)))
            except Exception as exc:
                yield ScopeResult(lst.repo, [], complete=False, error=f"{type(exc).__name__}: {exc}"[:300])

    def _fetch_list(self, lst) -> Iterator[RawJob]:
        default_type = "internship" if lst.kind == "internship" else "full-time"
        try:
            data = self.http.get_json(RAW.format(repo=lst.repo, branch=lst.branch, path=lst.path))
        except Exception:
            readme = self.http.get(RAW.format(repo=lst.repo, branch=lst.branch, path="README.md")).text
            yield from parse_readme_table(readme, default_type)
            return
        for item in data:
            yield from self._from_listing(item, default_type)

    @staticmethod
    def _from_listing(item: dict, default_type: str) -> Iterator[RawJob]:
        if not item.get("is_visible", True):
            return
        title = (item.get("title") or "").strip()
        locs = [loc for loc in item.get("locations") or [] if loc]
        hint = "co-op" if any("co-op" in str(t).lower() for t in item.get("terms") or []) else default_type
        yield RawJob(
            source_id=str(item.get("id") or item.get("url")),
            title=title,
            company=(item.get("company_name") or "").strip(),
            url=item.get("url", ""),
            locations=locs,
            date_posted=to_date(item.get("date_posted")),
            role_type=classify_role_type(title, hint) or default_type,
            remote=classify_remote(locs),
            field=item.get("category") or "",
            active=bool(item.get("active", True)),
        )


# ---------------------------------------------------------------- README fallback

_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_HREF = re.compile(r'href="([^"]+)"')


def parse_readme_table(readme: str, default_type: str) -> Iterator[RawJob]:
    """Parse rows of `| Company | Role | Location | Application | Age |` tables (markdown or HTML)."""
    rows: list[list[str]] = []
    if "<tr" in readme:
        for tr in re.findall(r"<tr>(.*?)</tr>", readme, re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if cells:
                rows.append(cells)
    else:
        for line in readme.splitlines():
            if line.startswith("|") and not re.match(r"^\|\s*:?-", line):
                rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    last_company = ""
    for cells in rows:
        if len(cells) < 4 or cells[0].strip().lower() in {"company", "**company**"}:
            continue
        company = html_to_text(_MD_LINK.sub(lambda m: m.group(0).split("]")[0][1:], cells[0])).strip("* ")
        if company in {"↳", ""}:
            company = last_company
        last_company = company
        app = cells[3]
        if "🔒" in app:
            continue  # closed
        links = _HREF.findall(app) or _MD_LINK.findall(app)
        if not links:
            continue
        url = links[0]
        title = html_to_text(cells[1]).strip()
        locs = [s.strip() for s in re.split(r"<br\s*/?>|</br>|;", cells[2]) if html_to_text(s).strip()]
        locs = [html_to_text(s) for s in locs]
        yield RawJob(
            source_id=url, title=title, company=company, url=url, locations=locs,
            role_type=classify_role_type(title, default_type) or default_type, remote=classify_remote(locs),
        )
