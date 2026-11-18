import pytest

from jobpilot.sources.dedupe import canonical_url, company_key, dedupe_key, location_key, title_key, titles_match
from jobpilot.sources.fields import job_tags, tags_for
from jobpilot.sources.normalize import classify_remote, classify_role_type, detect_ats, html_to_text, to_date


def test_html_to_text_handles_double_escaped_greenhouse():
    raw = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;"
    assert html_to_text(raw) == "Hello & welcome\n\n• Python\n\n• SQL"


def test_html_to_text_drops_scripts():
    assert html_to_text("<p>a</p><script>alert(1)</script><style>x{}</style>") == "a"


@pytest.mark.parametrize("title,hint,expected", [
    ("Software Engineer Intern", "", "internship"),
    ("Software Engineering Internship - Summer 2027", "Full-time", "internship"),
    ("Mechanical Co-op (Fall)", "", "co-op"),
    ("Software Engineer", "Full-time", "full-time"),
    ("Barista", "Part-time", "part-time"),
    ("Software Engineer", "FullTime", "full-time"),
    ("Software Engineer", "", ""),
    ("Summer Analyst, Investment Banking", "", "internship"),
    ("Internal Tools Engineer", "", ""),
])
def test_role_type(title, hint, expected):
    assert classify_role_type(title, hint) == expected


def test_remote():
    assert classify_remote(["Remote - US"]) == "remote"
    assert classify_remote(["Austin, TX"], "hybrid") == "hybrid"
    assert classify_remote(["Austin, TX"]) == ""


@pytest.mark.parametrize("url,ats", [
    ("https://job-boards.greenhouse.io/x/jobs/1", "greenhouse"),
    ("https://careers.airbnb.com/positions/1?gh_jid=1", "greenhouse"),
    ("https://jobs.lever.co/x/1", "lever"),
    ("https://jobs.ashbyhq.com/x/1", "ashby"),
    ("https://acme.wd5.myworkdayjobs.com/en-US/x", "workday"),
    ("https://example.com/careers", "other"),
    ("", ""),
])
def test_detect_ats(url, ats):
    assert detect_ats(url) == ats


def test_to_date():
    assert to_date(1793600000) == "2026-11-02"
    assert to_date(1793500000000) == "2026-11-01"  # milliseconds
    assert to_date("2026-11-05T00:00:00.000+00:00") == "2026-11-05"
    assert to_date("Mon, 10 Nov 2026 12:00:00 GMT") == "2026-11-10"
    assert to_date("not a date") == ""
    assert to_date(None) == ""


def test_canonical_url():
    a = canonical_url("https://job-boards.greenhouse.io/acme/jobs/111?utm_source=x")
    b = canonical_url("https://boards.greenhouse.io/acme/jobs/111/?gh_src=simplify")
    assert a == b == "https://boards.greenhouse.io/acme/jobs/111"
    assert canonical_url("https://careers.x.com/p/1?gh_jid=1&utm=2") == "https://careers.x.com/p/1?gh_jid=1"
    assert canonical_url("https://jobs.lever.co/x/1/apply") == "https://jobs.lever.co/x/1"


def test_company_and_title_keys():
    assert company_key("Acme Robotics, Inc.") == company_key("acme robotics") == "acmerobotics"
    assert company_key("AT&T") == company_key("AT and T")
    assert title_key("Software Engineering Intern (Summer 2027)") == title_key("Software Engineer Internship")
    assert title_key("SWE Intern - Remote") == "software engineer intern"


def test_titles_match_but_different_roles_dont():
    assert titles_match("Software Engineer Intern, Summer 2027", "Software Engineering Intern")
    assert not titles_match("Software Engineer Intern", "Data Science Intern")
    assert not titles_match("Software Engineer I", "Software Engineer II")
    assert not titles_match("Senior Software Engineer", "Software Engineer")
    assert titles_match("Software Engineer, Backend", "Software Engineer - Backend")


def test_dedupe_key_location():
    assert dedupe_key("Acme", "SWE Intern", ["New York, NY"]) == dedupe_key("Acme Inc", "Software Engineer Intern", ["NYC"])
    assert dedupe_key("Acme", "SWE Intern", ["Chicago, IL"]) != dedupe_key("Acme", "SWE Intern", ["Austin, TX"])
    assert location_key(["In-Office"]) == location_key(["Multiple Locations"]) == ""
    assert location_key(["In-Office", "Austin, TX"]) == "austin"


def test_field_tags():
    assert tags_for(["software engineering", "data science"]) >= {"software", "data"}
    assert job_tags("Hardware Intern", "Hardware") >= {"hardware"}
    assert job_tags("Member of Technical Staff", "") == {"software"}
    assert job_tags("Senior Account Executive", "Sales") == set()
