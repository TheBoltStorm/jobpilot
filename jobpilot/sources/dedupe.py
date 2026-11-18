"""Cross-source dedupe.

Two postings are the same job if either:
  1. their canonical URLs match (tracking params stripped, /apply removed, gh_jid kept), or
  2. same normalized company + same primary city + titles that match after normalization
     (or are >= 0.92 similar).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_KEEP_PARAMS = {"gh_jid", "jobid", "job_id", "id", "jk", "token"}
_COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|group|holdings|plc|gmbh|the)\b\.?"
)
_TITLE_NOISE = re.compile(
    r"\b(19|20)\d{2}\b|\b(summer|fall|spring|winter|autumn)\b|\b(remote|hybrid|onsite|on-site)\b|"
    r"\(.*?\)|\[.*?\]|\bnew\s*grad(uate)?\b|\buniversity\s*grad(uate)?\b"
)
_TITLE_SYNONYMS = [
    (re.compile(r"\binternship\b"), "intern"),
    (re.compile(r"\bswe\b"), "software engineer"),
    (re.compile(r"\bsr\.?\b"), "senior"),
    (re.compile(r"\bjr\.?\b"), "junior"),
    (re.compile(r"\bml\b"), "machine learning"),
    (re.compile(r"\bengineering intern\b"), "engineer intern"),
]
SIMILARITY = 0.92


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    if host == "job-boards.greenhouse.io":
        host = "boards.greenhouse.io"
    path = re.sub(r"/(apply|application)/?$", "", parts.path).rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() in _KEEP_PARAMS))
    return urlunsplit(("https", host, path, query, ""))


def company_key(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = _COMPANY_SUFFIX.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def title_key(title: str) -> str:
    s = title.lower()
    s = _TITLE_NOISE.sub(" ", s)
    for pat, rep in _TITLE_SYNONYMS:
        s = pat.sub(rep, s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_PLACEHOLDER_LOCATIONS = {
    "in office", "in-office", "office", "onsite", "on-site", "on site", "hybrid", "multiple locations",
    "various", "various locations", "tbd", "n/a", "united states", "usa", "us", "anywhere",
}


def location_key(locations: list[str]) -> str:
    """Primary city, 'remote', or '' when unknown/placeholder (unknown matches any location)."""
    real = [loc for loc in locations if loc.strip().lower() not in _PLACEHOLDER_LOCATIONS]
    if not real:
        return ""
    first = real[0].lower()
    if "remote" in first:
        return "remote"
    city = re.split(r"[,(/-]", first)[0]
    city = {"nyc": "new york", "new york city": "new york", "sf": "san francisco", "bay area": "san francisco"}.get(
        city.strip(), city
    )
    return re.sub(r"[^a-z]+", "", city)


def dedupe_key(company: str, title: str, locations: list[str]) -> str:
    return f"{company_key(company)}|{title_key(title)}|{location_key(locations)}"


_LEVEL_WORDS = {"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5", "senior", "junior", "staff", "principal",
                "lead", "intern", "manager", "director", "head", "associate", "co", "op"}


def _levels(key: str) -> set[str]:
    return {w for w in key.split() if w in _LEVEL_WORDS}


def titles_match(a: str, b: str) -> bool:
    """Equal after normalization, or very similar AND the same seniority markers (I vs II, senior, intern...)."""
    ka, kb = title_key(a), title_key(b)
    if ka == kb:
        return True
    return _levels(ka) == _levels(kb) and SequenceMatcher(None, ka, kb).ratio() >= SIMILARITY
