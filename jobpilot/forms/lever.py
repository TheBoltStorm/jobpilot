"""Read a Lever application form (jobs.lever.co/{site}/{id}/apply).

Lever's robots.txt allows normal visitors (with a crawl delay, which the polite client honors).
The apply page is server-rendered HTML:
- standard fields have fixed names (name, email, phone, location, org, urls[LinkedIn], ...);
- each custom "card" carries its questions as JSON in a hidden `cards[<id>][baseTemplate]` input,
  and its inputs are named `cards[<id>][field<N>]`;
- voluntary surveys work the same way under `surveysResponses[<id>]`;
- EEO questions are native <select name="eeo[...]"> dropdowns.
So we read the questions straight from the page, without running its JavaScript.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from jobpilot.forms.greenhouse import FormQuestion

STANDARD = {
    # name: (label, kind)
    "resume": ("Resume/CV", "file"),
    "name": ("Full name", "text"),
    "email": ("Email", "text"),
    "phone": ("Phone", "text"),
    "location": ("Current location", "location"),
    "org": ("Current company", "text"),
    "urls[LinkedIn]": ("LinkedIn URL", "text"),
    "urls[Twitter]": ("Twitter URL", "text"),
    "urls[GitHub]": ("GitHub URL", "text"),
    "urls[Portfolio]": ("Portfolio URL", "text"),
    "urls[Other]": ("Other website", "text"),
    "comments": ("Additional information", "textarea"),
}
CARD_KINDS = {"text": "text", "textarea": "textarea", "multiple-choice": "radio", "multiple-select": "multi_select",
              "dropdown": "select"}
EEO_LABELS = {"eeo[gender]": "Gender", "eeo[race]": "Race", "eeo[veteran]": "Veteran status",
              "eeo[disability]": "Disability status", "eeo[disabilitySignature]": "Disability form signature",
              "eeo[disabilitySignatureDate]": "Disability form date"}


def form_url(posting_url: str) -> str:
    parts = urlsplit(posting_url)
    m = re.match(r"^/([^/]+)/([0-9a-f-]{36})", parts.path)
    if not m or "lever.co" not in parts.netloc:
        raise ValueError(f"not a Lever posting url: {posting_url}")
    host = "jobs.eu.lever.co" if ".eu." in parts.netloc else "jobs.lever.co"
    return f"https://{host}/{m.group(1)}/{m.group(2)}/apply"


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inputs: list[dict] = []
        self.selects: dict[str, list[str]] = {}
        self._select: str | None = None
        self._option = False
        self._has_form = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form" and a.get("id") == "application-form":
            self._has_form = True
        if tag in ("input", "textarea"):
            a["_tag"] = tag
            self.inputs.append(a)
        elif tag == "select":
            self._select = a.get("name", "")
            self.selects[self._select] = []
            self.inputs.append({"_tag": "select", **a})
        elif tag == "option" and self._select is not None:
            self._option = True

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        if tag == "option":
            self._option = False

    def handle_data(self, data):
        if self._option and self._select is not None and data.strip():
            text = data.strip()
            if not text.lower().startswith("select"):
                self.selects[self._select].append(text)


def parse_apply_page(html: str) -> list[FormQuestion]:
    c = _Collector()
    c.feed(html)
    if not c._has_form:
        return []
    names = {i.get("name", "") for i in c.inputs}
    required = {i.get("name", "") for i in c.inputs if "required" in i}
    out: list[FormQuestion] = []
    for name, (label, kind) in STANDARD.items():
        if name in names:
            out.append(FormQuestion(f"lv:{name}", name, label, kind, name in required, selector=f'[name="{name}"]'))
    for i in c.inputs:
        n = i.get("name", "")
        m = re.match(r"^(cards|surveysResponses)\[([^\]]+)\]\[baseTemplate\]$", n)
        if not m:
            continue
        try:
            tpl = json.loads(i.get("value", "") or "{}")
        except json.JSONDecodeError:
            continue
        group, cid = m.group(1), m.group(2)
        section = "demographic" if group == "surveysResponses" or tpl.get("type") == "survey" else "application"
        for idx, f in enumerate(tpl.get("fields") or []):
            fname = f"{group}[{cid}][field{idx}]" if group == "cards" else f"{group}[{cid}][responses][field{idx}]"
            kind = CARD_KINDS.get(f.get("type", ""), "text")
            out.append(FormQuestion(
                f"lv:{fname}", fname, str(f.get("text", "")).strip(), kind, bool(f.get("required")),
                [str(o.get("text", "")).strip() for o in f.get("options") or [] if o.get("text")],
                section=section, description=str(f.get("description", ""))[:500], selector=f'[name="{fname}"]'))
    for name, options in c.selects.items():
        if name.startswith("eeo["):
            out.append(FormQuestion(f"lv:{name}", name, f"{EEO_LABELS.get(name, name)} (voluntary EEO)", "select",
                                    False, options, section="eeoc", selector=f'select[name="{name}"]'))
    return out
