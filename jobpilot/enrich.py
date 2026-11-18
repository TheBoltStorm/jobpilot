"""Fetch missing job descriptions through the same public ATS APIs discovery uses.

The GitHub lists don't include descriptions. When a listed job is hosted on Greenhouse,
Lever or Ashby we can get the full posting from that system's public job-board API.
Other systems (Workday, iCIMS, custom sites) are left alone: no scraping.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from jobpilot.core.http import PoliteClient
from jobpilot.sources.base import Company
from jobpilot.sources.dedupe import company_key
from jobpilot.sources.normalize import html_to_text

SUPPORTED = ("greenhouse", "lever", "ashby")


@dataclass
class Target:
    ats: str
    api_url: str
    job_id: str = ""  # for Ashby (board-level fetch, then pick the job)


def _greenhouse_target(url: str, company: str, gh_tokens: dict[str, str]) -> Target | None:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    api_host = "boards-api.eu.greenhouse.io" if ".eu." in host else "boards-api.greenhouse.io"
    qs = parse_qs(parts.query)
    if "greenhouse.io" in host:
        m = re.match(r"^/([^/]+)/jobs/(\d+)", parts.path)
        if m:
            return Target("greenhouse", f"https://{api_host}/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
        if "for" in qs and "token" in qs:  # /embed/job_app?for=<board>&token=<id>
            return Target("greenhouse", f"https://{api_host}/v1/boards/{qs['for'][0]}/jobs/{qs['token'][0]}")
    if "gh_jid" in qs:
        # Custom career domain: we need the board token. Use companies.yaml if we know it, else
        # the most common convention (lowercased company name without spaces/suffixes).
        token = gh_tokens.get(company_key(company)) or company_key(company)
        return Target("greenhouse", f"https://{api_host}/v1/boards/{token}/jobs/{qs['gh_jid'][0]}")
    return None


def _lever_target(url: str) -> Target | None:
    parts = urlsplit(url)
    m = re.match(r"^/([^/]+)/([0-9a-f-]{36})", parts.path)
    if not m:
        return None
    api_host = "api.eu.lever.co" if ".eu." in parts.netloc else "api.lever.co"
    return Target("lever", f"https://{api_host}/v0/postings/{m.group(1)}/{m.group(2)}?mode=json")


def _ashby_target(url: str) -> Target | None:
    m = re.match(r"^/([^/]+)/([0-9a-f-]{36})", urlsplit(url).path)
    if not m:
        return None
    return Target("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}", job_id=m.group(2))


def target_for(ats: str, url: str, company: str, gh_tokens: dict[str, str]) -> Target | None:
    if ats == "greenhouse":
        return _greenhouse_target(url, company, gh_tokens)
    if ats == "lever":
        return _lever_target(url)
    if ats == "ashby":
        return _ashby_target(url)
    return None


def _lever_text(j: dict) -> str:
    parts = [j.get("openingPlain"), j.get("descriptionBodyPlain") or j.get("descriptionPlain")]
    for lst in j.get("lists") or []:
        parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}")
    parts.append(j.get("additionalPlain"))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


@dataclass
class EnrichSummary:
    attempted: int = 0
    filled: int = 0
    failed: int = 0
    skipped_unsupported: int = 0
    errors: list[str] = field(default_factory=list)


def enrich_descriptions(
    conn: sqlite3.Connection,
    http: PoliteClient,
    companies: list[Company],
    limit: int = 300,
    retry_failed: bool = False,
    log: Callable[[str], None] = print,
) -> EnrichSummary:
    gh_tokens = {company_key(c.name): c.token for c in companies if c.ats == "greenhouse"}
    where = "description = '' AND status != 'posting_closed' AND ats IN ('greenhouse', 'lever', 'ashby')"
    where += " AND enrich_status IN ('', 'failed')" if retry_failed else " AND enrich_status = ''"
    rows = conn.execute(
        f"SELECT id, ats, url, company FROM jobs WHERE {where} ORDER BY date_posted DESC LIMIT ?", (limit,)
    ).fetchall()
    summary = EnrichSummary()
    ashby_boards: dict[str, dict[str, dict]] = {}
    for i, r in enumerate(rows, 1):
        tgt = target_for(r["ats"], r["url"], r["company"], gh_tokens)
        if tgt is None:
            with conn:
                conn.execute("UPDATE jobs SET enrich_status = 'unsupported' WHERE id = ?", (r["id"],))
            summary.skipped_unsupported += 1
            continue
        summary.attempted += 1
        try:
            if tgt.ats == "greenhouse":
                text = html_to_text(http.get_json(tgt.api_url).get("content", ""))
            elif tgt.ats == "lever":
                text = _lever_text(http.get_json(tgt.api_url))
            else:
                if tgt.api_url not in ashby_boards:
                    data = http.get_json(tgt.api_url)
                    ashby_boards[tgt.api_url] = {j["id"]: j for j in data.get("jobs", [])}
                j = ashby_boards[tgt.api_url].get(tgt.job_id)
                if j is None:
                    raise LookupError("job no longer on board")
                text = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
            if not text:
                raise LookupError("empty description")
            with conn:
                conn.execute("UPDATE jobs SET description = ?, enrich_status = 'ok' WHERE id = ?", (text, r["id"]))
            summary.filled += 1
        except Exception as exc:
            with conn:
                conn.execute("UPDATE jobs SET enrich_status = 'failed' WHERE id = ?", (r["id"],))
            summary.failed += 1
            if len(summary.errors) < 5:
                summary.errors.append(f"job {r['id']}: {type(exc).__name__}: {str(exc)[:80]}")
        if i % 50 == 0:
            log(f"  ...{i}/{len(rows)} checked, {summary.filled} descriptions added")
    return summary
