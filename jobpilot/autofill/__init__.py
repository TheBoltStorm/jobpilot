"""Autofill runner: gate checks, the browser, and the run log.

The gate, again, in code:
- Fill mode only runs for jobs you approved, and only uses the snapshot of what you approved.
- Dry run types nothing. It opens the form, outlines each field and reports what it would do.
- Neither mode submits. Fill mode stops with the form open so you can check it and click Submit.
- A network guard aborts anything that looks like an application submission while JobPilot works.
- The browser uses its own profile under your data folder. You sign in to sites there yourself
  if you ever need to; JobPilot never types passwords or creates accounts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from jobpilot.autofill.greenhouse import SUBMIT_URL, FillResult, GreenhouseFiller
from jobpilot.config import Paths
from jobpilot.core import db
from jobpilot.core.db import now_iso
from jobpilot.forms import load_form
from jobpilot.review.plan import load_application, save_application
from jobpilot.review.status import PRE_APPROVAL, GateError, log_event, transition

MODES = ("dry_run", "fill")
FILLERS = {"greenhouse": GreenhouseFiller}


def default_channel() -> str:
    return "msedge" if sys.platform == "win32" else "chrome"


def plan_run(conn, job_id: int, mode: str, profile=None) -> tuple[dict, str, list[dict], str | None]:
    """Check the gate and return (job, form_url, fields, resume_path).

    A dry run before approval refreshes the plan from your profile first (like opening its review page).
    Fill mode never does: it uses exactly what you approved.
    """
    if mode not in MODES:
        raise GateError(f"unknown mode {mode!r}")
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job and mode == "dry_run" and profile is not None and job["status"] in PRE_APPROVAL:
        from jobpilot.review.plan import prepare

        with conn:
            prepare(conn, job_id, profile)
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise GateError(f"no job {job_id}")
    if job["ats"] not in FILLERS:
        raise GateError(f"autofill doesn't handle {job['ats'] or 'this site'} yet; apply on the site yourself")
    form = load_form(conn, job_id)
    if not form:
        raise GateError(f"load the real form first: `jobpilot forms fetch {job_id}` (or the button on its review page)")
    app = load_application(conn, job_id)
    status = job["status"]
    if mode == "fill":
        if status not in ("approved", "filled") or not (app and app.approved_snapshot):
            raise GateError("fill mode only runs for jobs you approved. Review and approve it in the dashboard first.")
        snap = json.loads(app.approved_snapshot)
        fields, label = snap["fields"], snap["resume"]
    else:
        if status in ("approved", "filled") and app and app.approved_snapshot:
            snap = json.loads(app.approved_snapshot)
            fields, label = snap["fields"], snap["resume"]
        elif status in PRE_APPROVAL and app:
            fields, label = [json.loads(json.dumps(f.__dict__)) for f in app.fields], app.resume_label
        else:
            raise GateError("open the job's review page once (or run `jobpilot prepare`) before a dry run")
    if not any(f.get("field_id") for f in fields):
        raise GateError("the plan doesn't use the real form's questions yet. Open the review page, check the "
                        "answers" + (" and approve again." if mode == "fill" else "."))
    resume = db.get_resume(conn, label) if label else None
    resume_path = resume["path"] if resume and Path(resume["path"]).is_file() else None
    return dict(job), form[0], fields, resume_path


def record(conn, job_id: int, mode: str, started: str, result: FillResult) -> int:
    cur = conn.execute(
        """INSERT INTO autofill_runs (job_id, mode, started_at, finished_at, outcome, report_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (job_id, mode, started, now_iso(), result.outcome, json.dumps(result.to_json())))
    note = (f"{result.count('filled')} filled, {result.count('failed')} failed, "
            f"{result.count('left_for_you')} left for you" if mode == "fill"
            else f"{result.count('would_fill')} would be filled, {result.count('left_for_you')} left for you")
    log_event(conn, job_id, "system", f"autofill {mode.replace('_', ' ')}: {result.outcome.replace('_', ' ')}",
              note=note if result.steps else result.message)
    job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if mode == "fill" and result.outcome == "stopped_before_submit" and job["status"] == "approved":
        transition(conn, job_id, "filled", actor="system", action="form filled",
                   note="left open on the last page for you to submit")
        conn.execute("UPDATE applications SET filled_at = ? WHERE job_id = ?", (now_iso(), job_id))
    elif result.outcome == "account_wall" and job["status"] in ("approved", *PRE_APPROVAL):
        app = load_application(conn, job_id)
        if app and app.approved_at:
            app.approved_at, app.approved_snapshot = None, None
            save_application(conn, app)
        transition(conn, job_id, "account_wall", actor="system", action="hit a sign-in page")
    return cur.lastrowid


def run_autofill(paths: Paths, job_id: int, mode: str = "dry_run", *, profile=None, channel: str | None = None,
                 headless: bool = False, wait_for_close: bool = True, slow_mo_ms: int = 0,
                 setup_context: Callable | None = None, log: Callable[[str], None] = print) -> FillResult:
    with db.connect(paths.db) as conn:
        job, form_url, fields, resume_path = plan_run(conn, job_id, mode, profile)
    if mode == "fill" and headless:
        raise GateError("fill mode needs a visible browser so you can check the form and submit it yourself")

    from playwright.sync_api import sync_playwright

    started = now_iso()
    blocked: list[str] = []
    guard_on = {"on": True}

    def network_guard(route, request):
        if guard_on["on"] and request.method != "GET" and SUBMIT_URL.search(request.url):
            blocked.append(request.url)
            return route.abort()
        return route.fallback()

    result = FillResult("error")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(paths.home / "browser_profile"), channel=channel or default_channel(), headless=headless,
            no_viewport=not headless, slow_mo=slow_mo_ms)
        try:
            if setup_context:
                setup_context(ctx)
            ctx.route("**/*", network_guard)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            log(f"Opening {form_url}")
            page.goto(form_url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("#first_name, input[type=password], #application-form", timeout=20000)
            except Exception:
                pass
            filler = FILLERS[job["ats"]](page, dry_run=(mode == "dry_run"))
            result = filler.run(fields, resume_path)
            if blocked:
                result.message = (result.message + f" Blocked {len(blocked)} submit request(s).").strip()
            with db.connect(paths.db) as conn:
                with conn:
                    record(conn, job_id, mode, started, result)
            if mode == "fill" and result.outcome == "stopped_before_submit":
                guard_on["on"] = False
                filler.release()  # the form is yours now
            _summary(result, mode, log)
            if wait_for_close and not headless:
                log("The browser stays open. Close its window when you're done.")
                ctx.wait_for_event("close", timeout=0)
        except Exception as exc:
            result = FillResult("error", result.steps, message=str(exc).splitlines()[0])
            with db.connect(paths.db) as conn:
                with conn:
                    record(conn, job_id, mode, started, result)
            raise
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return result


def _summary(result: FillResult, mode: str, log) -> None:
    if result.message:
        log(result.message)
    if not result.steps:
        return
    icons = {"would_fill": "~", "filled": "✓", "left_for_you": "·", "not_found": "?", "failed": "!"}
    for s in result.steps:
        line = f"  {icons.get(s.action, ' ')} {s.label[:60]:<60} "
        line += (s.value[:40] if s.action in ("would_fill", "filled") else s.note[:60])
        log(line)
    if mode == "dry_run":
        log(f"\nDry run: {result.count('would_fill')} fields would be filled, "
            f"{result.count('left_for_you')} left for you. Nothing was typed.")
    else:
        log(f"\nFilled {result.count('filled')} fields; {result.count('failed')} need fixing and "
            f"{result.count('left_for_you')} are left for you (outlined in orange).")
        log("JobPilot did NOT submit. Check every field, then click Submit yourself if you're happy.")
        log("Afterwards, mark it submitted on its review page.")
