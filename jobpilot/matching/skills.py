"""Skill list loading, skill detection in text, and required/preferred section splitting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml


@dataclass
class Skill:
    key: str
    name: str
    cat: str
    family: str = ""
    implies: list[str] = field(default_factory=list)
    learn: str = ""
    regexes: list[re.Pattern] = field(default_factory=list)

    def found_in(self, text: str) -> re.Match | None:
        for rx in self.regexes:
            m = rx.search(text)
            if m:
                return m
        return None


def _alias_regex(alias: str) -> re.Pattern:
    # Word-ish boundaries that also work for names like "c++", "c#", ".net", "node.js".
    return re.compile(rf"(?<![\w+#]){re.escape(alias)}(?![\w+#]|\.\w)", re.IGNORECASE)


class Taxonomy:
    def __init__(self, data: dict):
        self.skills: dict[str, Skill] = {}
        for key, v in data.items():
            regexes = [_alias_regex(a) for a in v.get("aliases") or []]
            regexes += [re.compile(p) for p in v.get("patterns") or []]
            self.skills[key] = Skill(key, v["name"], v.get("cat", "Other"), v.get("family", ""),
                                     list(v.get("implies") or []), v.get("learn", ""), regexes)
        self.by_family: dict[str, set[str]] = {}
        for s in self.skills.values():
            if s.family:
                self.by_family.setdefault(s.family, set()).add(s.key)

    def find(self, text: str) -> dict[str, str]:
        """Return {skill_key: matched snippet} for every skill mentioned in text."""
        out: dict[str, str] = {}
        if not text:
            return out
        for key, skill in self.skills.items():
            m = skill.found_in(text)
            if m:
                start = max(0, m.start() - 60)
                out[key] = text[start: m.end() + 60].replace("\n", " ").strip()
        return out

    def expand_implied(self, keys: set[str]) -> set[str]:
        out = set(keys)
        frontier = list(keys)
        while frontier:
            k = frontier.pop()
            for imp in self.skills[k].implies if k in self.skills else []:
                if imp not in out:
                    out.add(imp)
                    frontier.append(imp)
        return out

    def siblings(self, key: str) -> set[str]:
        fam = self.skills[key].family if key in self.skills else ""
        return (self.by_family.get(fam, set()) - {key}) if fam else set()


@lru_cache(maxsize=4)
def _load(path: str | None) -> Taxonomy:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files("jobpilot.matching").joinpath("data/skills.yaml").read_text(encoding="utf-8")
    return Taxonomy(yaml.safe_load(text))


def load_taxonomy(custom_path: Path | None = None) -> Taxonomy:
    """The built-in list, or data/skills.yaml if the user made their own copy."""
    return _load(str(custom_path) if custom_path and custom_path.is_file() else None)


# ---------------------------------------------------------------- description sections

_REQUIRED_HEADS = re.compile(
    r"^\W*(minimum|basic|required|must[- ]have|key)?\s*(qualifications?|requirements?|skills|experience)\b|"
    r"^\W*(what you(?:'|’)?ll need|what we(?:'|’)?re looking for|you (?:have|bring|are|should have)|who you are|"
    r"about you|what you bring|you might be a (?:good )?fit|ideal candidate|your background|we(?:'|’)?re looking for)",
    re.IGNORECASE,
)
_PREFERRED_HEADS = re.compile(
    r"^\W*(preferred|desired|bonus|nice[- ]to[- ]haves?|additional|extra credit|it(?:'|’)?s a plus|pluses|"
    r"ideally|even better|you might also have|bonus points)",
    re.IGNORECASE,
)
_OTHER_HEADS = re.compile(
    r"^\W*(about (?:us|the (?:team|company|role))|benefits|perks|what we offer|compensation|pay|salary|"
    r"responsibilities|what you(?:'|’)?ll do|the role|equal opportunity|our values|why join)",
    re.IGNORECASE,
)
_PREFERRED_INLINE = re.compile(r"\b(preferred|a plus|nice to have|bonus|ideally|is a plus|desirable)\b", re.IGNORECASE)


def split_sections(description: str) -> dict[str, str]:
    """Split a description into required / preferred / other text using its headings.

    Lines under a "Requirements"/"Qualifications" heading count as required; under
    "Preferred"/"Nice to have" as preferred; a line that itself says "preferred" or
    "a plus" counts as preferred wherever it is. Everything else is "other".
    """
    buckets = {"required": [], "preferred": [], "other": []}
    current = "other"
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        # A heading is short and either ends with ":" or has only a few words.
        # The heading line itself also goes into its new bucket ("Experience with Go:" has a skill in it).
        is_heading = not line.startswith("•") and (line.endswith(":") and len(line) < 80 or len(line.split()) <= 6)
        if is_heading and _PREFERRED_HEADS.match(line):
            current = "preferred"
        elif is_heading and _REQUIRED_HEADS.match(line):
            current = "required"
        elif is_heading and _OTHER_HEADS.match(line):
            current = "other"
        bucket = "preferred" if _PREFERRED_INLINE.search(line) and current != "other" else current
        buckets[bucket].append(line)
    return {k: "\n".join(v) for k, v in buckets.items()}
