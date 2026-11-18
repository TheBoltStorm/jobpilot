"""Locations (region / area) and the minimum-requirements check."""

import json

import pytest

from jobpilot.core import db, places
from jobpilot.core.places import classify
from jobpilot.matching import minimums
from jobpilot.matching.minimums import evaluate
from tests.test_review import add_job, make_profile


def areas(text, remote=""):
    return [(p.region, p.area) for p in classify(text, remote)]


@pytest.mark.parametrize("text,expected", [
    ("San Jose, CA", [("US West", "SF Bay Area")]),
    ("SF", [("US West", "SF Bay Area")]),
    ("NYC", [("US Northeast", "New York City area")]),
    ("Cambridge, MA", [("US Northeast", "Boston area")]),
    ("Cambridge, UK", [("Europe", "Cambridge, United Kingdom")]),
    ("Indianapolis, IN", [("US Midwest", "Indianapolis, IN")]),  # IN is Indiana, not "in"
    ("Hillsboro, OR; Phoenix, AZ", [("US West", "Hillsboro, OR"), ("US West", "Phoenix area")]),  # OR is Oregon
    ("Toronto, ON, Canada", [("Canada", "Toronto area")]),
    ("Dublin, IE", [("Europe", "Dublin, Ireland")]),
    ("Remote in USA", [("Remote", "Remote (United States)")]),
    ("Remote; United States", [("Remote", "Remote (United States)")]),
    ("San Jose, CA; United States", [("US West", "SF Bay Area")]),  # bare country adds nothing
    ("New York, Seattle, South San Francisco HQ",
     [("US Northeast", "New York City area"), ("US West", "Seattle area"), ("US West", "SF Bay Area")]),
    ("", [("Unknown", "Location not listed")]),
])
def test_classify(text, expected):
    assert areas(text) == expected


def test_remote_flag_adds_remote_place():
    assert ("Remote", "Remote") in areas("", "remote")


def test_refresh_and_filter(paths):
    with db.connect(paths.db) as conn:
        a = add_job(conn, title="A")
        b = add_job(conn, title="B")
        conn.execute("UPDATE jobs SET location = 'Palo Alto, CA' WHERE id = ?", (a,))
        conn.execute("UPDATE jobs SET location = 'Austin, TX' WHERE id = ?", (b,))
        conn.commit()
        assert places.refresh(conn) == 2 and places.refresh(conn) == 0
        sql, params = places.sql_filter("US West", "")
        ids = [r[0] for r in conn.execute(f"SELECT j.id FROM jobs j WHERE {sql}", params)]
        assert ids == [a]
        regions = {r for r, _, _ in places.facets(conn)}
        assert {"US West", "US South"} <= regions
        conn.execute("UPDATE jobs SET location = 'Boston, MA' WHERE id = ?", (a,))
        conn.commit()
        assert places.refresh(conn) == 1  # a changed location is redone


# ---------------------------------------------------------------- minimums

DESC = """About the role
We build things.

Requirements
- Pursuing a Bachelor's degree in Computer Science, graduating between December 2026 and June 2027
- Experience with Python and SQL
- Minimum GPA of 3.0
"""


def job(desc=DESC, title="Software Engineer Intern"):
    return {"title": title, "description": desc}


def skill(name, status, importance="required", evidence=""):
    return {"key": name.lower(), "name": name, "status": status, "importance": importance, "evidence": evidence}


@pytest.fixture
def profile():
    p = make_profile()
    p.education[0].end = "May 2027"
    p.education[0].gpa = "3.6"
    return p


def test_meets_every_minimum(profile):
    v = evaluate(job(), profile, [skill("Python", "have"), skill("SQL", "have")])
    assert v.status == "qualify", v.checks
    assert {c.what for c in v.checks} >= {"Python", "SQL", "Graduation date", "GPA 3+"}


def test_one_missing_skill_is_close(profile):
    v = evaluate(job(), profile, [skill("Python", "have"), skill("Go", "missing")])
    assert v.status == "close" and [c.what for c in v.unmet] == ["Go"]


def test_related_skill_does_not_count(profile):
    v = evaluate(job(), profile, [skill("Python", "have"), skill("Kotlin", "partial", evidence="Related: Java (x)")])
    assert v.status == "close"
    coursework = evaluate(job(), profile, [skill("Python", "have"),
                                           skill("Statistics", "partial", evidence="Coursework: Statistics")])
    assert coursework.status == "qualify"


def test_years_grad_and_gpa(profile):
    desc = "Requirements\n- 5+ years of professional experience\n- Graduating in 2029\n- GPA 3.8 or higher\n"
    v = evaluate(job(desc), profile, [skill("Python", "have")])
    unmet = {c.what for c in v.unmet}
    assert unmet == {"5+ years of experience", "Graduation date", "GPA 3.8+"} and v.status == "not"


def test_grad_degree_requirement(profile):
    desc = "Requirements\n- Currently pursuing a PhD in Machine Learning\n- Python\n"
    v = evaluate(job(desc, "Research Intern"), profile, [skill("Python", "have")])
    assert any(c.what == "Degree level" and c.ok is False for c in v.checks)


def test_sponsorship_is_a_note_not_a_verdict(profile):
    desc = DESC + "\nWe are unable to sponsor visas for this role.\n"
    v = evaluate(job(desc), profile, [skill("Python", "have"), skill("SQL", "have")])
    assert v.status == "qualify" and any("sponsor" in n for n in v.notes)


def test_too_little_to_judge(profile):
    assert evaluate(job(""), profile, []).status == "unknown"
    one = evaluate(job("Requirements\n- Git\n"), profile, [skill("Git", "have")])
    assert one.status == "unknown"  # one requirement isn't enough to say you meet the minimums


def test_experience_years_counts_overlap_once(profile):
    from jobpilot.onboarding.profile import Experience

    profile.experience = [Experience(title="A", company="X", start="Jan 2024", end="Dec 2024"),
                          Experience(title="B", company="Y", start="Jun 2024", end="Dec 2025")]
    assert minimums.experience_years(profile) == 2.0


def test_refresh_redoes_when_profile_changes(paths, profile):
    with db.connect(paths.db) as conn:
        j = add_job(conn, description=DESC)
        conn.execute("UPDATE matches SET skills_json = ? WHERE job_id = ?",
                     (json.dumps({"skills": [skill("Python", "have"), skill("SQL", "have")]}), j))
        conn.commit()
        assert minimums.refresh(conn, profile) == 1 and minimums.refresh(conn, profile) == 0
        assert minimums.load(conn, j)["status"] == "qualify"
        profile.education[0].gpa = "2.5"
        assert minimums.refresh(conn, profile) == 1
        assert minimums.load(conn, j)["status"] == "close"


# ---------------------------------------------------------------- dashboard

def test_dashboard_place_filter_and_qualify_page(paths):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    p = make_profile()
    p.education[0].end = "May 2027"
    save_profile(p, paths.profile)
    with db.connect(paths.db) as conn:
        good = add_job(conn, title="Data Intern", description=DESC)
        far = add_job(conn, title="Ops Intern", description=DESC)
        conn.execute("UPDATE jobs SET location = 'Austin, TX' WHERE id = ?", (good,))
        conn.execute("UPDATE jobs SET location = 'London, UK' WHERE id = ?", (far,))
        conn.execute("UPDATE matches SET skills_json = ?",
                     (json.dumps({"skills": [skill("Python", "have"), skill("SQL", "have")]}),))
        conn.commit()
    client = TestClient(create_app(paths))
    r = client.get("/jobs?region=US+South")
    assert r.status_code == 200 and "Data Intern" in r.text and "Ops Intern" not in r.text
    assert "Austin area" in r.text
    r = client.get("/qualify")
    assert r.status_code == 200 and "Data Intern" in r.text and "Ops Intern" in r.text
    r = client.get("/qualify?area=London%2C+United+Kingdom")
    assert "Ops Intern" in r.text and "Data Intern" not in r.text
    r = client.get(f"/jobs/{good}")
    assert "Minimum requirements" in r.text and "you meet them all" in r.text
    assert client.get("/").status_code == 200
