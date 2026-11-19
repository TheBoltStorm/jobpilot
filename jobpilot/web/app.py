"""Local review dashboard (FastAPI + server-rendered HTML).

Runs on 127.0.0.1 only. Every POST needs a token that's created when the dashboard
starts, and requests from other websites are refused, so a web page you visit
can't press Approve for you.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from jobpilot.config import Paths
from jobpilot.forms import FORM_ATS
from jobpilot.core import db
from jobpilot.onboarding.profile import load_profile
from jobpilot.matching.skills import load_taxonomy
from jobpilot.review import export, outcomes, pagefill, plan, tailor
from jobpilot.review.status import LABELS, PRE_APPROVAL, STATUSES, GateError
from jobpilot.settings import Settings, load_settings, save_settings

HERE = Path(__file__).parent
LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}
PAGE_SIZE = 50

MESSAGES = {
    "saved": "Saved.",
    "approved": "Approved. Autofill will fill this form and stop before Submit. You submit it yourself.",
    "rejected": "Rejected. You can reopen it from its page.",
    "skipped": "Skipped. It's at the end of your queue now.",
    "submitted": "Marked as submitted. Good luck!",
    "reopened": "Reopened.",
    "prepared": "Prepared every matched job.",
    "settings": "Settings saved. They apply to the next discover / match run.",
    "queue_empty": "Nothing left to review. Run discover and match for more.",
    "form_loaded": "Loaded the real form's questions. Check the answers below.",
    "outcome": "Updated.",
    "followup_sent": "Recorded your follow-up. JobPilot didn't send anything; it just noted the date.",
    "tailored": "Suggested a tailored order. Check the changes under Tailored resume.",
    "tailor_approved": "Created the tailored resume and picked it for this application.",
    "tailor_discarded": "Discarded the tailored version.",
    "added": "Added. Check the answers below, then approve when they look right.",
    "already": "You already had this job. Here it is.",
    "filling": ("A browser window is opening with the form. JobPilot fills what you approved and stops on the "
                "last page. You check it and click Submit yourself."),
}


def create_app(paths: Paths) -> FastAPI:
    app = FastAPI(title="JobPilot", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    token = secrets.token_urlsafe(32)
    app.state.csrf_token = token
    app.state.filling = set()  # job ids with a fill running in a browser window
    app.state.fill_errors = {}  # job id -> last error from a dashboard-started fill
    t_env = templates.env
    t_env.globals.update(STATUSES=STATUSES, LABELS=LABELS, csrf=token)
    t_env.filters["label"] = lambda s: LABELS.get(s, s)
    t_env.filters["day"] = lambda s: (s or "")[:10]

    def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("msg", MESSAGES.get(request.query_params.get("msg", ""), ""))
        ctx.setdefault("error", request.query_params.get("err", "")[:300])
        ctx.setdefault("profile_missing", not paths.profile.is_file())
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    async def posted(request: Request):
        """Read a POSTed form after checking it really came from this dashboard."""
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
            raise HTTPException(403, "Request came from another website.")
        if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            raise HTTPException(403, "Request came from another website.")
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf", "")), token):
            raise HTTPException(403, "Missing or stale form token. Reload the page and try again.")
        return form

    def go(url: str, msg: str = "") -> RedirectResponse:
        sep = "&" if "?" in url else "?"
        return RedirectResponse(url + (f"{sep}msg={msg}" if msg else ""), status_code=303)

    def profile_or_400():
        profile = load_profile(paths.profile)
        if profile is None:
            raise HTTPException(400, "No profile yet. Run `jobpilot onboard` first.")
        return profile

    def settings() -> Settings:
        return load_settings(paths.settings)

    # ------------------------------------------------------------ shared

    FIELDS = ["software", "data", "product", "hardware", "quant", "security", "design"]

    def refresh_indexes(conn, profile=None) -> None:
        """Keep the place index and minimum-requirement checks current (cheap when nothing changed)."""
        from jobpilot.core import places
        from jobpilot.matching import minimums

        places.refresh(conn)
        minimums.refresh(conn, profile if profile is not None else load_profile(paths.profile))

    def job_where(f: dict) -> tuple[str, list]:
        """WHERE clause shared by the Jobs and You qualify pages (jobs j, matches m)."""
        from jobpilot.core import places

        where, params = ["1=1"], []
        if f.get("status"):
            where.append("j.status = ?")
            params.append(f["status"])
        else:
            where.append("j.status NOT IN ('posting_closed', 'rejected')")
        if f.get("q"):
            where.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
            params += [f"%{f['q']}%"] * 3
        if f.get("field"):
            where.append("(',' || COALESCE(m.tags, '') || ',') LIKE ?")
            params.append(f"%,{f['field']},%")
        if f.get("type"):
            where.append("j.role_type = ?")
            params.append(f["type"])
        if str(f.get("min_score", "")).strip().isdigit():
            where.append("m.score >= ?")
            params.append(int(f["min_score"]))
        if f.get("described"):
            where.append("j.description != ''")
        place_sql, place_params = places.sql_filter(f.get("region", ""), f.get("area", ""))
        if place_sql:
            where.append(place_sql)
            params += place_params
        return " AND ".join(where), params

    SORTS = {"score": "m.score IS NULL, m.score DESC, j.date_posted DESC",
             "date": "j.date_posted DESC, j.id DESC", "company": "j.company COLLATE NOCASE, j.title"}

    def list_page(request, template: str, base_url: str, f: dict, extra_where: str = "", page: int = 1, **ctx):
        from jobpilot.core import places

        sql_where, params = job_where(f)
        if extra_where:
            sql_where += " AND " + extra_where
        order = SORTS.get(f.get("sort", ""), SORTS["score"])
        page = max(1, page)
        with db.connect(paths.db) as conn:
            refresh_indexes(conn)
            base = "FROM jobs j LEFT JOIN matches m ON m.job_id = j.id LEFT JOIN qualifications q ON q.job_id = j.id"
            total = conn.execute(f"SELECT COUNT(*) {base} WHERE {sql_where}", params).fetchone()[0]
            rows = conn.execute(
                f"""SELECT j.id, j.title, j.company, j.location, j.remote, j.role_type, j.status, j.date_posted, j.ats,
                           j.description != '' AS described, m.score, m.confidence, m.reason, m.tags,
                           q.status AS qual, q.detail_json
                    {base} WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?""",
                (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
            areas = {r["job_id"]: r["areas"] for r in conn.execute(
                f"""SELECT job_id, GROUP_CONCAT(area, ' · ') areas FROM job_places
                    WHERE job_id IN ({','.join(str(r['id']) for r in rows) or '0'}) GROUP BY job_id""")}
            types = [r[0] for r in conn.execute("SELECT DISTINCT role_type FROM jobs WHERE role_type != '' ORDER BY 1")]
            # Place counts follow every other filter, so the chips say what you'd get.
            nf = dict(f, region="", area="")
            facet_where, facet_params = job_where(nf)
            if extra_where:
                facet_where += " AND " + extra_where
            facets = places.facets(conn, facet_where, facet_params)
            qual_counts = dict(conn.execute(
                """SELECT q.status, COUNT(*) FROM qualifications q JOIN jobs j ON j.id = q.job_id
                   WHERE j.status NOT IN ('posting_closed', 'rejected') GROUP BY q.status""").fetchall())
        pages = max(1, math.ceil(total / PAGE_SIZE))

        def page_url(n: int) -> str:
            return base_url + "?" + urlencode({**{k: v for k, v in f.items() if v}, "page": n})

        def checks(row) -> list[dict]:
            return json.loads(row["detail_json"])["checks"] if row["detail_json"] else []

        return render(request, template, rows=rows, total=total, page=page, pages=pages, page_url=page_url,
                      filters=f, types=types, fields=FIELDS, facets=facets, areas=areas, checks=checks,
                      qual_counts=qual_counts, **ctx)

    # ------------------------------------------------------------ board

    @app.get("/", response_class=HTMLResponse)
    def board(request: Request):
        with db.connect(paths.db) as conn:
            refresh_indexes(conn)
            counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
            queue = plan.queue(conn, 8)
            recent = conn.execute("""SELECT e.*, j.title, j.company FROM application_events e
                                     JOIN jobs j ON j.id = e.job_id
                                     WHERE NOT (e.actor = 'system' AND e.action = 'prepared')
                                     ORDER BY e.id DESC LIMIT 8""").fetchall()
            undescribed = conn.execute("SELECT COUNT(*) FROM jobs WHERE description = '' AND status IN ('found','matched')"
                                       ).fetchone()[0]
            qualify = conn.execute(
                """SELECT j.id, j.title, j.company, m.score FROM qualifications q JOIN jobs j ON j.id = q.job_id
                   LEFT JOIN matches m ON m.job_id = j.id
                   WHERE q.status = 'qualify' AND j.status IN ('found','matched','ready_to_fill','needs_human')
                   ORDER BY m.score DESC LIMIT 8""").fetchall()
            qual_counts = dict(conn.execute(
                """SELECT q.status, COUNT(*) FROM qualifications q JOIN jobs j ON j.id = q.job_id
                   WHERE j.status IN ('found','matched','ready_to_fill','needs_human') GROUP BY q.status""").fetchall())
            with conn:
                outcomes.ensure(conn)
            due = outcomes.due(conn, outcomes.FollowupRules.from_settings(settings()))
            responded = conn.execute("SELECT COUNT(*) FROM outcomes WHERE responded_at IS NOT NULL").fetchone()[0]
            to_submit = conn.execute(
                """SELECT j.id, j.title, j.company, j.status, j.ats FROM jobs j
                   WHERE j.status IN ('approved', 'filled') ORDER BY j.id DESC LIMIT 8""").fetchall()
        return render(request, "board.html", due=due[:5], due_n=len(due), responded=responded, counts=counts,
                      total=sum(counts.values()), queue=queue, recent=recent, undescribed=undescribed,
                      qualify=qualify, qual_counts=qual_counts, to_submit=to_submit,
                      review_n=sum(counts.get(s, 0) for s in plan.QUEUE_STATUSES))

    @app.post("/prepare")
    async def prepare_all(request: Request):
        await posted(request)
        profile = profile_or_400()
        with db.connect(paths.db) as conn:
            plan.prepare_matched(conn, profile)
        return go("/", "prepared")

    # ------------------------------------------------------------ after you apply

    def outcome_action(conn, job_id: int, form) -> str:
        """Apply one outcome form action. Returns the message key."""
        action = str(form.get("action", ""))
        with conn:
            if action == "outcome":
                outcomes.set_stage(conn, job_id, str(form.get("stage", "")), str(form.get("note", "")).strip(),
                                   str(form.get("on", "")).strip() or None)
                if "next_at" in form:
                    outcomes.set_next(conn, job_id, str(form.get("next_at", "")).strip() or None,
                                      str(form.get("next_note", "")))
                return "outcome"
            if action == "followup_sent":
                outcomes.followup_sent(conn, job_id)
                return "followup_sent"
            if action in ("mute", "unmute"):
                outcomes.mute(conn, job_id, action == "mute")
                return "saved"
        raise GateError(f"Unknown action {action!r}.")

    @app.get("/applied", response_class=HTMLResponse)
    def applied(request: Request, stage: str = ""):
        profile = load_profile(paths.profile)
        rules = outcomes.FollowupRules.from_settings(settings())
        with db.connect(paths.db) as conn:
            with conn:
                outcomes.ensure(conn)
            due = outcomes.due(conn, rules)
            drafts = {d.job_id: outcomes.draft(d.kind, d.title, d.company, d.submitted, profile) for d in due}
            soon = outcomes.upcoming(conn)
            where, params = ("WHERE o.stage = ?", [stage]) if stage in outcomes.BY_KEY else ("", [])
            rows = conn.execute(
                f"""SELECT o.*, j.title, j.company, a.submitted_at, a.resume_label,
                           (SELECT GROUP_CONCAT(area, ' · ') FROM job_places p WHERE p.job_id = j.id) areas
                    FROM outcomes o JOIN jobs j ON j.id = o.job_id LEFT JOIN applications a ON a.job_id = j.id
                    {where} ORDER BY a.submitted_at DESC""", params).fetchall()
            st = outcomes.stats(conn)
        return render(request, "applied.html", rows=rows, due=due, drafts=drafts, soon=soon, stats=st,
                      stage=stage, STAGES=outcomes.STAGES, BY_KEY=outcomes.BY_KEY, rules=rules)

    @app.post("/applied/{job_id}")
    async def applied_action(request: Request, job_id: int):
        form = await posted(request)
        back = str(form.get("back", "/applied"))
        back = back if back.startswith("/") and not back.startswith("//") else "/applied"
        with db.connect(paths.db) as conn:
            try:
                return go(back, outcome_action(conn, job_id, form))
            except GateError as exc:
                return go(back + ("&" if "?" in back else "?") + urlencode({"err": str(exc)}))

    # ------------------------------------------------------------ jobs list

    OPEN_TO_APPLY = "j.status IN ('found', 'matched', 'ready_to_fill', 'needs_human', 'custom_site', 'account_wall')"

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, show: str = "", status: str = "", q: str = "", field: str = "", type: str = "",
             min_score: str = "", described: str = "", region: str = "", area: str = "", sort: str = "score",
             page: int = 1):
        if show not in ("qualify", "close", "all"):
            # First visit: start on the jobs you qualify for, if there are any.
            with db.connect(paths.db) as conn:
                refresh_indexes(conn)
                n = conn.execute(f"""SELECT COUNT(*) FROM qualifications q JOIN jobs j ON j.id = q.job_id
                                     WHERE q.status = 'qualify' AND {OPEN_TO_APPLY}""").fetchone()[0]
            show = "qualify" if n and not (status or q or field or type or min_score or region or area) else "all"
        f = {"show": show, "status": status if show == "all" else "", "q": q, "field": field, "type": type,
             "min_score": min_score, "described": described, "region": region, "area": area,
             "sort": sort if sort in SORTS else "score"}
        # `show` is one of two fixed words here (checked above), so it's safe in the SQL text.
        extra = f"q.status = '{show}' AND {OPEN_TO_APPLY}" if show in ("qualify", "close") else ""
        return list_page(request, "jobs.html", "/jobs", f, extra_where=extra, page=page)

    @app.get("/qualify")
    def qualify_page(request: Request):
        qs = dict(request.query_params)
        qs["show"] = qs.get("show") if qs.get("show") in ("qualify", "close") else "qualify"
        return RedirectResponse("/jobs?" + urlencode(qs), status_code=303)

    # ------------------------------------------------------------ apply: everything waiting on you

    @app.get("/apply", response_class=HTMLResponse)
    def apply_page(request: Request):
        with db.connect(paths.db) as conn:
            rows = conn.execute(
                f"""SELECT j.id, j.title, j.company, j.status, j.ats, m.score, a.skipped_at FROM jobs j
                    LEFT JOIN matches m ON m.job_id = j.id LEFT JOIN applications a ON a.job_id = j.id
                    WHERE j.status IN ({','.join('?' * len(plan.QUEUE_STATUSES))}, 'approved', 'filled')
                    ORDER BY a.skipped_at IS NOT NULL, m.score DESC, j.id""", plan.QUEUE_STATUSES).fetchall()
        groups = [
            ("Fill and submit", "You approved these. Fill the form, check it, and click Submit on the site yourself.",
             [r for r in rows if r["status"] in ("approved", "filled")]),
            ("Answer a few questions", "Some questions only you can answer.",
             [r for r in rows if r["status"] in ("needs_human", "account_wall")]),
            ("Ready for your OK", "JobPilot has an answer for every question. Check them and approve.",
             [r for r in rows if r["status"] in ("ready_to_fill", "matched", "custom_site")]),
        ]
        return render(request, "apply.html", groups=groups, total=len(rows))

    # ------------------------------------------------------------ add a job from a link

    @app.get("/add", response_class=HTMLResponse)
    def add_page(request: Request, url: str = ""):
        return render(request, "add.html", f={"url": url}, manual=False)

    def _http():
        from jobpilot.core.http import PoliteClient

        s = settings()
        return PoliteClient(s.user_agent, s.min_seconds_between_requests, s.request_timeout_seconds)

    def _gh_tokens() -> dict[str, str]:
        from jobpilot.sources.companies import load_companies
        from jobpilot.sources.dedupe import company_key

        return {company_key(c.name): c.token for c in load_companies(paths.companies) if c.ats == "greenhouse"}

    def add_job(conn, url: str, **typed) -> tuple[int, bool]:
        """Store a job from a link, score it, plan its answers and load the real form when we can."""
        from jobpilot.matching.engine import score_one
        from jobpilot.sources.link import add_link

        http = _http()
        try:
            with conn:
                job_id, new = add_link(conn, http, url, gh_tokens=_gh_tokens(), **typed)
            profile = load_profile(paths.profile)
            if new and profile is not None:
                with conn:
                    score_one(conn, job_id, profile, load_taxonomy(paths.home / "skills.yaml"),
                              settings().matching.min_score)
                    plan.prepare(conn, job_id, profile)
                job = conn.execute("SELECT ats FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if job["ats"] in FORM_ATS:
                    try:
                        with conn:
                            plan.load_real_form(conn, job_id, profile, http, _gh_tokens())
                    except GateError:
                        pass  # the standard fields still work; the job page offers to retry
        finally:
            http.close()
        return job_id, new

    @app.post("/add")
    async def add_post(request: Request):
        from jobpilot.sources.link import LinkError

        form = await posted(request)
        f = {k: str(form.get(k, "")).strip() for k in ("url", "title", "company", "location")}
        with db.connect(paths.db) as conn:
            try:
                job_id, new = add_job(conn, f["url"], title=f["title"], company=f["company"], location=f["location"])
            except LinkError as exc:
                return render(request, "add.html", status_code=400, f=f, error=str(exc),
                              manual="title and company" in str(exc).lower() or bool(f["title"]))
        return go(f"/jobs/{job_id}", "added" if new else "already")

    # ------------------------------------------------------------ browser button (extension) API
    #
    # The browser button calls these with its pairing key. Websites can't: they don't have the
    # key, and the dashboard sends no CORS headers, so a page can't read anything back either.

    def ext_check(request: Request) -> None:
        origin = request.headers.get("origin", "")
        if origin.startswith(("http://", "https://")) and urlparse(origin).hostname not in LOCAL_HOSTS:
            raise HTTPException(403, "Request came from a website.")
        key = request.headers.get("x-jobpilot-key", "")
        if not secrets.compare_digest(key, pagefill.extension_key(paths.home)):
            raise HTTPException(401, "Wrong key. Copy it again from JobPilot → Settings → Browser button.")

    async def ext_json(request: Request) -> dict:
        ext_check(request)
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "Expected JSON.") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected a JSON object.")
        return data

    @app.get("/api/ext/hello")
    def ext_hello(request: Request):
        ext_check(request)
        return {"ok": True, "profile": paths.profile.is_file()}

    @app.post("/api/ext/plan")
    async def ext_plan(request: Request):
        data = await ext_json(request)
        profile = load_profile(paths.profile)
        if profile is None:
            return JSONResponse({"refused": "No profile yet. Run `jobpilot onboard` first.", "fields": [], "job": None})
        fields = [f for f in data.get("fields") or [] if isinstance(f, dict)]
        with db.connect(paths.db) as conn:
            return pagefill.plan_page(conn, profile, str(data.get("url", ""))[:2000], str(data.get("title", "")), fields)

    @app.post("/api/ext/job")
    async def ext_job(request: Request):
        from jobpilot.sources.link import LinkError

        data = await ext_json(request)
        with db.connect(paths.db) as conn:
            try:
                job_id, new = add_job(conn, str(data.get("url", "")), title=str(data.get("title", ""))[:300],
                                      company=str(data.get("company", ""))[:200],
                                      location=str(data.get("location", ""))[:200],
                                      description=str(data.get("description", ""))[:60000])
            except LinkError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        return {"job_id": job_id, "new": new, "path": f"/jobs/{job_id}"}

    @app.post("/api/ext/filled")
    async def ext_filled(request: Request):
        data = await ext_json(request)
        with db.connect(paths.db) as conn:
            with conn:
                job_id = pagefill.record_fill(conn, str(data.get("url", ""))[:2000], int(data.get("filled", 0) or 0),
                                              int(data.get("left", 0) or 0))
        return {"job_id": job_id}

    def start_fill(job_id: int) -> None:
        """Open the form in a visible browser and fill it from your approval, in the background."""
        import threading

        from jobpilot.autofill import run_autofill

        if job_id in app.state.filling:
            raise GateError("A browser window is already filling this form.")
        with db.connect(paths.db) as conn:
            from jobpilot.autofill import plan_run

            plan_run(conn, job_id, "fill")  # gate check now, so you see a refusal right away
        app.state.filling.add(job_id)
        app.state.fill_errors.pop(job_id, None)
        s = settings()

        def work():
            try:
                run_autofill(paths, job_id, "fill", profile=load_profile(paths.profile),
                             channel=s.autofill.browser_channel, slow_mo_ms=s.autofill.slow_mo_ms, log=lambda m: None)
            except Exception as exc:  # shown on the job's page
                app.state.fill_errors[job_id] = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
            finally:
                app.state.filling.discard(job_id)

        threading.Thread(target=work, daemon=True, name=f"fill-{job_id}").start()

    # ------------------------------------------------------------ review page

    def job_context(conn, job_id: int, profile):
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "No such job.")
        match = conn.execute("SELECT * FROM matches WHERE job_id = ?", (job_id,)).fetchone()
        skills = json.loads(match["skills_json"])["skills"] if match else []
        order = {"required": 0, "preferred": 1, "mentioned": 2}
        grouped = {st: sorted([s for s in skills if s["status"] == st], key=lambda s: order[s["importance"]])
                   for st in ("missing", "partial", "have")}
        app_ = plan.prepare(conn, job_id, profile) if profile else plan.load_application(conn, job_id)
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()  # prepare may move it
        events = conn.execute("SELECT * FROM application_events WHERE job_id = ? ORDER BY id DESC", (job_id,)).fetchall()
        sources = conn.execute("SELECT source, scope, active, url FROM job_sources WHERE job_id = ?", (job_id,)).fetchall()
        resumes = db.list_resumes(conn)
        nxt = next((r["id"] for r in plan.queue(conn, 2) if r["id"] != job_id), None)
        form_row = conn.execute("SELECT form_url, fetched_at FROM forms WHERE job_id = ?", (job_id,)).fetchone()
        run = conn.execute("SELECT * FROM autofill_runs WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        run_steps = json.loads(run["report_json"]) if run else []
        tailored = tailor.load(conn, job_id)
        from jobpilot.matching import minimums

        refresh_indexes(conn, profile)
        job_areas = [r["area"] for r in conn.execute("SELECT area FROM job_places WHERE job_id = ?", (job_id,))]
        outcome = draft_ = None
        if job["status"] == "submitted":
            outcomes.ensure(conn)
            outcome = outcomes.load(conn, job_id)
            app_row = conn.execute("SELECT submitted_at FROM applications WHERE job_id = ?", (job_id,)).fetchone()
            sub = outcomes._day(app_row["submitted_at"]) if app_row else None
            kind = "thank_you" if outcome and outcome["stage"] in ("interview", "final") else "followup"
            draft_ = outcomes.draft(kind, job["title"], job["company"], sub, profile)
        return dict(outcome=outcome, outcome_draft=draft_, STAGES=outcomes.STAGES, BY_KEY=outcomes.BY_KEY,
                    qual=minimums.load(conn, job_id), job_areas=job_areas, tailored=tailored, tailor_moves=tailor.moves(tailored), job=job, match=match, skills=grouped, app=app_, events=events, sources=sources,
                    resumes=resumes, next_id=nxt, editable=job["status"] in (*PRE_APPROVAL, "approved", "filled"),
                    form=form_row, can_load_form=job["ats"] in FORM_ATS, can_fill=job["ats"] in FORM_ATS, run=run,
                    run_steps=run_steps)

    def load_form_for(conn, job_id: int, profile) -> None:
        from jobpilot.core.http import PoliteClient
        from jobpilot.sources.companies import load_companies
        from jobpilot.sources.dedupe import company_key

        s = settings()
        companies = load_companies(paths.companies)
        tokens = {company_key(c.name): c.token for c in companies if c.ats == "greenhouse"}
        http = PoliteClient(s.user_agent, s.min_seconds_between_requests, s.request_timeout_seconds)
        try:
            plan.load_real_form(conn, job_id, profile, http, tokens)
        finally:
            http.close()

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: int):
        profile = load_profile(paths.profile)
        with db.connect(paths.db) as conn:
            with conn:
                ctx = job_context(conn, job_id, profile)
        return render(request, "job.html", filling=job_id in app.state.filling,
                      fill_error=app.state.fill_errors.get(job_id, ""), **ctx)

    @app.get("/review")
    def review_next():
        with db.connect(paths.db) as conn:
            q = plan.queue(conn, 1)
        return go(f"/jobs/{q[0]['id']}") if q else go("/", "queue_empty")

    def drafts_from(form) -> list[plan.Draft]:
        out = []
        for i in range(int(form.get("draft_count", 0) or 0)):
            if form.get(f"dd_{i}"):
                continue  # delete box ticked
            q, t = str(form.get(f"dq_{i}", "")).strip(), str(form.get(f"dt_{i}", ""))
            if q:
                out.append(plan.Draft(q, t, str(form.get(f"dn_{i}", ""))))
        nq, nt = str(form.get("dq_new", "")).strip(), str(form.get("dt_new", ""))
        if nq:
            out.append(plan.Draft(nq, nt, "you wrote this"))
        return out

    @app.post("/jobs/{job_id}")
    async def job_action(request: Request, job_id: int):
        form = await posted(request)
        action = form.get("action", "save")
        profile = profile_or_400()
        with db.connect(paths.db) as conn:
            try:
                job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if not job:
                    raise HTTPException(404, "No such job.")
                # Your edits are saved first (and kept) even if the action below is refused.
                if action in ("save", "approve", "reject", "skip") and job["status"] in (
                        *PRE_APPROVAL, "approved", "filled"):
                    grouped_vals: dict[str, list[str]] = {}
                    for k, v in form.multi_items():
                        if k.startswith("fv_"):
                            grouped_vals.setdefault(k[3:], []).append(str(v))
                    # Checkbox groups send one value per ticked box (plus an empty marker).
                    values = {k: "; ".join(x for x in vs if x.strip()) if len(vs) > 1 else vs[0]
                              for k, vs in grouped_vals.items()}
                    with conn:
                        plan.save_answers(conn, job_id, profile, values, drafts_from(form), str(form.get("notes", "")),
                                          str(form.get("resume", "")) or None)
                elif action == "save" and "notes" in form:  # submitted: only notes can change
                    with conn:
                        conn.execute("UPDATE applications SET notes = ? WHERE job_id = ?",
                                     (str(form.get("notes", "")), job_id))
                with conn:
                    if action == "load_form":
                        load_form_for(conn, job_id, profile)
                        return go(f"/jobs/{job_id}", "form_loaded")
                    if action == "tailor":
                        tailor.propose(conn, job_id, profile, load_taxonomy(paths.home / "skills.yaml"))
                        return go(f"/jobs/{job_id}", "tailored")
                    if action == "tailor_approve":
                        tailor.approve(conn, job_id, profile, paths.resumes)
                        return go(f"/jobs/{job_id}", "tailor_approved")
                    if action == "tailor_discard":
                        tailor.discard(conn, job_id)
                        return go(f"/jobs/{job_id}", "tailor_discarded")
                    if action == "approve":
                        if form.get("confirm") != "yes":
                            raise GateError("Tick the box to confirm you've checked every field and draft first.")
                        plan.approve(conn, job_id, profile)
                        return go(f"/jobs/{job_id}", "approved")
                    if action == "reject":
                        plan.reject(conn, job_id, str(form.get("reason", "")).strip())
                        return go(f"/jobs/{job_id}", "rejected")
                    if action == "skip":
                        plan.skip(conn, job_id)
                        return go("/review", "skipped")
                    if action == "submitted":
                        if form.get("confirm_submitted") != "yes":
                            raise GateError("Tick the box to confirm you clicked Submit on the site yourself.")
                        plan.mark_submitted(conn, job_id)
                        return go(f"/jobs/{job_id}", "submitted")
                    if action == "fill":
                        start_fill(job_id)
                        return go(f"/jobs/{job_id}", "filling")
                    if action == "reopen":
                        plan.reopen(conn, job_id, profile, settings().matching.min_score)
                        return go(f"/jobs/{job_id}", "reopened")
                    if action != "save":
                        raise GateError(f"Unknown action {action!r}.")
                return go(f"/jobs/{job_id}", "saved")
            except GateError as exc:
                ctx = job_context(conn, job_id, profile)
                conn.commit()
                return render(request, "job.html", status_code=400, error=str(exc), filling=False,
                              fill_error="", **ctx)

    # ------------------------------------------------------------ gaps

    def gap_data(field: str, min_score: str):
        from jobpilot.matching.gaps import gap_report
        from jobpilot.matching.skills import load_taxonomy

        ms = int(min_score) if min_score.strip().isdigit() else settings().matching.min_score
        with db.connect(paths.db) as conn:
            missing, partial, total = gap_report(conn, load_taxonomy(paths.home / "skills.yaml"), ms, field or None)
        return missing, partial, total, ms

    @app.get("/gaps", response_class=HTMLResponse)
    def gaps(request: Request, field: str = "", min_score: str = ""):
        from jobpilot.matching.gaps import by_field

        missing, partial, total, ms = gap_data(field, min_score)
        return render(request, "gaps.html", missing=missing, partial=partial, total=total, min_score=ms, field=field,
                      by_field=by_field(missing) if not field else {},
                      fields=["software", "data", "product", "hardware", "quant", "security", "design"],
                      export_qs=urlencode({k: v for k, v in (("field", field), ("min_score", ms)) if v}))

    @app.get("/gaps/export.{fmt}")
    def gaps_export(fmt: str, field: str = "", min_score: str = ""):
        from jobpilot.matching.gaps import to_csv, to_markdown

        missing, partial, total, ms = gap_data(field, min_score)
        if fmt == "md":
            return PlainTextResponse(to_markdown(missing, partial, total, ms, 50), media_type="text/markdown",
                                     headers={"Content-Disposition": 'attachment; filename="skill_gaps.md"'})
        if fmt == "csv":
            return _temp_file(lambda p: to_csv(missing, partial, p), "skill_gaps.csv", "text/csv")
        raise HTTPException(404)

    # ------------------------------------------------------------ runs

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        with db.connect(paths.db) as conn:
            source_runs = conn.execute("SELECT * FROM source_runs ORDER BY id DESC LIMIT 150").fetchall()
            match_runs = conn.execute("SELECT * FROM match_runs ORDER BY id DESC LIMIT 100").fetchall()
            spent = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM match_runs").fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM source_runs WHERE error IS NOT NULL AND error != ''").fetchone()[0]
        return render(request, "runs.html", source_runs=source_runs, match_runs=match_runs, spent=spent, errors=errors)

    # ------------------------------------------------------------ settings

    def settings_ctx() -> dict:
        return dict(has_key=bool(os.environ.get("ANTHROPIC_API_KEY")), has_usajobs=bool(os.environ.get("USAJOBS_API_KEY")),
                    path=paths.settings, ext_key=pagefill.extension_key(paths.home),
                    ext_dir=Path(__file__).resolve().parents[2] / "extension", base_url="http://127.0.0.1:8765")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        ctx = settings_ctx()
        ctx["base_url"] = f"{request.url.scheme}://{request.url.netloc}"
        return render(request, "settings.html", s=settings(), **ctx)

    @app.post("/settings")
    async def settings_save(request: Request):
        form = await posted(request)
        current = settings()
        try:
            new = settings_from_form(current, form)
        except (ValidationError, ValueError) as exc:
            return render(request, "settings.html", status_code=400, s=current, error=_friendly(exc), **settings_ctx())
        save_settings(new, paths.settings)
        return go("/settings", "settings")

    # ------------------------------------------------------------ exports

    @app.get("/export/applications.{fmt}")
    def export_log(fmt: str):
        with db.connect(paths.db) as conn:
            if fmt == "csv":
                return _temp_file(lambda p: export.to_csv(conn, p), "jobpilot_applications.csv", "text/csv")
            if fmt == "xlsx":
                return _temp_file(lambda p: export.to_xlsx(conn, p), "jobpilot_applications.xlsx",
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        raise HTTPException(404)

    return app


def _temp_file(write, filename: str, media_type: str) -> Response:
    fd, name = tempfile.mkstemp(suffix=Path(filename).suffix)
    os.close(fd)
    p = Path(name)
    try:
        write(p)
        data = p.read_bytes()
    finally:
        p.unlink(missing_ok=True)
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _friendly(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    return str(exc)


def settings_from_form(current: Settings, form) -> Settings:
    """Build new settings from the settings form. Unknown keys are ignored; the result is validated."""
    data = current.model_dump(mode="json")

    def num(name: str, cast):
        raw = str(form.get(name, "")).strip()
        if raw == "":
            raise ValueError(f"{name} can't be empty")
        try:
            return cast(raw)
        except ValueError:
            raise ValueError(f"{name}: {raw!r} isn't a number") from None

    data["user_agent"] = str(form.get("user_agent", data["user_agent"])).strip() or data["user_agent"]
    data["min_seconds_between_requests"] = num("min_seconds_between_requests", float)
    data["request_timeout_seconds"] = num("request_timeout_seconds", float)
    data["max_age_days"] = num("max_age_days", int)
    data["filter_by_profile"] = form.get("filter_by_profile") == "on"
    if "fu_first" in form:
        data["followups"] = {"first_after_days": num("fu_first", int), "then_every_days": num("fu_every", int),
                             "max_followups": num("fu_max", int), "quiet_after_days": num("fu_quiet", int)}
    src = data["sources"]
    for name in ("github_lists", "greenhouse", "lever", "ashby", "usajobs", "rss"):
        src[name]["enabled"] = form.get(f"src_{name}") == "on"
    src["usajobs"]["keywords"] = [k.strip() for k in str(form.get("usajobs_keywords", "")).split(",") if k.strip()]
    lists = []
    for line in str(form.get("github_lists", "")).splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) > 3 or "/" not in parts[0]:
            raise ValueError(f"GitHub list line {line!r}: use 'owner/repo branch kind'")
        lists.append({"repo": parts[0], "branch": parts[1] if len(parts) > 1 else "dev",
                      "kind": parts[2] if len(parts) > 2 else "internship"})
    src["github_lists"]["lists"] = lists
    feeds = []
    for line in str(form.get("rss_feeds", "")).splitlines():
        url, _, company = line.partition("|")
        if url.strip():
            if not url.strip().startswith(("http://", "https://")):
                raise ValueError(f"RSS feed {url.strip()!r} must start with http:// or https://")
            feeds.append({"url": url.strip(), "company": company.strip()})
    src["rss"]["feeds"] = feeds
    m = data["matching"]
    m["engine"] = str(form.get("engine", m["engine"]))
    m["min_score"] = num("min_score", int)
    m["claude_model"] = str(form.get("claude_model", m["claude_model"])).strip() or m["claude_model"]
    m["claude_concurrency"] = num("claude_concurrency", int)
    m["claude_max_jobs_per_run"] = num("claude_max_jobs_per_run", int)
    m["claude_spend_cap_usd"] = num("claude_spend_cap_usd", float)
    m["enrich_per_run"] = num("enrich_per_run", int)
    return Settings.model_validate(data)


def run(paths: Paths, port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://127.0.0.1:{port}/"
    print(f"JobPilot dashboard: {url}  (only reachable from this computer; Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(paths), host="127.0.0.1", port=port, log_level="warning")
