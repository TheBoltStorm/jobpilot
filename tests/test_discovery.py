from datetime import date

import pytest

from jobpilot.core import db
from jobpilot.discovery import Filter, run_discovery, store_scope
from jobpilot.onboarding.profile import Profile, Targets
from jobpilot.settings import GitHubList, Settings
from jobpilot.sources.base import RawJob, ScopeResult
from jobpilot.sources.github_lists import GitHubListsSource
from jobpilot.sources.greenhouse import GreenhouseSource
from tests import fakes
from tests.test_sources import COMPANIES

INTERN_SWE = Profile(targets=Targets(fields=["software engineering"], role_types=["internship"]))


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        yield c


def _settings():
    s = Settings(max_age_days=60)
    s.sources.github_lists.lists = [GitHubList(repo="fake/Interns")]
    return s


def _sources(settings, profile, routes=fakes.ROUTES):
    http = fakes.make_client(routes)
    return [cls(http, settings, profile, COMPANIES) for cls in (GreenhouseSource, GitHubListsSource)]


def _jobs(conn):
    return {r["title"]: r for r in conn.execute("SELECT * FROM jobs")}


def test_filter_and_cross_source_dedupe(conn, monkeypatch):
    monkeypatch.setattr("jobpilot.discovery.date", type("D", (), {"today": staticmethod(lambda: date(2026, 11, 17))}))
    s = _settings()
    run_discovery(conn, _sources(s, INTERN_SWE), s, INTERN_SWE, log=lambda m: None)
    jobs = _jobs(conn)
    # Kept: Greenhouse SWE intern (merged with the GitHub list copy) and Maple's Workday intern.
    # Dropped: account exec (not an internship), old data-science post (too old + not software),
    # hardware intern (not a target field), Old Co (list says inactive).
    assert set(jobs) == {"Software Engineer Intern, Summer 2027", "Software Engineer Intern"}
    acme = jobs["Software Engineer Intern, Summer 2027"]
    assert acme["ats"] == "greenhouse" and "Python" in acme["description"]
    links = conn.execute("SELECT source FROM job_sources WHERE job_id = ?", (acme["id"],)).fetchall()
    assert sorted(r[0] for r in links) == ["github_lists", "greenhouse"]
    assert jobs["Software Engineer Intern"]["ats"] == "workday"


def test_no_profile_keeps_everything_recent(conn):
    s = _settings()
    s.max_age_days = 0
    run_discovery(conn, _sources(s, None), s, None, log=lambda m: None)
    titles = set(_jobs(conn))
    assert "Senior Account Executive" in titles and "Hardware Intern" in titles
    assert "Software Intern" not in titles  # inactive on the list


def test_rerun_is_idempotent(conn):
    s = _settings()
    for _ in range(2):
        summaries = run_discovery(conn, _sources(s, INTERN_SWE), s, INTERN_SWE, log=lambda m: None)
    assert sum(x.new for x in summaries) == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def _raw(i, title="Software Engineer Intern", company="Acme"):
    return RawJob(source_id=str(i), title=title, company=company, url=f"https://jobs.lever.co/acme/{i}",
                  locations=["Remote"], role_type="internship")


def test_missing_jobs_closed_and_reopened(conn):
    flt = Filter()
    store_scope(conn, "lever", ScopeResult("acme", [_raw(1), _raw(2, "Data Intern")]), flt)
    s = store_scope(conn, "lever", ScopeResult("acme", [_raw(1)]), flt)
    assert s.closed == 1
    status = dict(conn.execute("SELECT title, status FROM jobs").fetchall())
    assert status == {"Software Engineer Intern": "found", "Data Intern": "posting_closed"}
    store_scope(conn, "lever", ScopeResult("acme", [_raw(1), _raw(2, "Data Intern")]), flt)
    assert conn.execute("SELECT status FROM jobs WHERE title = 'Data Intern'").fetchone()[0] == "found"


def test_incomplete_or_failed_scope_closes_nothing(conn):
    flt = Filter()
    store_scope(conn, "lever", ScopeResult("acme", [_raw(1), _raw(2, "Data Intern")]), flt)
    assert store_scope(conn, "lever", ScopeResult("acme", [], complete=False), flt).closed == 0
    assert store_scope(conn, "lever", ScopeResult("acme", [], error="HTTP 500"), flt).closed == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'found'").fetchone()[0] == 2


def test_job_open_elsewhere_or_acted_on_is_not_closed(conn):
    flt = Filter()
    store_scope(conn, "lever", ScopeResult("acme", [_raw(1), _raw(2, "Data Intern")]), flt)
    # Same job 1 also listed on a GitHub list.
    store_scope(conn, "github_lists", ScopeResult("repo", [_raw(1)]), flt)
    conn.execute("UPDATE jobs SET status = 'approved' WHERE title = 'Data Intern'")
    store_scope(conn, "lever", ScopeResult("acme", []), flt)
    status = dict(conn.execute("SELECT title, status FROM jobs").fetchall())
    assert status == {"Software Engineer Intern": "found", "Data Intern": "approved"}


def test_unknown_location_matches_known_one(conn):
    flt = Filter()
    a = _raw(1)
    a.locations = ["Austin, TX"]
    b = RawJob(source_id="x", title="Software Engineer Intern (2027)", company="Acme Inc", url="https://go.example/x",
               locations=["In-Office"], role_type="internship")
    store_scope(conn, "lever", ScopeResult("acme", [a]), flt)
    s = store_scope(conn, "github_lists", ScopeResult("repo", [b]), flt)
    assert (s.new, s.merged) == (0, 1)


def test_disabled_source_logged(conn):
    s = _settings()
    s.sources.greenhouse.enabled = False
    logs = []
    run_discovery(conn, _sources(s, None)[:1], s, None, log=logs.append)
    assert logs == ["- Greenhouse job boards: skipped (disabled in settings.yaml)"]
