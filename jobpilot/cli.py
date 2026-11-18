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
    jl.add_argument("--limit", type=int, default=25)
    jl.add_argument("--all", action="store_true", help="include closed postings")
    jl.add_argument("--summary", action="store_true", help="also print counts by status and ATS")
    jsh = js.add_parser("show")
    jsh.add_argument("id", type=int)
    j.set_defaults(func=cmd_jobs)

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
