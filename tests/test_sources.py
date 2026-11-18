import pytest

from jobpilot.settings import GitHubList, Settings
from jobpilot.sources.ashby import AshbySource
from jobpilot.sources.base import Company
from jobpilot.sources.companies import load_companies, load_seed, save_companies, starter_companies
from jobpilot.sources.csv_import import parse_csv
from jobpilot.sources.github_lists import GitHubListsSource, parse_readme_table
from jobpilot.sources.greenhouse import GreenhouseSource
from jobpilot.sources.lever import LeverSource
from jobpilot.sources.rss import parse_feed
from jobpilot.sources.usajobs import USAJobsSource
from tests import fakes

COMPANIES = [
    Company("Acme Robotics", "greenhouse", "acmerobotics", ["software"]),
    Company("Blue Owl", "lever", "blueowl", ["software"]),
    Company("Zebra", "ashby", "zebra", ["data"]),
]


def _run(cls, settings=None, routes=fakes.ROUTES, companies=COMPANIES):
    src = cls(fakes.make_client(routes), settings or Settings(), None, companies)
    return list(src.fetch())


def test_greenhouse():
    [res] = _run(GreenhouseSource)
    assert res.scope == "acmerobotics" and res.complete and not res.error
    j = res.jobs[0]
    assert (j.source_id, j.company, j.locations) == ("111", "Acme Robotics", ["Chicago, IL"])
    assert j.role_type == "internship" and j.date_posted == "2026-11-01"
    assert "Python" in j.description and "<" not in j.description
    assert res.jobs[1].remote == "remote"


def test_lever():
    [res] = _run(LeverSource)
    a, b = res.jobs
    assert a.role_type == "full-time" and a.remote == "hybrid" and a.date_posted
    assert "Kubernetes" in a.description and "Requirements" in a.description
    assert b.role_type == "co-op"


def test_ashby_skips_unlisted():
    [res] = _run(AshbySource)
    assert [j.source_id for j in res.jobs] == ["z-1"]
    j = res.jobs[0]
    assert j.locations == ["San Francisco, CA", "Remote"] and j.remote == "hybrid" and j.role_type == "internship"


def test_board_error_is_reported_not_raised():
    bad = [Company("Nope", "greenhouse", "doesnotexist")]
    [res] = _run(GreenhouseSource, companies=bad)
    assert res.error and not res.complete and res.jobs == []


def test_board_source_disabled_without_companies():
    src = GreenhouseSource(fakes.make_client({}), Settings(), None, [])
    assert src.enabled() == (False, "no greenhouse companies in companies.yaml")


def test_github_lists_json():
    s = Settings()
    s.sources.github_lists.lists = [GitHubList(repo="fake/Interns", kind="internship")]
    [res] = _run(GitHubListsSource, settings=s)
    by_id = {j.source_id: j for j in res.jobs}
    assert by_id["s1"].role_type == "internship" and by_id["s1"].field == "Software"
    assert by_id["s3"].active is False


def test_github_lists_readme_fallback():
    s = Settings()
    s.sources.github_lists.lists = [GitHubList(repo="fake/Readme", kind="internship")]
    routes = {"https://raw.githubusercontent.com/fake/Readme/dev/README.md": fakes.README_MD}
    [res] = _run(GitHubListsSource, settings=s, routes=routes)
    assert [(j.company, j.title, j.url) for j in res.jobs] == [
        ("Fern Co", "Software Intern", "https://jobs.lever.co/fern/1"),
        ("Fern Co", "Data Intern", "https://jobs.lever.co/fern/2"),
    ]


def test_readme_html_table():
    html = """<table><tr><th>Company</th></tr>
    <tr><td><strong><a href="https://x.com">Oak</a></strong></td><td>SWE Intern</td><td>Austin, TX</br>Remote</td>
    <td><a href="https://jobs.ashbyhq.com/oak/1">Apply</a></td><td>1d</td></tr></table>"""
    [j] = list(parse_readme_table(html, "internship"))
    assert (j.company, j.url, j.locations) == ("Oak", "https://jobs.ashbyhq.com/oak/1", ["Austin, TX", "Remote"])


def test_rss_and_atom():
    [r] = list(parse_feed(fakes.RSS))
    assert (r.company, r.title, r.description, r.date_posted) == ("Birch Careers", "Frontend Intern", "React", "2026-11-10")
    [a] = list(parse_feed(fakes.ATOM, company="Cedar"))
    assert (a.company, a.url, a.role_type) == ("Cedar", "https://cedar.example.com/j/7", "internship")


def test_usajobs_needs_key(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    s = Settings()
    s.sources.usajobs.enabled = True
    s.sources.usajobs.keywords = ["intern"]
    src = USAJobsSource(fakes.make_client({}), s, None, [])
    assert src.enabled()[0] is False


def test_usajobs_parse(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "test-key")
    monkeypatch.setenv("USAJOBS_EMAIL", "someone@example.com")
    s = Settings()
    s.sources.usajobs.enabled = True
    s.sources.usajobs.keywords = ["information technology"]
    [res] = _run(USAJobsSource, settings=s, routes={"https://data.usajobs.gov/api/search": fakes.USAJOBS})
    [j] = res.jobs
    assert res.complete is False  # keyword search never closes jobs
    assert j.company == "Office of Science - Department of Energy" and j.role_type == "part-time"
    assert j.apply_url.endswith("/apply")


def test_csv_import(tmp_path):
    f = tmp_path / "handshake.csv"
    f.write_text("Job Title,Employer,Link,Location,Employment Type\n"
                 "Data Intern,Spruce,https://spruce.example.com/1,Remote,Internship\n"
                 ",Missing Title,https://x,,\n", encoding="utf-8")
    res = parse_csv(f)
    assert res.complete is False
    [j] = res.jobs
    assert (j.title, j.company, j.remote, j.role_type) == ("Data Intern", "Spruce", "remote", "internship")


def test_csv_missing_columns(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        parse_csv(f)


def test_seed_companies_and_roundtrip(tmp_path):
    seed = load_seed()
    assert len(seed) > 50 and {c.ats for c in seed} == {"greenhouse", "lever", "ashby"}
    quant = starter_companies(["quantitative trading"])
    assert quant and all("quant" in c.fields for c in quant)
    assert len(starter_companies([])) == len(seed)
    path = tmp_path / "companies.yaml"
    save_companies(quant, path)
    assert load_companies(path) == quant
