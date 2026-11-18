"""Shared helpers: HTML -> text, role type / remote / ATS detection, dates."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

# ---------------------------------------------------------------- HTML

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:  # Greenhouse double-escapes content
        raw = html.unescape(raw)
    parser = _TextExtractor()
    parser.feed(raw)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- role type / remote

_INTERN = re.compile(r"\b(intern|internship|summer analyst|summer associate|apprentice(ship)?)\b", re.I)
_COOP = re.compile(r"\bco[-\s]?op\b", re.I)
_PART = re.compile(r"\bpart[-\s]?time\b", re.I)
_FULL = re.compile(r"\bfull[-\s]?time\b", re.I)


def classify_role_type(title: str, hint: str = "") -> str:
    """Return internship | co-op | part-time | full-time | '' (unknown). Title wins over hint."""
    for text in (title, hint):
        if not text:
            continue
        if _COOP.search(text):
            return "co-op"
        if _INTERN.search(text):
            return "internship"
        if _PART.search(text):
            return "part-time"
    if hint and (_FULL.search(hint) or hint.lower().replace("_", "") in {"fulltime", "new_grad", "newgrad", "permanent"}):
        return "full-time"
    return ""


def classify_remote(locations: list[str], hint: str = "") -> str:
    h = (hint or "").lower()
    if "hybrid" in h:
        return "hybrid"
    if "remote" in h or h in {"true"}:
        return "remote"
    if h in {"onsite", "on-site", "in office", "in-office"}:
        return "onsite"
    joined = " ".join(locations).lower()
    if "hybrid" in joined:
        return "hybrid"
    if "remote" in joined:
        return "remote"
    return ""


# ---------------------------------------------------------------- ATS detection

_ATS_HOSTS = [
    ("greenhouse", ("greenhouse.io",)),
    ("lever", ("lever.co",)),
    ("ashby", ("ashbyhq.com",)),
    ("workday", ("myworkdayjobs.com", "myworkdaysite.com", "workday.com")),
    ("icims", ("icims.com",)),
    ("taleo", ("taleo.net",)),
    ("successfactors", ("successfactors.com", "sapsf.com")),
    ("smartrecruiters", ("smartrecruiters.com",)),
    ("workable", ("workable.com",)),
    ("jobvite", ("jobvite.com",)),
    ("oracle", ("oraclecloud.com",)),
    ("rippling", ("rippling.com",)),
    ("usajobs", ("usajobs.gov",)),
]

# ATSs that require creating an account before applying. JobPilot never creates accounts.
ACCOUNT_WALL_ATS = {"workday", "icims", "taleo", "successfactors", "oracle", "usajobs"}
SUPPORTED_ATS = {"greenhouse", "lever", "ashby"}


def detect_ats(url: str) -> str:
    if not url:
        return ""
    host = urlsplit(url).netloc.lower()
    if "gh_jid=" in url:
        return "greenhouse"
    for name, suffixes in _ATS_HOSTS:
        if any(host == s or host.endswith("." + s) for s in suffixes):
            return name
    return "other"


# ---------------------------------------------------------------- dates

def to_date(value) -> str:
    """Accept epoch seconds/ms, ISO strings, RFC 822 strings. Return YYYY-MM-DD or ''."""
    if value in (None, "", 0):
        return ""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            n = float(value)
            if n > 1e11:  # milliseconds
                n /= 1000
            return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat()
        s = str(value).strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(s).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return ""
