"""Fill a Greenhouse application form (job-boards.greenhouse.io) from an approved plan.

What this code may do on the page: type into text boxes, pick options in dropdowns,
tick checkboxes, attach the resume file. What it never does: click Submit (or any
submit control), press Enter, solve a CAPTCHA, sign in, or create an account.
In dry-run mode it only outlines the fields and reports what it would do.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from jobpilot.review.mapping import NEVER_AUTOFILL

BLUE, ORANGE, GREY = "#2f6fdb", "#d97706", "#9ca3af"

# Runs in the page. Blocks form submission while JobPilot is working, even if something
# on the page tries to submit. Removed (in fill mode) only when the form is handed to you.
GUARD_JS = """
() => {
  if (window.__jobpilotGuard !== undefined) { window.__jobpilotGuard = true; return; }
  window.__jobpilotGuard = true;
  window.__jobpilotBlocked = 0;
  const block = (e) => {
    if (!window.__jobpilotGuard) return;
    const isSubmit = e.type === 'submit' ||
      (e.target && e.target.closest && e.target.closest('button[type=submit], input[type=submit], [data-submit]'));
    if (isSubmit) { e.preventDefault(); e.stopImmediatePropagation(); window.__jobpilotBlocked++; }
  };
  for (const t of ['submit', 'click', 'mousedown', 'mouseup', 'pointerdown', 'pointerup', 'keydown']) {
    document.addEventListener(t, (e) => { if (t !== 'keydown' || e.key === 'Enter') block(e); }, true);
  }
}
"""
RELEASE_JS = "() => { window.__jobpilotGuard = false; }"

# Network guard: while JobPilot works, any non-GET request that looks like an application
# submission is aborted.
SUBMIT_URL = re.compile(r"(greenhouse\.io|lever\.co|ashbyhq\.com).*(/applications?\b|/apply\b|submit)", re.IGNORECASE)


@dataclass
class Step:
    key: str
    label: str
    kind: str
    action: str  # would_fill | filled | left_for_you | not_found | failed
    value: str = ""
    note: str = ""


@dataclass
class FillResult:
    outcome: str  # stopped_before_submit | dry_run_done | account_wall | captcha | form_not_found | error
    steps: list[Step] = field(default_factory=list)
    message: str = ""

    def to_json(self) -> list[dict]:
        return [asdict(s) for s in self.steps]

    def count(self, action: str) -> int:
        return sum(1 for s in self.steps if s.action == action)


def decide(f: dict, resume_path: str | None) -> tuple[str, str]:
    """('fill', value) or ('left_for_you', reason) for one planned field."""
    state, value, cat = f.get("state"), (f.get("value") or "").strip(), f.get("protected", "")
    if cat:
        if cat in NEVER_AUTOFILL:
            return "left_for_you", "legally binding or consent: you tick this yourself"
        if state == "human" and value:
            return "fill", value  # your answer for this one application
        return "left_for_you", "only you can answer this"
    if f.get("kind") == "file":
        if f.get("maps_to") != "resume":
            return "left_for_you", "attach this yourself"
        if not resume_path:
            return "left_for_you", "no resume file found for this version"
        return "fill", resume_path
    if state == "needs_human" or not value:
        return "left_for_you", "optional, left blank" if f.get("optional") else "NEEDS_HUMAN"
    return "fill", value


class GreenhouseFiller:
    def __init__(self, page, dry_run: bool = True, timeout_ms: int = 5000):
        self.page = page
        self.dry_run = dry_run
        self.timeout = timeout_ms

    # ------------------------------------------------------------ page checks

    def check_page(self) -> str | None:
        """Problems that mean we stop and hand over to you. None if the form is there."""
        p = self.page
        if p.locator("input[type=password]:visible").count():
            return "account_wall"
        if p.locator('iframe[src*="recaptcha"][src*="bframe"]:visible, iframe[src*="hcaptcha"]:visible').count():
            return "captcha"
        if not p.locator("#application-form, form#application_form, form[action*='applications'], "
                         "#first_name").count():
            return "form_not_found"
        return None

    def guard(self) -> None:
        self.page.evaluate(GUARD_JS)

    def release(self) -> None:
        self.page.evaluate(RELEASE_JS)

    def blocked_count(self) -> int:
        return int(self.page.evaluate("() => window.__jobpilotBlocked || 0"))

    # ------------------------------------------------------------ helpers

    def _by_id(self, field_id: str):
        return self.page.locator(f'[id="{field_id}"]')

    def _locate(self, f: dict):
        sel = f.get("selector")
        if sel:
            return self.page.locator(sel)
        fid = f.get("field_id", "")
        loc = self._by_id(fid)
        # Lever fields have names, not ids.
        return loc if loc.count() else self.page.locator(f'[name="{fid}"]')

    def _group(self, kind: str, name: str):
        """Radio buttons or checkboxes sharing one name."""
        return self.page.locator(f'input[type={kind}][name="{name}"]')

    def _choice_text(self, box) -> str:
        bid = box.get_attribute("id") or ""
        if bid:
            lab = self.page.locator(f'label[for="{bid}"]')
            if lab.count():
                return lab.first.inner_text().strip()
        return (box.get_attribute("value") or "").strip()

    def _mark(self, loc, color: str, tip: str) -> None:
        try:
            loc.first.evaluate("(el, a) => { el.style.outline = `3px solid ${a[0]}`; el.style.outlineOffset = '2px';"
                               " el.title = a[1]; }", [color, tip])
        except Exception:
            pass

    def _pick_option(self, loc, text: str, exact: bool = True) -> bool:
        """Open a combobox, type to filter, click the matching option. No Enter key."""
        loc.click()
        loc.fill(text)
        opts = self.page.locator('[role="option"]')
        try:
            opts.first.wait_for(state="visible", timeout=self.timeout)
        except Exception:
            loc.press("Escape")
            return False
        want = text.strip().lower()
        texts = [t.strip() for t in opts.all_inner_texts()]
        if exact:
            idx = [i for i, t in enumerate(texts) if t.lower() == want]
        else:
            idx = [i for i, t in enumerate(texts) if t.lower() == want] or \
                  [i for i, t in enumerate(texts) if t.lower().startswith(want)]
        if len(idx) != 1:  # none, or several that fit: don't guess, leave it for you
            loc.press("Escape")
            return False
        opts.nth(idx[0]).click()
        return True

    # ------------------------------------------------------------ per field

    def handle(self, f: dict, resume_path: str | None) -> Step:
        step = Step(f["key"], f["label"], f.get("kind", "text"), "left_for_you")
        what, val = decide(f, resume_path)
        fid, kind = f.get("field_id", ""), f.get("kind", "text")
        if not fid:
            step.note = "not a question on this form"
            return step
        if kind in ("multi_select", "radio") and self._group("checkbox" if kind == "multi_select" else "radio", fid).count():
            loc = self._group("checkbox" if kind == "multi_select" else "radio", fid).first
        else:
            loc = self._locate(f)
        if what != "fill":  # left for you whether or not the field is showing yet
            step.note = val
            if loc.count():
                self._mark(loc, GREY if "optional" in val else ORANGE, f"JobPilot: {val}")
            return step
        if loc.count() == 0:
            step.action, step.note = "not_found", f"no element #{fid} on the page"
            return step
        step.value = val if kind != "file" else val.replace("\\", "/").rsplit("/", 1)[-1]
        if self.dry_run:
            step.action = "would_fill"
            self._mark(loc, BLUE, f"JobPilot would fill: {step.value}")
            return step
        try:
            ok = self._fill(loc, f, kind, fid, val)
        except Exception as exc:  # leave that one for you rather than stopping the whole form
            ok, step.note = False, f"couldn't fill it: {str(exc).splitlines()[0][:120]}"
        if ok:
            step.action = "filled"
            self._mark(loc, BLUE, "JobPilot filled this; check it")
        else:
            step.action = "failed"
            step.note = step.note or "couldn't find a matching option; fill this yourself"
            self._mark(loc, ORANGE, f"JobPilot: {step.note}")
        return step

    def _fill(self, loc, f: dict, kind: str, fid: str, val: str) -> bool:
        if kind == "file":
            loc.first.set_input_files(val)
            return True
        if kind in ("text", "textarea"):
            loc.first.fill(val)
            return True
        if kind == "select":
            return self._pick_option(loc, val, exact=True)
        if kind in ("location", "search_select"):
            return self._pick_option(loc, val, exact=False)
        if kind == "radio":
            radios = self._group("radio", fid)
            for i in range(radios.count()):
                if self._choice_text(radios.nth(i)).lower() == val.strip().lower():
                    radios.nth(i).check()
                    return True
            return False
        if kind == "multi_select":
            boxes = self._group("checkbox", fid)
            if boxes.count():
                wanted = {v.strip().lower() for v in val.split("; ") if v.strip()}
                hit = 0
                for i in range(boxes.count()):
                    if self._choice_text(boxes.nth(i)).lower() in wanted:
                        boxes.nth(i).check()
                        hit += 1
                return hit == len(wanted)
            return all(self._pick_option(loc, v, exact=True) for v in val.split("; ") if v.strip())
        return False

    def run(self, fields: list[dict], resume_path: str | None) -> FillResult:
        problem = self.check_page()
        if problem:
            return FillResult(problem, message={
                "account_wall": "The site wants you to sign in. JobPilot doesn't sign in or create accounts.",
                "captcha": "There's a CAPTCHA. JobPilot doesn't solve those; you can finish this one by hand.",
                "form_not_found": "Couldn't find the application form on the page (the posting may be closed).",
            }[problem])
        self.guard()
        steps = [self.handle(f, resume_path) for f in fields]
        return FillResult("dry_run_done" if self.dry_run else "stopped_before_submit", steps)
