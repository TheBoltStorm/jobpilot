"""Read a Greenhouse job's real application questions from the public job-board API.

GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true returns every
question on the application form: labels, field names, types, options, and the voluntary
EEO / demographic sections. That's what the form on job-boards.greenhouse.io is built from,
so we can plan answers (and show them for review) without opening a browser.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from jobpilot.sources.normalize import html_to_text

TYPE_MAP = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multi_select",
}


@dataclass
class FormQuestion:
    key: str  # stable id used in the plan, e.g. "gh:question_123"
    field_id: str  # DOM id (or checkbox group name for multi_select)
    label: str
    kind: str  # text | textarea | file | select | multi_select | search_select | location
    required: bool = False
    options: list[str] = field(default_factory=list)
    section: str = "application"  # application | location | education | eeoc | demographic
    description: str = ""
    model_key: str = ""  # profile key suggested by the optional Claude mapper (a key, never a value)

    def to_dict(self) -> dict:
        return asdict(self)


def form_url_from_api(api_url: str) -> str:
    """https://boards-api[.eu].greenhouse.io/v1/boards/acme/jobs/123 -> job-boards[.eu].greenhouse.io/acme/jobs/123"""
    parts = urlsplit(api_url)
    m = re.match(r"^/v1/boards/([^/]+)/jobs/(\d+)", parts.path)
    if not m:
        raise ValueError(f"not a Greenhouse job API url: {api_url}")
    host = "job-boards.eu.greenhouse.io" if ".eu." in parts.netloc else "job-boards.greenhouse.io"
    return f"https://{host}/{m.group(1)}/jobs/{m.group(2)}"


def _options(f: dict) -> list[str]:
    return [str(v.get("label", "")).strip() for v in f.get("values") or [] if str(v.get("label", "")).strip()]


def parse_questions(data: dict) -> list[FormQuestion]:
    out: list[FormQuestion] = []
    for q in data.get("questions") or []:
        fields = [f for f in q.get("fields") or [] if f.get("type") != "input_hidden"]
        if not fields:
            continue
        f = fields[0]  # e.g. Resume/CV has a file field plus a "paste text" alternative; the file is first
        kind = TYPE_MAP.get(f.get("type", ""), "text")
        name = str(f.get("name", ""))
        out.append(FormQuestion(
            key=f"gh:{name}", field_id=name, label=str(q.get("label", "")).strip(), kind=kind,
            required=bool(q.get("required")), options=_options(f),
            description=html_to_text(q.get("description") or "")[:500],
        ))
    for q in data.get("location_questions") or []:
        for f in q.get("fields") or []:
            if f.get("name") == "location":
                out.append(FormQuestion("gh:location", "candidate-location", "Location (city)", "location",
                                        bool(q.get("required")), section="location"))
    edu = str(data.get("education") or "")
    if edu.startswith("education_"):
        req = edu == "education_required"
        out.append(FormQuestion("gh:school", "school--0", "School", "search_select", req, section="education"))
        out.append(FormQuestion("gh:degree", "degree--0", "Degree", "search_select", req, section="education"))
    for block in data.get("compliance") or []:
        for q in block.get("questions") or []:
            for f in q.get("fields") or []:
                name = str(f.get("name", ""))
                label = {"race": "Race", "gender": "Gender", "veteran_status": "Veteran status",
                         "disability_status": "Disability status"}.get(name, str(q.get("label", name)))
                out.append(FormQuestion(f"gh:{name}", name, f"{label} (voluntary EEO)",
                                        TYPE_MAP.get(f.get("type", ""), "select"), bool(q.get("required")),
                                        _options(f), section="eeoc"))
    demo = data.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        qid = str(q.get("id", ""))
        out.append(FormQuestion(
            f"gh:demo_{qid}", qid, str(q.get("label", "")).strip(), TYPE_MAP.get(q.get("type", ""), "select"),
            bool(q.get("required")),
            [str(a.get("label", "")).strip() for a in q.get("answer_options") or [] if a.get("label")],
            section="demographic"))
    return out
