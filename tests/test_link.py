"""Adding a job from a link."""

import httpx
import pytest

from jobpilot.core import db
from jobpilot.core.http import PoliteClient
from jobpilot.sources.link import LinkError, add_link, find_job

GH = {"id": 42, "title": "Data Intern", "company_name": "Acme", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/42",
      "location": {"name": "Boston, MA"}, "content": "&lt;p&gt;Python and SQL.&lt;/p&gt;", "first_published": "2026-11-01"}


def client(handler):
    def route(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return handler(request)
    return PoliteClient("test", 0.5, transport=httpx.MockTransport(route), sleep=lambda s: None)


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        yield c


def test_greenhouse_link_reads_the_posting(conn):
    http = client(lambda r: httpx.Response(200, json=GH))
    job_id, new = add_link(conn, http, "https://job-boards.greenhouse.io/acme/jobs/42")
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert new and job["title"] == "Data Intern" and job["company"] == "Acme" and "Python" in job["description"]
    assert job["status"] == "matched"  # you picked it, so it's on your review list
    again = add_link(conn, http, "https://job-boards.greenhouse.io/acme/jobs/42?utm_source=x")
    assert again == (job_id, False)


def test_other_sites_need_title_and_company(conn):
    with pytest.raises(LinkError, match="title and company"):
        add_link(conn, None, "https://careers.example.com/jobs/7")
    job_id, new = add_link(conn, None, "https://careers.example.com/jobs/7", title="Analyst", company="Example Co")
    assert new and find_job(conn, "https://careers.example.com/jobs/7")["id"] == job_id


def test_bad_links_and_unreadable_postings(conn):
    with pytest.raises(LinkError, match="web link"):
        add_link(conn, None, "not a link")
    http = client(lambda r: httpx.Response(404))
    with pytest.raises(LinkError, match="Type the job title"):
        add_link(conn, http, "https://job-boards.greenhouse.io/acme/jobs/43")
    job_id, _ = add_link(conn, http, "https://job-boards.greenhouse.io/acme/jobs/43", title="X", company="Acme")
    assert job_id
