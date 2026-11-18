"""Company career feeds (RSS 2.0 or Atom) that you add in settings.yaml."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterator

from jobpilot.sources.base import JobSource, RawJob, ScopeResult
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date

ATOM = "{http://www.w3.org/2005/Atom}"


class RSSSource(JobSource):
    name = "rss"
    label = "Career RSS/Atom feeds"
    terms_note = "Feeds are published for syndication; each URL is still checked against robots.txt."

    def enabled(self) -> tuple[bool, str]:
        ok, why = super().enabled()
        if ok and not self.settings.sources.rss.feeds:
            return False, "no feeds listed in settings.yaml"
        return ok, why

    def fetch(self) -> Iterator[ScopeResult]:
        for feed in self.settings.sources.rss.feeds:
            try:
                xml = self.http.get(feed.url).text
                yield ScopeResult(feed.url, list(parse_feed(xml, feed.company)))
            except Exception as exc:
                yield ScopeResult(feed.url, [], complete=False, error=f"{type(exc).__name__}: {exc}"[:300])


def parse_feed(xml: str, company: str = "") -> Iterator[RawJob]:
    root = ET.fromstring(xml)
    channel_title = root.findtext("channel/title") or root.findtext(f"{ATOM}title") or ""
    company = company or channel_title
    items = root.findall("channel/item")
    if items:
        for it in items:
            link = (it.findtext("link") or "").strip()
            title = (it.findtext("title") or "").strip()
            locs = [x.text.strip() for x in it.findall("location") if x.text]
            yield RawJob(
                source_id=(it.findtext("guid") or link).strip(), title=title, company=company, url=link,
                locations=locs, description=html_to_text(it.findtext("description") or ""),
                date_posted=to_date(it.findtext("pubDate")), role_type=classify_role_type(title),
                remote=classify_remote(locs),
            )
        return
    for e in root.findall(f"{ATOM}entry"):
        link_el = e.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        title = (e.findtext(f"{ATOM}title") or "").strip()
        body = e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary") or ""
        yield RawJob(
            source_id=(e.findtext(f"{ATOM}id") or link).strip(), title=title, company=company, url=link,
            description=html_to_text(body),
            date_posted=to_date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")),
            role_type=classify_role_type(title),
        )
