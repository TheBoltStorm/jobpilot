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
