"""Field mapper: real form questions -> known profile keys.

Each question ends up as exactly one of:
- a profile value (with its source),
- a protected question (never filled from the profile; only your per-application answer is used),
- a DRAFT written answer built from facts (see drafts.py),
- NEEDS_HUMAN.

Mapping uses local rules. An optional Claude step can map labels the rules don't recognize,
but only onto the fixed list of profile keys below, never to free text. Values always come
from the profile, never from the model.
"""

from __future__ import annotations

import re
from typing import Callable

from jobpilot.core.protected import protected_category
from jobpilot.forms.greenhouse import FormQuestion
from jobpilot.onboarding.profile import Profile
from jobpilot.review.drafts import draft_answer, is_open_ended

# The only keys a question can map to. Order matters for the label rules below.
PROFILE_KEYS: dict[str, str] = {
    "full_name": "contact.first_name + last_name", "current_company": "experience[0].company (if current)",
    "first_name": "contact.first_name", "last_name": "contact.last_name", "preferred_name": "contact.preferred_name",
    "email": "contact.email", "phone": "contact.phone", "location": "contact.city, contact.state",
    "country": "contact.country", "linkedin": "links.linkedin", "github": "links.github",
    "portfolio": "links.portfolio", "school": "education[0].school", "degree": "education[0].degree",
    "field_of_study": "education[0].field_of_study", "graduation": "education[0].end", "gpa": "education[0].gpa",
    "earliest_start": "targets.earliest_start", "resume": "resume versions",
}

BY_FIELD_ID = {"first_name": "first_name", "last_name": "last_name", "preferred_name": "preferred_name",
               "email": "email", "phone": "phone", "resume": "resume", "candidate-location": "location",
               "school--0": "school", "degree--0": "degree",
               # Lever
               "name": "full_name", "location": "location", "org": "current_company", "urls[LinkedIn]": "linkedin",
               "urls[GitHub]": "github", "urls[Portfolio]": "portfolio"}

LABEL_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.IGNORECASE), k) for p, k in [
    (r"linked\s*in", "linkedin"),
    (r"\bgit\s*hub\b", "github"),
    (r"\b(portfolio|personal (web)?site|website)\b", "portfolio"),
    (r"\bpreferred (first )?name\b", "preferred_name"),
    (r"^first name\b", "first_name"),
    (r"^last name\b|\bsurname\b", "last_name"),
    (r"^e-?mail( address)?\b", "email"),
    (r"^(phone|mobile)( number)?\b", "phone"),
    (r"\bgraduat\w*\b.*\b(date|year|when)\b|\b(expected|anticipated) graduation\b|\bwhen (do|will) you graduate\b",
     "graduation"),
    (r"\bgpa\b|\bgrade point\b", "gpa"),
    (r"\b(university|college|school)( name)?\b.*\b(attend|attending|name)\b|^(university|school|college)\b",
     "school"),
    (r"\b(major|field of study|discipline|area of study)\b", "field_of_study"),
    (r"^(highest )?degree\b|\bdegree (are you pursuing|type|level)\b|\bdegree are you (currently )?(enrolled|pursuing)",
     "degree"),
    (r"\b(earliest )?start date\b|\bavailable to start\b|\bwhen can you start\b", "earliest_start"),
    (r"\b(current|home) (city|location)\b|\bwhere (are you|do you) (located|live|based)\b", "location"),
    (r"^country\b|\bcountry of residence\b", "country"),
]]

# Legally binding or consent questions: autofill never answers these, even with your answer on file.
NEVER_AUTOFILL = ("legal", "background_check")


def profile_value(key: str, profile: Profile, resume_label: str) -> str:
    c, ln = profile.contact, profile.links
    ed = profile.education[0] if profile.education else None
    current = ""
    if profile.experience and profile.experience[0].end.strip().lower() in ("present", "current", "now"):
        current = profile.experience[0].company
    return {
        "full_name": profile.full_name, "current_company": current,
        "first_name": c.first_name, "last_name": c.last_name, "preferred_name": c.preferred_name,
        "email": c.email, "phone": c.phone, "location": ", ".join(p for p in (c.city, c.state) if p),
        "country": c.country, "linkedin": ln.linkedin, "github": ln.github, "portfolio": ln.portfolio,
        "school": ed.school if ed else "", "degree": ed.degree if ed else "",
        "field_of_study": ed.field_of_study if ed else "", "graduation": ed.end if ed else "",
        "gpa": ed.gpa if ed else "", "earliest_start": profile.targets.earliest_start, "resume": resume_label,
    }.get(key, "")


def map_label(q: FormQuestion) -> str | None:
    if q.field_id in BY_FIELD_ID:
        return BY_FIELD_ID[q.field_id]
    if q.kind == "textarea" or len(q.label) > 80:
        return None  # long or open questions ("share a link to something you've built...") aren't guessed
    for pat, key in LABEL_RULES:
        if pat.search(q.label):
            return key
    return None


def protected_for(q: FormQuestion) -> str:
    # An answer like "I have read and understand..." makes the whole question an acknowledgement.
    if any(protected_category(o) == "legal" for o in q.options):
        return "legal"
    cat = protected_category(q.label)
    if cat:
        return cat
    if q.section in ("eeoc", "demographic"):
        return "veteran" if "veteran" in q.field_id else "disability" if "disab" in q.field_id else "eeo_demographic"
    return ""


_STOP = {"are", "you", "your", "the", "for", "this", "that", "with", "will", "would", "have", "has", "our",
          "any", "can", "did", "does", "and", "please"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in _STOP}


def short_answer_for(label: str, answers: dict[str, str]) -> tuple[str, str] | None:
    """A reusable answer whose question clearly matches this label (most shared words wins)."""
    lt = _tokens(label)
    best, best_score = None, 0.0
    for q, a in answers.items():
        qt = _tokens(q)
        if not qt or not lt or not a:
            continue
        score = len(qt & lt) / len(qt | lt)
        if q.strip().lower() == label.strip().lower():
            score = 1.0
        if score > best_score:
            best, best_score = (q, a), score
    return best if best_score >= 0.6 else None


_SEASONS = {**{m: "spring" for m in ("jan", "feb", "mar", "apr", "may")},
            **{m: "summer" for m in ("jun", "jul", "aug")},
            **{m: "fall" for m in ("sep", "oct", "nov")}, "dec": "winter"}


def fit_option(value: str, options: list[str]) -> str | None:
    """The one option that clearly means `value`, or None. Never guesses between several."""
    if not options or not value:
        return None
    v = value.strip().lower()
    exact = [o for o in options if o.strip().lower() == v]
    if exact:
        return exact[0]
    starts = [o for o in options if o.lower().startswith(v) or v.startswith(o.lower())]
    if len(starts) == 1:
        return starts[0]
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{4})$", v)  # "May 2027" -> "Spring 2027"
    if m and m.group(1) in _SEASONS:
        season = [o for o in options if o.lower() == f"{_SEASONS[m.group(1)]} {m.group(2)}"]
        if len(season) == 1:
            return season[0]
    # "Bachelor of Science" -> "Bachelor's": same leading word, and only one option has it.
    first = re.match(r"[a-z]+", v)
    if first and len(first.group()) >= 4 and " " in v:
        same = [o for o in options if (m2 := re.match(r"[a-z]+", o.lower())) and m2.group() == first.group()]
        if len(same) == 1:
            return same[0]
    return None


def unmapped(questions: list[FormQuestion], profile: Profile) -> list[FormQuestion]:
    """Questions the local rules can't place (candidates for the optional Claude mapper)."""
    return [q for q in questions if not protected_for(q) and map_label(q) is None
            and not is_open_ended(q.label, q.kind) and short_answer_for(q.label, profile.short_answers) is None]


def plan_from_form(questions: list[FormQuestion], profile: Profile, resume_label: str, job, skills: list[dict]):
    """Build PlanFields for a real form. `skills` are the job's match lines (for drafts)."""
    from jobpilot.review.plan import DRAFT, FILLED, PlanField

    llm = {q.key: q.model_key for q in questions if q.model_key in PROFILE_KEYS}

    out: list[PlanField] = []
    for q in questions:
        f = PlanField(q.key, q.label, optional=not q.required, field_id=q.field_id, kind=q.kind,
                      options=list(q.options), section=q.section)
        cat = protected_for(q)
        if cat:
            f.protected, f.maps_to = cat, f"protected:{cat}"
            f.source = ("only you can answer this; autofill never ticks it, you do it on the form"
                        if cat in NEVER_AUTOFILL else "only you can answer this")
            out.append(f)
            continue
        key = map_label(q) or llm.get(q.key)
        if key:
            f.maps_to = key
            value = profile_value(key, profile, resume_label)
            how = "matched by label" if map_label(q) else "matched by Claude (key only; value from your profile)"
            src = f"profile: {PROFILE_KEYS[key]}" if key != "resume" else "resume versions"
            if value and q.kind in ("select", "multi_select", "radio"):
                opt = fit_option(value, q.options)
                if opt:
                    f.value, f.state, f.source = opt, FILLED, f"{src} ('{value}'), {how}"
                else:
                    f.source = f"your profile says '{value}', which isn't one of the options; pick one"
            elif value:
                f.value, f.state, f.source = value, FILLED, f"{src}, {how}"
            else:
                f.source = f"not in your profile ({PROFILE_KEYS[key]})"
            out.append(f)
            continue
        sa = short_answer_for(q.label, profile.short_answers)
        if sa:
            f.maps_to = f"short:{sa[0]}"
            value = fit_option(sa[1], q.options) if q.options else sa[1]
            if value:
                f.value, f.state, f.source = value, FILLED, f"profile: short_answers['{sa[0]}']"
            else:
                f.source = f"your saved answer '{sa[1]}' isn't one of the options; pick one"
            out.append(f)
            continue
        if is_open_ended(q.label, q.kind):
            got = draft_answer(q.label, job, skills)
            if got:
                f.value, f.state, f.source = got[0], DRAFT, got[1]
            else:
                f.source = "no facts in your profile to draft from; write this one yourself"
            out.append(f)
            continue
        f.source = "not something your profile covers"
        out.append(f)
    return out


# ---------------------------------------------------------------- optional Claude mapping

def claude_mapper(model: str, client=None) -> Callable[[list[FormQuestion]], dict[str, str]]:
    """Map unrecognized labels onto PROFILE_KEYS (or 'none'). Never produces values."""
    from typing import Literal

    from pydantic import BaseModel, create_model

    keys = tuple(PROFILE_KEYS) + ("none",)
    Item = create_model("Item", label_id=(str, ...), key=(Literal[keys], ...))  # type: ignore[valid-type]

    class Mapping(BaseModel):
        items: list[Item]  # type: ignore[valid-type]

    def run(questions: list[FormQuestion]) -> dict[str, str]:
        nonlocal client
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        listing = "\n".join(f"{q.key}: {q.label}" for q in questions)
        resp = client.messages.parse(
            model=model, max_tokens=1024, output_format=Mapping,
            system=("You map job-application form labels to profile keys. Answer with one of the allowed keys "
                    "for each label, or 'none' if no key clearly fits. Never invent keys. "
                    "Keys: " + ", ".join(f"{k} ({v})" for k, v in PROFILE_KEYS.items())),
            messages=[{"role": "user", "content": f"Labels (id: label):\n{listing}"}],
        )
        parsed = resp.parsed_output
        if parsed is None:
            return {}
        ids = {q.key for q in questions}
        return {i.label_id: i.key for i in parsed.items if i.label_id in ids and i.key != "none"}

    return run
