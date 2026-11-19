"""Fill an application form on any site (Ashby, Workday, SmartRecruiters, Workable, company sites...).

It uses the same form reader as the browser button (extension/page.js) and the same plan
(review/pagefill.py): values only from your profile, your remembered answers, and what you
answered on this job's page. Protected questions only get your answer for this one job;
legal/consent questions, files and single checkboxes are left to you.

Multi-page forms: while the window is open, each new page of the form is filled when it
appears. Fields that already have something in them (yours or ours) are never overwritten.
Sign-in pages are never filled. Sign in yourself in that window; JobPilot carries on with the
form after you do. It never clicks Next, Submit, or any button.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from jobpilot.autofill.greenhouse import GUARD_JS, RELEASE_JS, FillResult, Step

PAGE_JS = Path(__file__).resolve().parents[2] / "extension" / "page.js"

# Visible form fields the reader hasn't seen yet (a new page, or a section that just opened).
NEW_FIELDS_JS = """
() => [...document.querySelectorAll('input, select, textarea')].filter((el) => {
  const t = (el.getAttribute('type') || '').toLowerCase();
  if (['hidden', 'submit', 'button', 'reset', 'image', 'password'].includes(t) || el.disabled) return false;
  if (el.dataset.jobpilot) return false;
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}).length
"""

# Everything visible is "seen" after a pass, so only fields that appear later count as new.
MARK_SEEN_JS = """
() => document.querySelectorAll('input, select, textarea').forEach((el) => {
  if (!el.dataset.jobpilot) el.dataset.jobpilot = 'seen';
})
"""

SIGN_IN = ("This is a sign-in page. Sign in (or create your account) yourself in this window. "
           "JobPilot fills the form once it appears.")

Planner = Callable[[str, str, list[dict]], dict]


def _reader_source() -> str:
    return "() => { " + PAGE_JS.read_text(encoding="utf-8") + "\n; return !!window.__jobpilot; }"


class GenericFiller:
    def __init__(self, planner: Planner, dry_run: bool = False):
        self.planner = planner
        self.dry_run = dry_run
        self.src = _reader_source()

    def _frames(self, page):
        return [f for f in page.frames if not f.is_detached()]

    def fill_page(self, page) -> FillResult:
        """Read every frame on the page, plan, fill what may be filled. Never clicks a button."""
        steps: list[Step] = []
        signed_out = False
        for frame in self._frames(page):
            try:
                frame.evaluate(self.src)
                got = frame.evaluate("() => window.__jobpilot.scan()")
                frame.evaluate(MARK_SEEN_JS)
            except Exception:
                continue  # a frame from a site that went away, or one we can't read
            if got.get("hasPassword"):
                signed_out = True
                continue
            if not got.get("fields"):
                continue
            plan = self.planner(got.get("url", ""), got.get("title", ""), got["fields"])
            if plan.get("refused"):
                signed_out = True
                continue
            todo = [f for f in plan["fields"] if not f.get("present")]
            answers = [{"key": f["key"], "value": f["value"], "kind": f["kind"]} for f in todo if f["fill"]]
            left = [f["key"] for f in todo if not f["fill"] and not f["optional"]]
            failed: set[str] = set()
            if not self.dry_run and (answers or left):
                frame.evaluate(GUARD_JS)
                try:
                    res = frame.evaluate("([a, l]) => window.__jobpilot.fill(a, l)", [answers, left])
                    failed = set(res.get("failed", []))
                finally:
                    frame.evaluate(RELEASE_JS)
            for f in todo:
                if f["fill"]:
                    action = "would_fill" if self.dry_run else ("failed" if f["key"] in failed else "filled")
                    steps.append(Step(f["key"], f["label"], f["kind"], action, f["value"], f["source"]))
                elif not f["optional"] or f["value"]:
                    steps.append(Step(f["key"], f["label"], f["kind"], "left_for_you", "", f["source"]))
        if not steps:
            if signed_out:
                return FillResult("sign_in", message=SIGN_IN)
            return FillResult("waiting_for_form", message="No form on this page yet. Click Apply on the page; "
                                                          "JobPilot fills each form page as it appears.")
        filled = sum(1 for s in steps if s.action in ("filled", "would_fill"))
        outcome = "dry_run_done" if self.dry_run else ("stopped_before_submit" if filled else "left_for_you")
        return FillResult(outcome, steps)

    def has_new_fields(self, page) -> bool:
        for frame in self._frames(page):
            try:
                if frame.evaluate(NEW_FIELDS_JS):
                    return True
            except Exception:
                continue
        return False

    def watch(self, ctx, on_page: Callable[[FillResult], None], every_ms: int = 1500,
              stop: Callable[[], bool] = lambda: False) -> None:
        """Until you close the window: fill each new form page that appears."""
        while not stop():
            pages = [p for p in ctx.pages if not p.is_closed()]
            if not pages:
                return
            try:
                pages[0].wait_for_timeout(every_ms)
                for p in pages:
                    if not p.is_closed() and self.has_new_fields(p):
                        on_page(self.fill_page(p))
            except Exception:
                if not [p for p in ctx.pages if not p.is_closed()]:
                    return
