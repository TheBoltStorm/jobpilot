"""Add a job from a link you found.

Greenhouse, Lever and Ashby postings are read through the same public job-board APIs discovery
uses, so the title, company, location and description come from the posting itself. For any other
site JobPilot doesn't fetch the page; you type the title and company (or the browser button sends
what's on the page you're looking at).
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

from jobpilot.core.db import now_iso
from jobpilot.core.http import PoliteClient
from jobpilot.enrich import _lever_text, target_for
from jobpilot.review.status import log_event
from jobpilot.sources.base import RawJob
from jobpilot.sources.dedupe import canonical_url
from jobpilot.sources.normalize import classify_remote, classify_role_type, detect_ats, html_to_text, to_date

READABLE = ("greenhouse", "lever", "ashby")
SOURCE = "link"


class LinkError(ValueError):
    pass


def _slug_name(url: str) -> str:
    """The board name in an ATS link, as a fallback company name: jobs.lever.co/acme-ai/... -> Acme Ai."""
    first = urlsplit(url).path.strip("/").split("/")[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", first) if w)


def check_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise LinkError("That doesn't look like a web link. Paste the full address, starting with https://")
    return url


def read_posting(http: PoliteClient, url: str, gh_tokens: dict[str, str] | None = None) -> RawJob | None:
    """Read a Greenhouse, Lever or Ashby posting from its public API. None for other sites."""
    ats = detect_ats(url)
    if ats not in READABLE:
        return None
    target = target_for(ats, url, "", gh_tokens or {})
    if target is None:
        return None
    if ats == "greenhouse":
        j = http.get_json(target.api_url)
        locs = [j["location"]["name"]] if (j.get("location") or {}).get("name") else []
        title = (j.get("title") or "").strip()
        return RawJob(source_id=canonical_url(url), title=title, company=j.get("company_name") or _slug_name(url),
                      url=j.get("absolute_url") or url, locations=locs, description=html_to_text(j.get("content", "")),
                      date_posted=to_date(j.get("first_published") or j.get("updated_at")),
                      role_type=classify_role_type(title), remote=classify_remote(locs))
    if ats == "lever":
        j = http.get_json(target.api_url)
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        title = (j.get("text") or "").strip()
        return RawJob(source_id=canonical_url(url), title=title, company=_slug_name(url), url=j.get("hostedUrl") or url,
                      apply_url=j.get("applyUrl", ""), locations=locs, description=_lever_text(j),
                      date_posted=to_date(j.get("createdAt")), role_type=classify_role_type(title, cats.get("commitment", "")),
                      remote=classify_remote(locs, j.get("workplaceType", "")), field=cats.get("team", ""))
    data = http.get_json(target.api_url)
    j = next((x for x in data.get("jobs", []) if x.get("id") == target.job_id), None)
    if j is None:
        raise LinkError("That Ashby posting isn't on the company's board any more. It may be closed.")
    locs = [j["location"]] if j.get("location") else []
    title = (j.get("title") or "").strip()
    return RawJob(source_id=canonical_url(url), title=title, company=_slug_name(url), url=j.get("jobUrl") or url,
                  apply_url=j.get("applyUrl", ""), locations=locs,
                  description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                  date_posted=to_date(j.get("publishedAt")), role_type=classify_role_type(title, j.get("employmentType", "")),
                  remote=classify_remote(locs, "remote" if j.get("isRemote") else ""), field=j.get("department", ""))


def find_job(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    """A stored job whose posting or apply link is this URL."""
    key = canonical_url(url)
    if not key:
        return None
    row = conn.execute("SELECT * FROM jobs WHERE url_key = ?", (key,)).fetchone()
    if row:
        return row
    for r in conn.execute("SELECT * FROM jobs WHERE apply_url != ''"):
        if canonical_url(r["apply_url"]) == key:
            return r
    link = conn.execute("SELECT job_id FROM job_sources WHERE source = ? AND source_id = ?", (SOURCE, key)).fetchone()
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (link[0],)).fetchone() if link else None


def add_link(conn: sqlite3.Connection, http: PoliteClient | None, url: str, *, title: str = "", company: str = "",
             location: str = "", description: str = "", gh_tokens: dict[str, str] | None = None) -> tuple[int, bool]:
    """Store the job behind a link. Returns (job_id, is_new).

    What you type wins over what the posting says, except an empty box never erases anything.
    """
    from jobpilot.discovery import upsert_job

    url = check_url(url)
    existing = find_job(conn, url)
    if existing:
        return existing["id"], False
    raw = None
    if http is not None and detect_ats(url) in READABLE:
        try:
            raw = read_posting(http, url, gh_tokens)
        except LinkError:
            raise
        except Exception as exc:  # network, 404, robots.txt: fall back to what you typed
            if not (title.strip() and company.strip()):
                raise LinkError(f"Couldn't read that posting ({str(exc).splitlines()[0][:100]}). "
                                "Type the job title and company to add it anyway.") from exc
    title, company, location = title.strip(), company.strip(), location.strip()
    if raw is None:
        if not (title and company):
            raise LinkError("JobPilot can't read postings on this site by itself. Type the job title and company.")
        locs = [location] if location else []
        raw = RawJob(source_id=canonical_url(url), title=title, company=company, url=url, locations=locs,
                     description=description.strip()[:60000], role_type=classify_role_type(title),
                     remote=classify_remote(locs))
    else:
        raw.title, raw.company = title or raw.title, company or raw.company
        if location:
            raw.locations = [location]
        if description.strip() and not raw.description:
            raw.description = description.strip()[:60000]
    if not raw.title or not raw.company:
        raise LinkError("The posting didn't say its title or company. Type them in.")
    now = now_iso()
    upsert_job(conn, SOURCE, "you", raw, now)
    job_id = conn.execute("SELECT job_id FROM job_sources WHERE source = ? AND scope = 'you' AND source_id = ?",
                          (SOURCE, raw.source_id)).fetchone()[0]
    # You picked this job, so it goes to your review list whatever its score.
    conn.execute("UPDATE jobs SET status = 'matched' WHERE id = ? AND status IN ('found', 'posting_closed')", (job_id,))
    log_event(conn, job_id, "user", "added from a link")
    return job_id, True
