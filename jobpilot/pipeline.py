"""`jobpilot run` (the daily pipeline) and `jobpilot doctor` (setup check).

`run` chains the steps you'd otherwise type one by one:
discover -> enrich -> match -> prepare -> load real forms. It never fills or submits anything;
it ends with jobs waiting for your review in the dashboard.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from jobpilot.config import Paths

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "[ok]  ", WARN: "[!]   ", FAIL: "[x]   "}


def run_pipeline(home: str | None, *, skip_discover: bool = False, enrich_limit: int = 50, forms_limit: int = 25,
                 main: Callable[[list[str]], int] | None = None, log: Callable[[str], None] = print) -> int:
    """Run each step through the normal CLI so behavior (rate limits, robots.txt, gates) is identical."""
    if main is None:
        from jobpilot.cli import main
    base = ["--home", home] if home else []
    steps = [
        ("Discover new jobs", ["discover"]),
        ("Fetch missing descriptions", ["enrich", "--limit", str(enrich_limit)]),
        ("Score them against your profile", ["match"]),
        ("Sort into Ready to fill / Needs human / Custom site", ["prepare"]),
        ("Load real application questions", ["forms", "fetch", "--limit", str(forms_limit)]),
    ]
    if skip_discover:
        steps = steps[1:]
    failed = []
    for i, (title, argv) in enumerate(steps, 1):
        log(f"\n== {i}/{len(steps)} {title} (jobpilot {' '.join(argv)})")
        try:
            code = main(base + argv)
        except SystemExit as exc:  # a step that exits (e.g. no profile) stops the run
            code = int(exc.code or 0)
        if code:
            failed.append(argv[0])
            if argv[0] in ("match", "prepare"):  # later steps depend on these
                log(f"Stopped: `jobpilot {argv[0]}` failed.")
                return code
    log("\nDone. Nothing was filled or sent. Review the queue with `jobpilot dashboard`.")
    if failed:
        log(f"Some steps reported problems: {', '.join(failed)}. Their output is above.")
    return 1 if failed else 0


# ---------------------------------------------------------------- doctor

def _git_ignored(path: Path) -> bool | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        top = subprocess.run([git, "rev-parse", "--show-toplevel"], cwd=path.parent if path.is_file() else path,
                             capture_output=True, text=True, timeout=10)
        if top.returncode:
            return None  # not inside a git repo: nothing to commit by accident
        r = subprocess.run([git, "check-ignore", "-q", str(path)], cwd=top.stdout.strip(), capture_output=True,
                           timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def _browser_check(channel: str) -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return FAIL, "Playwright isn't installed: pip install -e ."
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(channel=None if channel == "chromium" else channel, headless=True)
            b.close()
        return OK, f"{channel} starts"
    except Exception as exc:
        hint = ("python -m playwright install chromium" if channel == "chromium"
                else f"install {channel}, or set autofill.browser_channel in settings.yaml")
        return FAIL, f"{channel} didn't start ({str(exc).splitlines()[0][:80]}). Fix: {hint}"


def doctor(paths: Paths, *, check_browser: bool = True, log: Callable[[str], None] = print) -> int:
    from jobpilot.core import db
    from jobpilot.onboarding.profile import load_profile
    from jobpilot.settings import load_settings
    from jobpilot.sources.companies import load_companies

    results: list[tuple[str, str, str]] = []

    def add(status, name, detail):
        results.append((status, name, detail))

    v = sys.version_info
    add(OK if v >= (3, 11) else FAIL, "Python", f"{v.major}.{v.minor}" + ("" if v >= (3, 11) else ": needs 3.11+"))
    add(OK, "Data folder", str(paths.home))

    try:
        profile = load_profile(paths.profile)
    except Exception as exc:  # bad YAML or a field that doesn't validate
        profile = None
        add(FAIL, "Profile", f"profile.yaml doesn't load: {str(exc).splitlines()[0][:100]}. "
                             "Fix it with `jobpilot profile edit`.")
    else:
        if profile is None:
            add(FAIL, "Profile", "none yet. Run `jobpilot onboard`.")
        else:
            missing = [k for k, val in (("name", profile.full_name), ("email", profile.contact.email)) if not val]
            add(WARN if missing else OK, "Profile",
                f"missing {', '.join(missing)}: run `jobpilot profile edit`" if missing else profile.full_name)

    for name, path in (("Profile file", paths.profile), ("Database", paths.db), ("Resumes", paths.resumes)):
        if not path.exists():
            continue
        ign = _git_ignored(path)
        if ign is False:
            add(FAIL, f"{name} privacy", f"{path} is inside a git repo and NOT gitignored. Add it to .gitignore.")
    if not any(r[1].endswith("privacy") for r in results):
        add(OK, "Privacy", "personal files aren't tracked by git")

    try:
        with db.connect(paths.db) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            resume = db.get_resume(conn)
            counts = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        add(OK, "Database", f"schema v{version}, {sum(counts.values())} jobs")
        if resume is None:
            add(FAIL, "Resume", "none added. Run `jobpilot resume add FILE --label general --default`.")
        elif not Path(resume["path"]).is_file():
            add(WARN, "Resume", f"default '{resume['label']}' points to a missing file: {resume['path']}")
        else:
            add(OK, "Resume", f"default '{resume['label']}'")
    except Exception as exc:
        add(FAIL, "Database", str(exc)[:120])

    try:
        settings = load_settings(paths.settings)
        add(OK, "Settings", f"engine {settings.matching.engine}, "
                            f"{settings.min_seconds_between_requests}s between requests")
    except Exception as exc:
        settings = None
        add(FAIL, "Settings", f"settings.yaml doesn't load: {str(exc).splitlines()[0][:100]}")

    if settings is not None and settings.matching.engine == "claude":
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        try:
            import anthropic  # noqa: F401
            has_sdk = True
        except ImportError:
            has_sdk = False
        if has_key and has_sdk:
            add(OK, "Claude engine", "API key and SDK found")
        else:
            add(WARN, "Claude engine", ("no ANTHROPIC_API_KEY in .env" if not has_key else "pip install -e .[claude]")
                + "; matching falls back to the free local engine")

    companies = load_companies(paths.companies)
    add(OK if companies else WARN, "Company boards",
        f"{len(companies)} in companies.yaml" if companies else "none yet. Run `jobpilot companies init`.")

    if check_browser and settings is not None:
        st, detail = _browser_check(settings.autofill.browser_channel)
        add(st, "Autofill browser", detail)

    width = max(len(r[1]) for r in results)
    for st, name, detail in results:
        log(f"{MARK[st]}{name:<{width}}  {detail}")
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    log("\nAll good." if not fails and not warns else f"\n{fails} problem(s), {warns} warning(s).")
    return 1 if fails else 0
