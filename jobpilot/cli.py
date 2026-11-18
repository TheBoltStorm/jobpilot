"""Command line entry point: `jobpilot <command>` (or `python -m jobpilot`)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from jobpilot.config import get_paths
from jobpilot.core import db


def cmd_onboard(args) -> int:
    from jobpilot.onboarding.interview import ConsoleIO, run_interview
    from jobpilot.onboarding.profile import load_profile, save_profile
    from jobpilot.onboarding.resume_parser import parse_resume
    from jobpilot.onboarding.resumes import add_resume

    paths = get_paths(args.home)
    io = ConsoleIO()
    with db.connect(paths.db) as conn:
        current = db.get_resume(conn)

    parsed, label = None, current["label"] if current else ""
    resume_path = args.resume
    if not resume_path and not current:
        resume_path = io.ask("Path to your resume (PDF or DOCX), blank to skip", "").strip('"')
    if resume_path:
        label, parsed = add_resume(paths, resume_path, args.label, make_default=True)
        print(f"Saved resume version '{label}'.")
    elif current:
        _, parsed = parse_resume(current["path"])
        print(f"Using resume version '{label}'. (Pass --resume to use a different file.)")

    profile = run_interview(io, parsed, load_profile(paths.profile))
    profile.default_resume = label
    save_profile(profile, paths.profile)
    print(f"\nProfile saved to {paths.profile}")
    print("Edit it any time with `jobpilot profile edit`, or re-run `jobpilot onboard`.")
    return 0


def cmd_resume(args) -> int:
    from jobpilot.onboarding.resumes import add_resume

    paths = get_paths(args.home)
    if args.action == "add":
        label, parsed = add_resume(paths, args.path, args.label, make_default=args.default)
        print(f"Added '{label}': {len(parsed.experience)} experience, {len(parsed.education)} education, "
              f"{len(parsed.skills)} skills.")
        for w in parsed.warnings:
            print(f"  ! {w}")
        return 0
    with db.connect(paths.db) as conn:
        if args.action == "list":
            rows = db.list_resumes(conn)
            if not rows:
                print("No resumes yet. Run `jobpilot onboard` or `jobpilot resume add <file> --label <name>`.")
            for r in rows:
                print(f"{'*' if r['is_default'] else ' '} {r['label']:<20} {Path(r['path']).name:<24} added {r['added_at']}")
            return 0
        if args.action == "show":
            row = db.get_resume(conn, args.label)
            if not row:
                print("No such resume.", file=sys.stderr)
                return 1
            print(json.dumps(json.loads(row["parsed_json"]), indent=2))
            return 0
        if args.action == "default":
            ok = db.set_default_resume(conn, args.label)
            print(f"Default resume is now '{args.label}'." if ok else "No such resume.")
            return 0 if ok else 1
    return 1


def cmd_profile(args) -> int:
    from jobpilot.onboarding.profile import load_profile

    paths = get_paths(args.home)
    if args.action == "path":
        print(paths.profile)
        return 0
    if not paths.profile.is_file():
        print("No profile yet. Run `jobpilot onboard`.", file=sys.stderr)
        return 1
    if args.action == "show":
        print(paths.profile.read_text(encoding="utf-8"))
    elif args.action == "validate":
        try:
            load_profile(paths.profile)
        except Exception as exc:  # show pydantic/yaml errors plainly
            print(f"Profile is invalid:\n{exc}", file=sys.stderr)
            return 1
        print("Profile is valid.")
    elif args.action == "edit":
        editor = os.environ.get("EDITOR")
        if editor:
            subprocess.call([editor, str(paths.profile)])
        elif sys.platform == "win32":
            os.startfile(paths.profile)  # type: ignore[attr-defined]
        else:
            subprocess.call(["xdg-open" if sys.platform != "darwin" else "open", str(paths.profile)])
    return 0


def _discovery_context(args):
    from jobpilot.onboarding.profile import load_profile
    from jobpilot.settings import load_settings
    from jobpilot.sources.companies import load_companies

    paths = get_paths(args.home)
    settings = load_settings(paths.settings)
    profile = load_profile(paths.profile)
    return paths, settings, profile, load_companies(paths.companies)


def _make_sources(settings, profile, companies, only=None):
    from jobpilot.core.http import PoliteClient
    from jobpilot.sources import ALL_SOURCES

    http = PoliteClient(settings.user_agent, settings.min_seconds_between_requests, settings.request_timeout_seconds)
    classes = [s for s in ALL_SOURCES if not only or s.name in only]
    return http, [cls(http, settings, profile, companies) for cls in classes]


def cmd_companies(args) -> int:
    from jobpilot.sources.companies import load_companies, save_companies, starter_companies
    from jobpilot.onboarding.profile import load_profile

    paths = get_paths(args.home)
    if args.action == "init":
        if paths.companies.is_file() and not args.force:
            print(f"{paths.companies} already exists. Use --force to overwrite it.", file=sys.stderr)
            return 1
        profile = load_profile(paths.profile)
        fields = profile.targets.fields + profile.targets.roles if profile else []
        companies = starter_companies(fields)
        save_companies(companies, paths.companies)
        print(f"Wrote {len(companies)} companies to {paths.companies}"
              + (f" (matched to: {', '.join(fields)})" if fields else " (no target fields in profile, so all of them)"))
        print("Edit that file to add or remove companies.")
        return 0
    companies = load_companies(paths.companies)
    if not companies:
        print("No companies yet. Run `jobpilot companies init`.")
    for c in companies:
        print(f"{c.ats:<11} {c.token:<18} {c.name:<22} {', '.join(c.fields)}")
    return 0


def cmd_sources(args) -> int:
    paths, settings, profile, companies = _discovery_context(args)
    http, sources = _make_sources(settings, profile, companies)
    for s in sources:
        ok, why = s.enabled()
        print(f"[{'on ' if ok else 'off'}] {s.name:<13} {s.label}" + (f"  ({why})" if why else ""))
        print(f"      {s.terms_note}")
    http.close()
    print("\nNot included on purpose (see docs/SOURCES.md): LinkedIn, Indeed, Glassdoor, ZipRecruiter.")
    return 0


def cmd_discover(args) -> int:
    from jobpilot.discovery import run_discovery

    paths, settings, profile, companies = _discovery_context(args)
    if profile is None:
        print("No profile yet, so jobs won't be filtered to your targets. Run `jobpilot onboard` first for better results.")
    if not companies:
        print("No companies.yaml yet; run `jobpilot companies init` to add company job boards.")
    http, sources = _make_sources(settings, profile, companies, args.source)
    print("Discovering jobs (rate-limited; this can take a few minutes)...")
    try:
        with db.connect(paths.db) as conn:
            summaries = run_discovery(conn, sources, settings, profile)
            total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status != 'posting_closed'").fetchone()[0]
    finally:
        http.close()
    new = sum(s.new for s in summaries)
    merged = sum(s.merged for s in summaries)
    closed = sum(s.closed for s in summaries)
    errors = [s for s in summaries if s.error]
    print(f"\nDone: {new} new jobs, {merged} duplicates merged, {closed} marked closed, "
          f"{http.requests_made} requests. {total} open jobs stored.")
    if errors:
        print(f"{len(errors)} listing(s) failed; see above. Check the company token in companies.yaml.")
    return 0


def cmd_import_csv(args) -> int:
    from jobpilot.discovery import Filter, store_scope
    from jobpilot.sources.csv_import import parse_csv

    paths, settings, profile, _ = _discovery_context(args)
    result = parse_csv(args.path)
    flt = Filter.build(settings, profile) if args.filter else Filter()
    with db.connect(paths.db) as conn:
        s = store_scope(conn, f"csv:{args.name}", result, flt)
    print(f"Imported {args.path}: {s.fetched} rows, {s.kept} kept, {s.new} new, {s.merged} duplicates merged.")
    return 0


def cmd_jobs(args) -> int:
    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        if args.action == "show":
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.id,)).fetchone()
            if not row:
                print("No such job.", file=sys.stderr)
                return 1
            for k in ("id", "title", "company", "location", "remote", "role_type", "field", "ats", "status",
                      "date_posted", "date_found", "url", "apply_url"):
                if row[k] not in ("", None):
                    print(f"{k:>12}: {row[k]}")
            srcs = conn.execute("SELECT source, scope, active FROM job_sources WHERE job_id = ?", (args.id,)).fetchall()
            print(f"{'sources':>12}: " + ", ".join(f"{s['source']}:{s['scope']}{'' if s['active'] else ' (gone)'}" for s in srcs))
            print("\n" + (row["description"] or "(no description from this source yet)"))
            return 0
        where, params = ["1=1"], []
        if args.status:
            where.append("status = ?")
            params.append(args.status)
        elif not args.all:
            where.append("status != 'posting_closed'")
        for col, val in (("role_type", args.type), ("ats", args.ats)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        if args.search:
            where.append("(title LIKE ? OR company LIKE ?)")
            params += [f"%{args.search}%"] * 2
        if args.region or args.area:
            from jobpilot.core import places

            places.refresh(conn)
            psql, pparams = places.sql_filter(args.region or "", "", args.area or "")
            where.append(psql.replace("p.job_id = j.id", "p.job_id = jobs.id"))
            params += pparams
        sql = f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY date_posted DESC, id DESC LIMIT ?"
        rows = conn.execute(sql, (*params, args.limit)).fetchall()
        count = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {' AND '.join(where)}", params).fetchone()[0]
        for r in rows:
            loc = (r["location"] or "")[:24]
            print(f"{r['id']:>6}  {r['date_posted'] or '?':<10}  {r['company'][:20]:<20}  {r['title'][:46]:<46}  "
                  f"{loc:<24}  {r['ats']}")
        print(f"\nShowing {len(rows)} of {count}. `jobpilot jobs show <id>` for details.")
        if args.summary:
            print("\nBy status:")
            for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY n DESC"):
                print(f"  {r['status']:<16} {r['n']}")
            print("By ATS (open jobs):")
            for r in conn.execute("SELECT ats, COUNT(*) n FROM jobs WHERE status != 'posting_closed' GROUP BY ats ORDER BY n DESC"):
                print(f"  {r['ats'] or '?':<16} {r['n']}")
    return 0


def cmd_enrich(args) -> int:
    from jobpilot.enrich import enrich_descriptions

    paths, settings, profile, companies = _discovery_context(args)
    http, _ = _make_sources(settings, profile, companies, only=["none"])
    limit = args.limit or settings.matching.enrich_per_run
    print(f"Fetching up to {limit} missing descriptions from Greenhouse/Lever/Ashby (rate-limited)...")
    try:
        with db.connect(paths.db) as conn:
            s = enrich_descriptions(conn, http, companies, limit=limit, retry_failed=args.retry_failed)
            left = conn.execute("""SELECT COUNT(*) FROM jobs WHERE description = '' AND enrich_status = ''
                                   AND status != 'posting_closed' AND ats IN ('greenhouse','lever','ashby')""").fetchone()[0]
    finally:
        http.close()
    print(f"Added {s.filled} descriptions, {s.failed} couldn't be fetched, {http.requests_made} requests. "
          f"{left} still to fetch" + (" (run again to continue)." if left else "."))
    for e in s.errors:
        print(f"  ! {e}")
    return 0


def _profile_or_exit(paths):
    from jobpilot.onboarding.profile import load_profile

    profile = load_profile(paths.profile)
    if profile is None:
        print("No profile yet. Run `jobpilot onboard` first.", file=sys.stderr)
        raise SystemExit(1)
    return profile


def cmd_match(args) -> int:
    from jobpilot.matching.engine import run_local
    from jobpilot.matching.skills import load_taxonomy
    from jobpilot.settings import load_settings

    paths = get_paths(args.home)
    settings = load_settings(paths.settings)
    profile = _profile_or_exit(paths)
    tax = load_taxonomy(paths.home / "skills.yaml")
    engine = args.engine or settings.matching.engine
    with db.connect(paths.db) as conn:
        if engine == "claude":
            from jobpilot.matching.claude import run_claude

            try:
                s = run_claude(conn, profile, tax, settings.matching, rescore=args.rescore, limit=args.limit)
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        else:
            s = run_local(conn, profile, tax, settings.matching.min_score, rescore=args.rescore, limit=args.limit)
        no_desc = conn.execute("""SELECT COUNT(*) FROM matches m JOIN jobs j ON j.id = m.job_id
                                  WHERE j.description = '' AND j.status IN ('found', 'matched')""").fetchone()[0]
    cost = f", {s.input_tokens:,} in / {s.output_tokens:,} out tokens, about ${s.cost_usd:.4f}" if engine == "claude" else " (free)"
    print(f"Scored {s.scored} jobs with the {s.engine} engine{cost}. "
          f"{s.matched} newly scored {settings.matching.min_score}+ and are now 'matched'. "
          "Jobs you've prepared or decided on keep their status.")
    if no_desc:
        print(f"{no_desc} jobs have no description, so their scores are title-only estimates. "
              "`jobpilot enrich` fetches descriptions where it can.")
    return 0


def _matches_query(args, min_score):
    where, params = ["m.score >= ?", "j.status NOT IN ('posting_closed', 'rejected')"], [min_score]
    if args.type:
        where.append("j.role_type = ?")
        params.append(args.type)
    if args.location:
        where.append("(j.location LIKE ? OR j.remote = ?)")
        params += [f"%{args.location}%", args.location.lower()]
    if args.field:
        where.append("(',' || m.tags || ',') LIKE ?")
        params.append(f"%,{args.field},%")
    if args.since:
        where.append("j.date_posted >= ?")
        params.append(args.since)
    if args.search:
        where.append("(j.title LIKE ? OR j.company LIKE ?)")
        params += [f"%{args.search}%"] * 2
    if args.with_description:
        where.append("j.description != ''")
    return " AND ".join(where), params


def cmd_matches(args) -> int:
    from jobpilot.settings import load_settings

    paths = get_paths(args.home)
    settings = load_settings(paths.settings)
    min_score = settings.matching.min_score if args.min_score is None else args.min_score
    where, params = _matches_query(args, min_score)
    with db.connect(paths.db) as conn:
        rows = conn.execute(
            f"""SELECT j.id, j.title, j.company, j.location, j.date_posted, m.score, m.confidence, m.reason
                FROM matches m JOIN jobs j ON j.id = m.job_id WHERE {where}
                ORDER BY m.score DESC, j.date_posted DESC LIMIT ?""", (*params, args.limit)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM matches m JOIN jobs j ON j.id = m.job_id WHERE {where}",
                             params).fetchone()[0]
    for r in rows:
        conf = {"high": "", "medium": "~", "low": "?"}[r["confidence"]]
        print(f"{r['score']:>3}{conf:<1} {r['id']:>6}  {r['company'][:18]:<18}  {r['title'][:44]:<44}  "
              f"{(r['location'] or '')[:20]:<20} {r['date_posted'] or ''}")
        if args.reasons:
            print(f"            {r['reason']}")
    print(f"\n{len(rows)} of {total} jobs scoring {min_score}+.  ~ = medium confidence, ? = low (no description).")
    print("`jobpilot match show <id>` shows the skill breakdown for one job.")
    return 0


def cmd_match_show(args) -> int:
    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        r = conn.execute("""SELECT j.*, m.score, m.confidence, m.reason, m.skills_json, m.engine
                            FROM jobs j JOIN matches m ON m.job_id = j.id WHERE j.id = ?""", (args.id,)).fetchone()
    if not r:
        print("No score for that job yet. Run `jobpilot match`.", file=sys.stderr)
        return 1
    print(f"{r['title']} - {r['company']} ({r['location'] or 'location unknown'})")
    print(f"{r['url']}")
    print(f"\nScore {r['score']}/100 ({r['confidence']} confidence, {r['engine']} engine)\n{r['reason']}\n")
    skills = json.loads(r["skills_json"])["skills"]
    labels = {"have": "You have", "partial": "You partly have", "missing": "MISSING"}
    for status in ("have", "partial", "missing"):
        group = [s for s in skills if s["status"] == status]
        if not group:
            continue
        print(f"{labels[status]}:")
        for s in sorted(group, key=lambda s: ["required", "preferred", "mentioned"].index(s["importance"])):
            line = f"  - {s['name']} [{s['importance']}]"
            if s["evidence"]:
                line += f"  <- {s['evidence'][:110]}"
            print(line)
        print()
    return 0


def cmd_gaps(args) -> int:
    from jobpilot.matching.gaps import by_field, gap_report, to_csv, to_markdown
    from jobpilot.matching.skills import load_taxonomy
    from jobpilot.settings import load_settings

    paths = get_paths(args.home)
    settings = load_settings(paths.settings)
    min_score = settings.matching.min_score if args.min_score is None else args.min_score
    tax = load_taxonomy(paths.home / "skills.yaml")
    with db.connect(paths.db) as conn:
        rows, partial, total = gap_report(conn, tax, min_score, args.field)
    if not total:
        print("No matched jobs with descriptions yet. Run `jobpilot enrich` and `jobpilot match` (or lower --min-score).")
        return 0
    if args.export:
        out = Path(args.export)
        if out.suffix.lower() == ".csv":
            to_csv(rows, partial, out)
        else:
            out.write_text(to_markdown(rows, partial, total, min_score, args.top), encoding="utf-8")
        print(f"Wrote {out}")
        return 0
    print(f"Skill Gap Report: {total} matched jobs with descriptions (score {min_score}+)"
          + (f", field '{args.field}'" if args.field else ""))
    print(f"\nMissing:\n{'#':>3}  {'Skill':<28} {'Asked for in':>12} {'Required in':>12}")
    for i, g in enumerate(rows[: args.top], 1):
        print(f"{i:>3}  {g.name[:28]:<28} {g.jobs:>12} {g.required_in:>12}")
    if partial:
        print("\nYou partly have (worth strengthening): "
              + ", ".join(f"{g.name} ({g.jobs})" for g in partial[:8]))
    print("\nHow to learn the top 5:")
    for g in rows[:5]:
        print(f"  {g.name}: {g.learn}")
    if not args.field:
        print("\nBy field (top missing):")
        for tag, gs in by_field(rows).items():
            print(f"  {tag:<9} " + ", ".join(f"{g.name} ({g.by_field[tag]})" for g in gs[:6]))
    print("\nExport with --export gaps.md or --export gaps.csv")
    return 0


def cmd_runs(args) -> int:
    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        rows = conn.execute("SELECT * FROM match_runs ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
        total = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM match_runs").fetchone()[0]
    print(f"{'started':<26} {'engine':<26} {'jobs':>6} {'tokens in':>10} {'out':>8} {'cost':>9}")
    for r in rows:
        print(f"{r['started_at']:<26} {r['engine']:<26} {r['jobs_scored']:>6} {r['input_tokens']:>10,} "
              f"{r['output_tokens']:>8,} ${r['cost_usd']:>8.4f}" + (f"  ! {r['error'][:60]}" if r["error"] else ""))
    print(f"\nTotal spent on Claude scoring: ${total:.4f}")
    return 0


def _gh_tokens(companies) -> dict[str, str]:
    from jobpilot.sources.dedupe import company_key

    return {company_key(c.name): c.token for c in companies if c.ats == "greenhouse"}


def _llm_map(settings):
    """The optional Claude label mapper, only when you've chosen the Claude engine and have a key."""
    import os

    if settings.matching.engine != "claude" or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None
    from jobpilot.review.mapping import claude_mapper

    return claude_mapper(settings.matching.claude_model)


def cmd_forms(args) -> int:
    from jobpilot.forms import fetch_forms, load_form
    from jobpilot.review import plan
    from jobpilot.review.status import GateError

    paths, settings, profile, companies = _discovery_context(args)
    if args.action == "show":
        with db.connect(paths.db) as conn:
            form = load_form(conn, args.id)
        if not form:
            print(f"No form stored for job {args.id}. Run `jobpilot forms fetch {args.id}`.", file=sys.stderr)
            return 1
        print(form[0])
        for q in form[1]:
            req = "*" if q.required else " "
            opts = f"  [{' | '.join(q.options[:5])}{' ...' if len(q.options) > 5 else ''}]" if q.options else ""
            print(f" {req} {q.section:<11} {q.kind:<13} {q.label[:70]}{opts}")
        return 0
    if profile is None:
        print("No profile yet. Run `jobpilot onboard` first.", file=sys.stderr)
        return 1
    http, _ = _make_sources(settings, profile, companies, only=["none"])
    llm = _llm_map(settings)
    try:
        with db.connect(paths.db) as conn:
            if args.id:
                try:
                    with conn:
                        app = plan.load_real_form(conn, args.id, profile, http, _gh_tokens(companies), llm)
                except GateError as exc:
                    print(exc, file=sys.stderr)
                    return 1
                left = len(app.unanswered)
                print(f"Loaded {len([f for f in app.fields if f.field_id])} questions for job {args.id}. "
                      f"{left} need you. Review them with `jobpilot dashboard`.")
                return 0
            statuses = ("ready_to_fill", "needs_human") if not args.all else (
                "matched", "ready_to_fill", "needs_human", "found")
            print(f"Loading real form questions for up to {args.limit} Greenhouse/Lever jobs (rate-limited)...")
            ok, failed = fetch_forms(conn, http, _gh_tokens(companies), statuses, args.limit, llm_map=llm,
                                     profile=profile)
            ids = [r[0] for r in conn.execute("SELECT job_id FROM forms")]
            with conn:
                for job_id in ids:
                    plan.prepare(conn, job_id, profile)
    finally:
        http.close()
    print(f"Loaded {ok} forms, {failed} failed. For a job you already approved, load its form on its own "
          "(`jobpilot forms fetch ID`); that withdraws the approval so you can check the real questions.")
    return 0


def cmd_autofill(args) -> int:
    from jobpilot.autofill import run_autofill
    from jobpilot.review.status import GateError
    from jobpilot.settings import load_settings

    paths = get_paths(args.home)
    settings = load_settings(paths.settings)
    mode = "fill" if args.fill else "dry_run"
    try:
        from jobpilot.onboarding.profile import load_profile

        result = run_autofill(paths, args.id, mode, profile=load_profile(paths.profile),
                              channel=args.browser or settings.autofill.browser_channel,
                              headless=args.headless, wait_for_close=not args.no_wait,
                              slow_mo_ms=settings.autofill.slow_mo_ms)
    except GateError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0 if result.outcome in ("dry_run_done", "stopped_before_submit") else 2


def cmd_dashboard(args) -> int:
    from jobpilot.web.app import run

    run(get_paths(args.home), port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_prepare(args) -> int:
    from jobpilot.review.plan import prepare_matched

    paths = get_paths(args.home)
    profile = _profile_or_exit(paths)
    with db.connect(paths.db) as conn:
        n = prepare_matched(conn, profile)
        counts = dict(conn.execute("""SELECT status, COUNT(*) FROM jobs
                                      WHERE status IN ('ready_to_fill','needs_human','custom_site','account_wall')
                                      GROUP BY status""").fetchall())
    print(f"Prepared {n} matched jobs. Nothing was filled or sent.")
    for k, v in counts.items():
        print(f"  {k:<14} {v}")
    print("Review them with `jobpilot dashboard`.")
    return 0


def cmd_tailor(args) -> int:
    from jobpilot.matching.skills import load_taxonomy
    from jobpilot.review import tailor
    from jobpilot.review.status import GateError

    paths = get_paths(args.home)
    profile = _profile_or_exit(paths)
    with db.connect(paths.db) as conn:
        try:
            with conn:
                if args.approve:
                    label = tailor.approve(conn, args.id, profile, paths.ensure().resumes)
                    print(f"Created resume version '{label}' and picked it for job {args.id}.")
                    return 0
                if args.discard:
                    tailor.discard(conn, args.id)
                    print("Discarded.")
                    return 0
                d = tailor.propose(conn, args.id, profile, load_taxonomy(paths.home / "skills.yaml"))
        except GateError as exc:
            print(exc, file=sys.stderr)
            return 1
    print(f"Suggested order for job {args.id} (same content, nothing added or removed):")
    for m in d.moves:
        where = m.section if m.group in ("project order", "skills list") else f"{m.section} / {m.group}"
        print(f"  #{m.old} -> #{m.new}  {where}: {m.text[:80]}")
    print(f"Approve on the job's page in the dashboard, or `jobpilot tailor {args.id} --approve`. No file is made until you do.")
    return 0


def cmd_run(args) -> int:
    from jobpilot.pipeline import run_pipeline

    return run_pipeline(args.home, skip_discover=args.skip_discover, enrich_limit=args.enrich_limit,
                        forms_limit=args.forms_limit)


def cmd_doctor(args) -> int:
    from jobpilot.pipeline import doctor

    return doctor(get_paths(args.home), check_browser=not args.no_browser)


def cmd_places(args) -> int:
    from jobpilot.core import places

    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        places.refresh(conn)
        for region, n, areas in places.facets(conn):
            if args.region and args.region.lower() not in region.lower():
                continue
            print(f"{region}  ({n} open jobs)")
            for area, c in areas[: args.top]:
                print(f"  {c:>5}  {area}")
            if len(areas) > args.top:
                print(f"         ... {len(areas) - args.top} more areas")
    print("\nFilter with `jobpilot jobs list --area \"SF Bay Area\"` or `--region \"US West\"`, "
          "or in the dashboard's Jobs and You qualify pages.")
    return 0


def cmd_qualify(args) -> int:
    from jobpilot.core import places
    from jobpilot.matching import minimums

    paths = get_paths(args.home)
    profile = _profile_or_exit(paths)
    want = "close" if args.close else "qualify"
    with db.connect(paths.db) as conn:
        places.refresh(conn)
        minimums.refresh(conn, profile)
        where = ["q.status = ?", "j.status IN ('found', 'matched', 'ready_to_fill', 'needs_human')"]
        params: list = [want]
        if args.region or args.area:
            psql, pparams = places.sql_filter(args.region or "", "", args.area or "")
            where.append(psql)
            params += pparams
        rows = conn.execute(
            f"""SELECT j.id, j.title, j.company, m.score, q.detail_json,
                       (SELECT GROUP_CONCAT(area, ' / ') FROM job_places p WHERE p.job_id = j.id) areas
                FROM qualifications q JOIN jobs j ON j.id = q.job_id LEFT JOIN matches m ON m.job_id = j.id
                WHERE {' AND '.join(where)} ORDER BY m.score DESC LIMIT ?""", (*params, args.limit)).fetchall()
    print("Open jobs where you meet every minimum requirement found in the posting:" if want == "qualify"
          else "Open jobs where you're one requirement short:")
    for r in rows:
        line = f"{r['id']:>6}  {r['score'] or '-':>3}  {r['company'][:20]:<20}  {r['title'][:44]:<44}  {(r['areas'] or '')[:28]}"
        if want == "close":
            miss = [c["what"] for c in json.loads(r["detail_json"])["checks"] if c["ok"] is False]
            line += f"  (missing: {', '.join(miss)})"
        print(line)
    print(f"\n{len(rows)} shown. `jobpilot match show ID` for the details, or open the dashboard.")
    return 0


def cmd_applied(args) -> int:
    from jobpilot.review import outcomes

    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        with conn:
            outcomes.ensure(conn)
        rows = conn.execute(
            """SELECT o.*, j.title, j.company, a.submitted_at FROM outcomes o JOIN jobs j ON j.id = o.job_id
               LEFT JOIN applications a ON a.job_id = j.id ORDER BY a.submitted_at DESC""").fetchall()
    if not rows:
        print("No submitted applications yet. Mark a job submitted on its page after you click Submit on the site.")
        return 0
    for r in rows:
        nxt = f"  next: {r['next_at']} {r['next_note']}" if r["next_at"] else ""
        print(f"{r['job_id']:>6}  {(r['submitted_at'] or '')[:10]:<10}  {outcomes.BY_KEY[r['stage']].label:<22}  "
              f"{r['company'][:20]:<20}  {r['title'][:40]:<40}  follow-ups: {r['followups']}{nxt}")
    print("\nChange a stage: `jobpilot outcome ID STAGE`. Stages: " + ", ".join(s.key for s in outcomes.STAGES))
    return 0


def cmd_outcome(args) -> int:
    from jobpilot.review import outcomes
    from jobpilot.review.status import GateError

    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        try:
            with conn:
                outcomes.set_stage(conn, args.id, args.stage, args.note or "", args.on)
                if args.next is not None:
                    outcomes.set_next(conn, args.id, args.next or None, args.next_note or "")
        except GateError as exc:
            print(exc, file=sys.stderr)
            return 1
    print(f"Job {args.id}: {outcomes.BY_KEY[args.stage].label}.")
    return 0


def cmd_followups(args) -> int:
    from jobpilot.onboarding.profile import load_profile
    from jobpilot.review import outcomes
    from jobpilot.review.status import GateError
    from jobpilot.settings import load_settings

    paths = get_paths(args.home)
    rules = outcomes.FollowupRules.from_settings(load_settings(paths.settings))
    with db.connect(paths.db) as conn:
        try:
            with conn:
                if args.sent:
                    outcomes.followup_sent(conn, args.sent)
                    print(f"Recorded a follow-up for job {args.sent}. (JobPilot didn't send anything.)")
                    return 0
                if args.mute:
                    outcomes.mute(conn, args.mute)
                    print(f"No more reminders for job {args.mute}.")
                    return 0
        except GateError as exc:
            print(exc, file=sys.stderr)
            return 1
        due = outcomes.due(conn, rules)
    if not due:
        print("Nothing to follow up on right now.")
        return 0
    profile = load_profile(paths.profile)
    for d in due:
        what = {"thank_you": "thank-you note", "quiet": "quiet a long time", "followup": f"follow-up {d.followups + 1}"}[d.kind]
        print(f"{d.job_id:>6}  {what:<18}  {d.days:>3} days quiet  {d.company[:20]:<20}  {d.title[:44]}")
        if args.drafts and d.kind != "quiet":
            subj, body = outcomes.draft(d.kind, d.title, d.company, d.submitted, profile)
            print(f"\n  DRAFT  Subject: {subj}\n" + "\n".join("  " + ln for ln in body.splitlines()) + "\n")
    print("\nJobPilot never sends these. Send it yourself, then `jobpilot followups --sent ID`."
          + ("" if args.drafts else " Add --drafts to see the drafts."))
    return 0


def cmd_stats(args) -> int:
    from jobpilot.review import outcomes

    paths = get_paths(args.home)
    with db.connect(paths.db) as conn:
        with conn:
            outcomes.ensure(conn)
        st = outcomes.stats(conn)
    o = st["overall"]
    if not o.applied:
        print("No submitted applications yet.")
        return 0
    print(f"{o.applied} applications: {o.pct(o.responded)}% heard back, {o.pct(o.interviews)}% interviews, "
          f"{o.pct(o.offers)}% offers.")
    if st["median_days_to_reply"] is not None:
        print(f"Replies usually came after {st['median_days_to_reply']} days.")
    if o.applied < 10:
        print("(Small numbers so far; rates get meaningful after a few dozen.)")
    for group, rates in st["groups"].items():
        print(f"\n{group}:")
        for r in rates:
            print(f"  {r.group:<16} {r.applied:>4} applied  {r.pct(r.responded):>3}% heard back  "
                  f"{r.pct(r.interviews):>3}% interview  {r.pct(r.offers):>3}% offer")
    return 0


def cmd_export(args) -> int:
    from jobpilot.review import export

    paths = get_paths(args.home)
    out = Path(args.path)
    with db.connect(paths.db) as conn:
        n = export.to_xlsx(conn, out) if out.suffix.lower() == ".xlsx" else export.to_csv(conn, out)
    print(f"Wrote {n} applications to {out} (no answers or personal details included).")
    return 0


def cmd_sample(args) -> int:
    from jobpilot.samples import write_sample_docx, write_sample_pdf

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Wrote {write_sample_docx(out / 'sample_resume.docx')}")
    try:
        print(f"Wrote {write_sample_pdf(out / 'sample_resume.pdf')}")
    except ImportError:
        print("(Skipped PDF: install the dev extra for fpdf2.)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobpilot", description="Find jobs, pre-fill applications, flag skill gaps. You always submit.")
    p.add_argument("--home", help="data folder (default: ./data or $JOBPILOT_HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("onboard", help="parse your resume and run the setup interview")
    o.add_argument("--resume", help="path to resume PDF/DOCX")
    o.add_argument("--label", default="default", help="name for this resume version")
    o.set_defaults(func=cmd_onboard)

    r = sub.add_parser("resume", help="manage resume versions")
    rs = r.add_subparsers(dest="action", required=True)
    ra = rs.add_parser("add")
    ra.add_argument("path")
    ra.add_argument("--label", required=True)
    ra.add_argument("--default", action="store_true")
    rs.add_parser("list")
    rsh = rs.add_parser("show")
    rsh.add_argument("label", nargs="?")
    rd = rs.add_parser("default")
    rd.add_argument("label")
    r.set_defaults(func=cmd_resume)

    pr = sub.add_parser("profile", help="show, edit or validate profile.yaml")
    pr.add_argument("action", choices=["show", "edit", "validate", "path"])
    pr.set_defaults(func=cmd_profile)

    c = sub.add_parser("companies", help="manage company job boards (data/companies.yaml)")
    c.add_argument("action", choices=["init", "list"])
    c.add_argument("--force", action="store_true", help="overwrite existing companies.yaml")
    c.set_defaults(func=cmd_companies)

    so = sub.add_parser("sources", help="list job sources and whether they're enabled")
    so.set_defaults(func=cmd_sources)

    d = sub.add_parser("discover", help="fetch new jobs from enabled sources")
    d.add_argument("--source", action="append", help="only run this source (repeatable)")
    d.set_defaults(func=cmd_discover)

    ic = sub.add_parser("import-csv", help="import jobs from a CSV you exported (e.g. Handshake)")
    ic.add_argument("path")
    ic.add_argument("--name", default="import", help="label for this import, e.g. handshake")
    ic.add_argument("--no-filter", dest="filter", action="store_false", help="keep every row")
    ic.set_defaults(func=cmd_import_csv)

    j = sub.add_parser("jobs", help="browse stored jobs")
    js = j.add_subparsers(dest="action", required=True)
    jl = js.add_parser("list")
    jl.add_argument("--status")
    jl.add_argument("--type", help="internship | co-op | part-time | full-time")
    jl.add_argument("--ats", help="greenhouse | lever | ashby | workday | other ...")
    jl.add_argument("--search", help="text in title or company")
    jl.add_argument("--region", help="e.g. 'US West', 'Remote', 'Europe' (see `jobpilot places`)")
    jl.add_argument("--area", help="area or city text, e.g. 'SF Bay Area', 'Austin', 'London'")
    jl.add_argument("--limit", type=int, default=25)
    jl.add_argument("--all", action="store_true", help="include closed postings")
    jl.add_argument("--summary", action="store_true", help="also print counts by status and ATS")
    jsh = js.add_parser("show")
    jsh.add_argument("id", type=int)
    j.set_defaults(func=cmd_jobs)

    e = sub.add_parser("enrich", help="fetch missing job descriptions (Greenhouse/Lever/Ashby only)")
    e.add_argument("--limit", type=int)
    e.add_argument("--retry-failed", action="store_true")
    e.set_defaults(func=cmd_enrich)

    m = sub.add_parser("match", help="score jobs against your profile, or `match show ID`")
    m.add_argument("show", nargs="?", choices=["show"], help=argparse.SUPPRESS)
    m.add_argument("id", nargs="?", type=int, help=argparse.SUPPRESS)
    m.add_argument("--engine", choices=["local", "claude"], help="override settings.matching.engine")
    m.add_argument("--rescore", action="store_true", help="rescore everything, not just new/changed jobs")
    m.add_argument("--limit", type=int)
    m.set_defaults(func=lambda a: cmd_match_show(a) if a.show else cmd_match(a))

    ms = sub.add_parser("matches", help="list scored jobs with filters")
    ms.add_argument("--min-score", type=int)
    ms.add_argument("--type", help="internship | co-op | part-time | full-time")
    ms.add_argument("--location", help="text in location, or 'remote'")
    ms.add_argument("--field", help="software | data | product | hardware | quant | security | design")
    ms.add_argument("--since", help="posted on/after YYYY-MM-DD")
    ms.add_argument("--search", help="text in title or company")
    ms.add_argument("--with-description", action="store_true", help="only jobs with a full description")
    ms.add_argument("--reasons", action="store_true", help="show the reason under each job")
    ms.add_argument("--limit", type=int, default=25)
    ms.set_defaults(func=cmd_matches)

    g = sub.add_parser("gaps", help="Skill Gap Report across matched jobs")
    g.add_argument("--min-score", type=int)
    g.add_argument("--field")
    g.add_argument("--top", type=int, default=20)
    g.add_argument("--export", help="write to .md or .csv")
    g.set_defaults(func=cmd_gaps)

    ru = sub.add_parser("runs", help="scoring run log with token costs")
    ru.add_argument("--limit", type=int, default=15)
    ru.set_defaults(func=cmd_runs)

    fo = sub.add_parser("forms", help="load real application questions (Greenhouse, Lever) for review")
    fos = fo.add_subparsers(dest="action", required=True)
    ff = fos.add_parser("fetch", help="fetch one job's form, or forms for prepared jobs")
    ff.add_argument("id", type=int, nargs="?")
    ff.add_argument("--limit", type=int, default=25)
    ff.add_argument("--all", action="store_true", help="include matched and found jobs too")
    fsh = fos.add_parser("show")
    fsh.add_argument("id", type=int)
    fo.set_defaults(func=cmd_forms)

    af = sub.add_parser("autofill", help="dry-run (default) or fill an approved application; never submits")
    af.add_argument("id", type=int)
    af.add_argument("--fill", action="store_true", help="type the approved answers (stops before Submit)")
    af.add_argument("--browser", choices=["msedge", "chrome", "chromium"], help="default: settings.autofill")
    af.add_argument("--headless", action="store_true", help="dry run without a visible window")
    af.add_argument("--no-wait", action="store_true", help="close the browser when done instead of waiting")
    af.set_defaults(func=cmd_autofill)

    pp = sub.add_parser("prepare", help="sort matched jobs into Ready to fill / Needs human / Custom site")
    pp.set_defaults(func=cmd_prepare)

    db_ = sub.add_parser("dashboard", help="open the review dashboard in your browser (local only)")
    db_.add_argument("--port", type=int, default=8765)
    db_.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    db_.set_defaults(func=cmd_dashboard)

    rn = sub.add_parser("run", help="discover, enrich, match, prepare and load forms in one go (fills nothing)")
    rn.add_argument("--skip-discover", action="store_true", help="work on jobs you already have")
    rn.add_argument("--enrich-limit", type=int, default=50)
    rn.add_argument("--forms-limit", type=int, default=25)
    rn.set_defaults(func=cmd_run)

    dr = sub.add_parser("doctor", help="check your setup: profile, resume, privacy, settings, browser")
    dr.add_argument("--no-browser", action="store_true", help="skip starting the autofill browser")
    dr.set_defaults(func=cmd_doctor)

    pl = sub.add_parser("places", help="open jobs by region and area")
    pl.add_argument("--region", help="only this region")
    pl.add_argument("--top", type=int, default=10, help="areas per region")
    pl.set_defaults(func=cmd_places)

    qu = sub.add_parser("qualify", help="open jobs where you meet every minimum requirement")
    qu.add_argument("--close", action="store_true", help="show jobs one requirement short instead")
    qu.add_argument("--region")
    qu.add_argument("--area")
    qu.add_argument("--limit", type=int, default=30)
    qu.set_defaults(func=cmd_qualify)

    ap = sub.add_parser("applied", help="your submitted applications and their stages")
    ap.set_defaults(func=cmd_applied)

    oc = sub.add_parser("outcome", help="record what happened with an application (only you set this)")
    oc.add_argument("id", type=int)
    oc.add_argument("stage", choices=["waiting", "screen", "assessment", "interview", "final", "offer", "accepted",
                                      "declined", "rejected", "withdrawn", "no_response"])
    oc.add_argument("--note")
    oc.add_argument("--on", help="date it happened, YYYY-MM-DD (default today)")
    oc.add_argument("--next", help="date of the next step, YYYY-MM-DD ('' to clear)")
    oc.add_argument("--next-note", help="what the next step is")
    oc.set_defaults(func=cmd_outcome)

    fu = sub.add_parser("followups", help="follow-ups due, with drafts you send yourself")
    fu.add_argument("--drafts", action="store_true", help="print the draft messages")
    fu.add_argument("--sent", type=int, metavar="ID", help="record that you sent a follow-up")
    fu.add_argument("--mute", type=int, metavar="ID", help="stop reminders for this application")
    fu.set_defaults(func=cmd_followups)

    stt = sub.add_parser("stats", help="response, interview and offer rates, and what's working")
    stt.set_defaults(func=cmd_stats)

    tl = sub.add_parser("tailor", help="suggest a resume version reordered for one job (you approve the diff)")
    tl.add_argument("id", type=int)
    g = tl.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true", help="create the suggested version as a DOCX")
    g.add_argument("--discard", action="store_true")
    tl.set_defaults(func=cmd_tailor)

    ex = sub.add_parser("export", help="export the applications log (.csv or .xlsx)")
    ex.add_argument("path", help="e.g. applications.xlsx")
    ex.set_defaults(func=cmd_export)

    s = sub.add_parser("sample-resume", help="write a made-up resume to try JobPilot with")
    s.add_argument("--out", default="samples")
    s.set_defaults(func=cmd_sample)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
