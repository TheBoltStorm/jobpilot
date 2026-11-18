"""Protected question categories.

Questions in these categories are ALWAYS answered by the human. JobPilot never
stores a default for them in the profile and never fills them in a form. The
same patterns are used by onboarding (to refuse saving such answers) and, later,
by autofill (to force NEEDS_HUMAN).
"""

from __future__ import annotations

import re

PROTECTED_CATEGORIES: dict[str, list[str]] = {
    # Checked in order: "visa sponsorship" is a sponsorship question even though it mentions a visa.
    "sponsorship": [r"sponsor"],
    "work_authorization": [
        r"work(ing)?\s*auth", r"authori[sz]ed\s+to\s+work", r"legally\s+(eligible|able|permitted)",
        r"eligib\w*\s+to\s+work", r"right\s+to\s+work", r"citizen", r"visa", r"\bopt\b", r"\bcpt\b",
        r"green\s*card", r"permanent\s+resident", r"immigration",
    ],
    "eeo_demographic": [
        r"\bgender\b", r"\bsex\b", r"\brace\b", r"ethnic", r"hispanic", r"latin[oax]", r"sexual\s+orientation",
        r"transgender", r"pronoun", r"\beeo", r"equal\s+(employment\s+)?opportunity", r"demographic",
        r"date\s+of\s+birth", r"\bage\b", r"religio", r"marital",
    ],
    "veteran": [r"veteran", r"military", r"armed\s+forces", r"protected\s+vet"],
    "disability": [r"disab", r"accommodat", r"impairment"],
    "salary": [r"salary", r"compensation", r"pay\s+(range|expectation|rate)", r"desired\s+pay", r"wage"],
    "background_check": [r"background\s+check", r"criminal", r"convict", r"felon", r"drug\s+(test|screen)"],
    "legal": [
        r"non[-\s]?compete", r"non[-\s]?disclosure", r"\bnda\b", r"agree(ment)?\s+to", r"i\s+(certify|attest|agree)",
        r"signature", r"\bsign\b", r"terms\s+(and|&)\s+conditions", r"consent", r"acknowledg",
        r"social\s+security", r"\bssn\b",
    ],
}

_COMPILED = {cat: [re.compile(p, re.IGNORECASE) for p in pats] for cat, pats in PROTECTED_CATEGORIES.items()}


def protected_category(text: str) -> str | None:
    """Return the protected category a label/key falls into, or None."""
    normalized = re.sub(r"[_\-]+", " ", text or "")
    for cat, patterns in _COMPILED.items():
        if any(p.search(normalized) for p in patterns):
            return cat
    return None


def is_protected(text: str) -> bool:
    return protected_category(text) is not None
