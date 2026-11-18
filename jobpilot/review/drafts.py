"""Draft answers to open questions ("Why this company?") from facts only.

The local drafter writes a short, plain answer using:
- what the job asks for (skills found in the posting), and
- the resume lines that show you have those skills (quoted from your profile).
Nothing else. If there aren't facts to use, there's no draft and the question stays
NEEDS_HUMAN. Every draft is labeled DRAFT and needs your review before approval.
"""

from __future__ import annotations

import re

OPEN_ENDED = re.compile(
    r"\b(why|interest(ed|s)?|excit\w*|motivat\w*|tell us (about|why)|what draws|cover letter|about yourself|"
    r"what makes you|anything else)\b", re.IGNORECASE)

_EXP = re.compile(r"^(?P<title>.*?) @ (?P<org>.*?): (?P<text>.+)$")
_PROJ = re.compile(r"^Project (?P<name>.*?): (?P<text>.+)$")
_IMPLIES = re.compile(r" \(implies [^)]*\)$")


def is_open_ended(label: str, kind: str) -> bool:
    return kind == "textarea" and bool(OPEN_ENDED.search(label))


def _first_person(text: str) -> str:
    text = text.strip().rstrip(".")
    return text[0].lower() + text[1:] if text and text[0].isupper() and not text[:2].isupper() else text


def _sentence(evidence: str) -> tuple[str, str] | None:
    """Turn a profile evidence line into (sentence, source) or None if it's just a skills-list entry."""
    ev = _IMPLIES.sub("", evidence)
    if m := _EXP.match(ev):
        org = m["org"] or "my last role"
        return f"At {org}, I {_first_person(m['text'])}.", f"experience at {org}"
    if m := _PROJ.match(ev):
        return f"In my {m['name']} project, I {_first_person(m['text'])}.", f"project {m['name']}"
    return None


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def draft_answer(label: str, job, skills: list[dict]) -> tuple[str, str] | None:
    """Return (draft text, note on what it was built from), or None when there are no facts to use."""
    have = [s for s in skills if s.get("status") == "have" and s.get("importance") in ("required", "preferred")]
    if not have:
        return None
    names = list(dict.fromkeys(s["name"] for s in have))[:3]
    sentences, sources, seen = [], [], set()
    for s in have:
        got = _sentence(s.get("evidence", ""))
        if got and got[0] not in seen:
            seen.add(got[0])
            sentences.append(got[0])
            sources.append(got[1])
        if len(sentences) == 2:
            break
    if not sentences:
        return None
    title, company = job["title"], job["company"]
    worked = "I've worked with it" if len(names) == 1 else (
        "I've worked with both" if len(names) == 2 else "I've worked with all three")
    text = (f"The {title} role asks for {_join(names)}, and {worked}. "
            + " ".join(sentences)
            + f" I'd like to keep building on that at {company}.")
    note = (f"Drafted from the posting's skills ({', '.join(names)}) and your profile ({'; '.join(sources)}). "
            "Make it yours before approving.")
    return text, note
