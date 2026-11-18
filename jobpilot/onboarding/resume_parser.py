"""Local resume parser (no API needed).

Extracts text from PDF/DOCX, splits it into sections, and pulls out contact
info, education, experience, projects and skills with simple heuristics.

Design rules:
- Every extracted item records the line it came from (``line``), so later
  phases can show provenance.
- When a heuristic isn't sure (e.g. which part of a header is the job title),
  it leaves the field empty and keeps the raw ``header`` text. The onboarding
  interview then asks the human. We never guess.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- text extraction

BULLET_CHARS = "•●▪◦‣○■□➢➤►-*–·"
_BULLET_RE = re.compile(rf"^\s*[{re.escape(BULLET_CHARS)}]\s*")


def extract_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif suffix == ".docx":
        import docx

        document = docx.Document(str(path))
        parts: list[str] = []
        for para in document.paragraphs:
            style = (para.style.name or "").lower() if para.style is not None else ""
            prefix = "• " if "list" in style and para.text.strip() else ""
            parts.append(prefix + para.text)
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(dict.fromkeys(cells)))  # dedupe merged cells
        text = "\n".join(parts)
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported resume format: {suffix} (use PDF or DOCX)")
    return normalize_text(text)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    # collapse runs of blank lines
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- sections

SECTION_ALIASES: dict[str, list[str]] = {
    "summary": ["summary", "professional summary", "profile", "objective", "about me", "about"],
    "education": ["education", "academic background", "education & coursework"],
    "experience": [
        "experience", "work experience", "professional experience", "employment", "employment history",
        "work history", "relevant experience", "internships", "internship experience",
    ],
    "projects": ["projects", "personal projects", "academic projects", "selected projects", "technical projects"],
    "skills": [
        "skills", "technical skills", "skills & interests", "skills and interests", "core competencies",
        "technologies", "skills & tools", "tools & technologies",
    ],
    "certifications": ["certifications", "certificates", "licenses & certifications", "certifications & licenses"],
    "awards": ["awards", "honors", "honors & awards", "awards & honors", "achievements"],
    "leadership": [
        "leadership", "activities", "leadership & activities", "extracurricular activities", "extracurriculars",
        "volunteer", "volunteering", "volunteer experience", "involvement",
    ],
}
_ALIAS_TO_SECTION = {alias: sec for sec, aliases in SECTION_ALIASES.items() for alias in aliases}


def _heading_key(line: str) -> str | None:
    if not line or len(line) > 45 or _BULLET_RE.match(line):
        return None
    key = re.sub(r"[:\s]+$", "", line).strip().lower()
    key = key.replace(" and ", " & ")
    return _ALIAS_TO_SECTION.get(key) or _ALIAS_TO_SECTION.get(key.replace(" & ", " and "))


@dataclass
class Line:
    no: int  # 1-based line number in normalized text
    text: str

    @property
    def is_bullet(self) -> bool:
        return bool(_BULLET_RE.match(self.text))

    @property
    def clean(self) -> str:
        return _BULLET_RE.sub("", self.text).strip()


def split_sections(text: str) -> tuple[list[Line], dict[str, list[Line]]]:
    """Return (header lines before first section, {section: lines})."""
    header: list[Line] = []
    sections: dict[str, list[Line]] = {}
    current: str | None = None
    for i, raw in enumerate(text.split("\n"), start=1):
        sec = _heading_key(raw)
        if sec:
            current = sec
            sections.setdefault(sec, [])
            continue
        if not raw:
            continue
        line = Line(i, raw)
        (sections[current] if current else header).append(line)
    return header, sections


# ---------------------------------------------------------------- patterns

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/[\w\-./?=&%#~+]*)?", re.IGNORECASE)

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Fall|Autumn|Winter)"
_DATE = rf"(?:{_MONTH}\.?,?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_END = rf"(?:{_DATE}|Present|Current|Now|Ongoing)"
DATE_RANGE_RE = re.compile(rf"(?P<start>{_DATE})\s*(?:-|–|—|to)\s*(?P<end>{_END})", re.IGNORECASE)
SINGLE_DATE_RE = re.compile(rf"(?:(?:Expected|Anticipated|Graduat\w*)[:\s]+)?(?P<date>{_DATE})", re.IGNORECASE)
GPA_RE = re.compile(r"GPA[:\s]*([0-4]\.\d{1,2})(?:\s*/\s*4\.0+)?", re.IGNORECASE)

DEGREE_RE = re.compile(
    r"\b(Bachelor(?:'s)?(?: of [A-Z][\w]+(?: [A-Z][\w]+)*)?|Master(?:'s)?(?: of [A-Z][\w]+(?: [A-Z][\w]+)*)?|"
    r"Associate(?:'s)?(?: of [A-Z][\w]+(?: [A-Z][\w]+)*)?|Doctor(?:ate)?|Ph\.?\s?D\.?|"
    r"B\.?S\.?E?|B\.?A\.?|M\.?S\.?|M\.?A\.?|M\.?B\.?A\.?|B\.?Eng\.?|M\.?Eng\.?|A\.?A\.?|A\.?S\.?|High School Diploma)\b"
)
SCHOOL_RE = re.compile(r"\b(University|College|Institute|School|Academy|Polytechnic|Conservatory)\b", re.IGNORECASE)
TITLE_WORDS = re.compile(
    r"\b(intern|internship|co-?op|engineer|developer|analyst|assistant|associate|researcher|scientist|"
    r"manager|lead|designer|consultant|tutor|coordinator|specialist|technician|administrator|architect|"
    r"fellow|teaching|director|officer|representative|volunteer|president|founder|member|programmer)\b",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(r"^(Remote|Hybrid|[A-Z][a-zA-Z .'-]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+))$")
_SPLIT_RE = re.compile(r"\s+[|·•]\s+|\s+[—–]\s+|\s+-\s+|\t")


# ---------------------------------------------------------------- data model

@dataclass
class Contact:
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    other_links: list[str] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)  # field -> source line


@dataclass
class Education:
    header: str
    line: int
    school: str = ""
    degree: str = ""
    field_of_study: str = ""
    start: str = ""
    end: str = ""
    gpa: str = ""
    coursework: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


@dataclass
class Entry:
    """An experience / project / leadership entry."""

    header: str
    line: int
    title: str = ""
    organization: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class ParsedResume:
    contact: Contact
    summary: str = ""
    education: list[Education] = field(default_factory=list)
    experience: list[Entry] = field(default_factory=list)
    projects: list[Entry] = field(default_factory=list)
    leadership: list[Entry] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    skill_groups: dict[str, list[str]] = field(default_factory=dict)
    certifications: list[str] = field(default_factory=list)
    awards: list[str] = field(default_factory=list)
    sections_found: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- parsing helpers

def _find_dates(text: str) -> tuple[str, str, str]:
    """Return (text_without_dates, start, end)."""
    m = DATE_RANGE_RE.search(text)
    if m:
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        return _tidy(rest), m.group("start"), m.group("end")
    m = SINGLE_DATE_RE.search(text)
    if m and re.search(r"\d{4}", m.group(0)):
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        return _tidy(rest), "", m.group("date")
    return text, "", ""


def _tidy(s: str) -> str:
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,|·•—–-\t")


def _parts(s: str) -> list[str]:
    parts = [p.strip(" ,") for p in _SPLIT_RE.split(s) if p.strip(" ,")]
    if len(parts) == 1 and "," in s:
        # "Acme Corp, Austin, TX" -> keep trailing "City, ST" together
        m = re.match(r"^(.*?),\s*([A-Z][a-zA-Z .'-]+,\s*[A-Z]{2})$", s)
        if m:
            parts = [m.group(1).strip(), m.group(2).strip()]
    return parts


def _blocks(lines: list[Line]) -> list[tuple[list[Line], list[Line]]]:
    """Group lines into (header_lines, bullet_lines) blocks."""
    blocks: list[tuple[list[Line], list[Line]]] = []
    heads: list[Line] = []
    bullets: list[Line] = []
    for ln in lines:
        if ln.is_bullet:
            bullets.append(ln)
            continue
        if bullets and _is_continuation(bullets[-1], ln):
            bullets[-1] = Line(bullets[-1].no, bullets[-1].text + " " + ln.text)
            continue
        if bullets:  # a non-bullet after bullets starts a new block...
            if blocks or heads:
                blocks.append((heads, bullets))
            heads, bullets = [], []
        elif heads and len(heads) >= 2 and DATE_RANGE_RE.search(ln.text) and any(
            DATE_RANGE_RE.search(h.text) for h in heads
        ):
            # ...as does a second dated header with no bullets in between.
            blocks.append((heads, []))
            heads = []
        heads.append(ln)
    if heads or bullets:
        blocks.append((heads, bullets))
    return [b for b in blocks if b[0]]


def _is_continuation(prev: Line, ln: Line) -> bool:
    """A PDF-wrapped bullet: next line starts lowercase, or prev bullet didn't end a sentence
    and the line doesn't look like a header (no dates, no separators)."""
    t = ln.text
    if t[:1].islower():
        return True
    looks_like_header = bool(DATE_RANGE_RE.search(t) or _SPLIT_RE.search(t))
    return not prev.text.rstrip().endswith((".", "!", "?", ";")) and not looks_like_header and len(t) > 40


def _bullets_with_wraps(heads: list[Line], bullets: list[Line]) -> list[str]:
    return [b.clean for b in bullets if b.clean]


def _parse_entry(heads: list[Line], bullets: list[Line], kind: str = "experience") -> Entry:
    header_text = " | ".join(h.text for h in heads)
    entry = Entry(header=header_text, line=heads[0].no)
    pieces: list[str] = []
    for h in heads:
        rest, start, end = _find_dates(h.text)
        if end and not entry.end:
            entry.start, entry.end = start, end
        pieces.extend(_parts(rest))
    leftovers: list[str] = []
    if kind == "projects" and pieces:
        # Projects: first piece is the project name.
        entry.title = pieces.pop(0)
    for p in pieces:
        if not entry.location and LOCATION_RE.match(p):
            entry.location = p
        elif not entry.title and TITLE_WORDS.search(p):
            entry.title = p
        else:
            leftovers.append(p)
    # Only assign the organization when exactly one candidate remains; otherwise ask the human.
    if len(leftovers) == 1 and (entry.title or kind == "projects"):
        entry.organization = leftovers[0]
    entry.bullets = _bullets_with_wraps(heads, bullets)
    return entry


def _parse_education(lines: list[Line]) -> list[Education]:
    out: list[Education] = []
    for heads, bullets in _blocks(lines):
        ed = Education(header=" | ".join(h.text for h in heads), line=heads[0].no)
        for ln in heads + bullets:
            txt = ln.clean
            low = txt.lower()
            if low.startswith(("relevant coursework", "coursework", "courses")):
                _, _, rest = txt.partition(":")
                ed.coursework.extend(_split_list(rest))
                continue
            gm = GPA_RE.search(txt)
            if gm and not ed.gpa:
                ed.gpa = gm.group(1)
                txt = _tidy(GPA_RE.sub("", txt))
            rest, start, end = _find_dates(txt)
            if end and not ed.end:
                ed.start, ed.end = start, end
                txt = rest
            for p in _parts(txt):
                if not ed.school and SCHOOL_RE.search(p):
                    ed.school = p
                elif not ed.degree and DEGREE_RE.search(p):
                    ed.degree, ed.field_of_study = _split_degree(p)
                elif p and ln.is_bullet:
                    ed.details.append(p)
        out.append(ed)
    return out


def _split_degree(text: str) -> tuple[str, str]:
    for sep in (" in ", ", "):
        head, found, tail = text.partition(sep)
        if found and DEGREE_RE.search(head):
            return head.strip(), tail.strip()
    m = DEGREE_RE.search(text)
    if m:
        rest = _tidy(text[m.end():])
        return m.group(0).strip(), rest
    return text, ""


def _split_list(s: str) -> list[str]:
    items = re.split(r"\s*[,;|•·]\s*", s)
    return [i.strip(" .") for i in items if i.strip(" .")]


def _parse_skills(lines: list[Line]) -> tuple[list[str], dict[str, list[str]]]:
    flat: list[str] = []
    groups: dict[str, list[str]] = {}
    for ln in lines:
        txt = ln.clean
        cat = ""
        if ":" in txt:
            cat, _, txt = txt.partition(":")
            cat = cat.strip()
        items = _split_list(txt)
        if cat:
            groups.setdefault(cat, []).extend(items)
        flat.extend(items)
    seen: set[str] = set()
    deduped = [s for s in flat if not (s.lower() in seen or seen.add(s.lower()))]
    return deduped, groups


def _parse_contact(header: list[Line], full_text: str) -> Contact:
    c = Contact()
    # Name: first header line that isn't contact details.
    for ln in header[:3]:
        t = ln.text
        if EMAIL_RE.search(t) or PHONE_RE.search(t) or "http" in t.lower():
            continue
        words = t.split()
        if 1 < len(words) <= 5 and all(re.match(r"^[A-Za-z][A-Za-z.'-]*$", w) for w in words):
            c.name = t
            c.lines["name"] = ln.no
            break
    search_lines = header or [Line(i, t) for i, t in enumerate(full_text.split("\n")[:8], start=1)]
    for ln in search_lines:
        t = ln.text
        if not c.email and (m := EMAIL_RE.search(t)):
            c.email, c.lines["email"] = m.group(0), ln.no
        if not c.phone and (m := PHONE_RE.search(t)):
            c.phone, c.lines["phone"] = m.group(0), ln.no
        for chunk in re.split(r"\s*[|·•]\s*|\s{2,}", t):
            chunk = chunk.strip()
            if not chunk or EMAIL_RE.fullmatch(chunk):
                continue
            low = chunk.lower()
            if "linkedin.com" in low and not c.linkedin:
                c.linkedin, c.lines["linkedin"] = chunk, ln.no
            elif "github.com" in low and not c.github:
                c.github, c.lines["github"] = chunk, ln.no
            elif URL_RE.fullmatch(chunk) and "." in chunk and not PHONE_RE.fullmatch(chunk):
                if not c.portfolio:
                    c.portfolio, c.lines["portfolio"] = chunk, ln.no
                else:
                    c.other_links.append(chunk)
            elif not c.location and LOCATION_RE.match(chunk) and ln.no != c.lines.get("name"):
                c.location, c.lines["location"] = chunk, ln.no
    return c


# ---------------------------------------------------------------- entry point

def parse_text(text: str) -> ParsedResume:
    text = normalize_text(text)
    header, sections = split_sections(text)
    parsed = ParsedResume(contact=_parse_contact(header, text), sections_found=list(sections))

    if "summary" in sections:
        parsed.summary = " ".join(ln.clean for ln in sections["summary"])
    if "education" in sections:
        parsed.education = _parse_education(sections["education"])
    for key in ("experience", "projects", "leadership"):
        if key in sections:
            setattr(parsed, key, [_parse_entry(h, b, key) for h, b in _blocks(sections[key])])
    if "skills" in sections:
        parsed.skills, parsed.skill_groups = _parse_skills(sections["skills"])
    for key in ("certifications", "awards"):
        if key in sections:
            setattr(parsed, key, [ln.clean for ln in sections[key] if ln.clean])

    if not sections:
        parsed.warnings.append("No section headings recognized; most details will be asked in the interview.")
    for f in ("name", "email"):
        if not getattr(parsed.contact, f):
            parsed.warnings.append(f"Could not find {f} in resume.")
    for e in parsed.experience:
        if not (e.title and e.organization):
            parsed.warnings.append(f"Line {e.line}: unsure of title/company in '{e.header}' - will ask.")
    return parsed


def parse_resume(path: str | Path) -> tuple[str, ParsedResume]:
    text = extract_text(path)
    return text, parse_text(text)
