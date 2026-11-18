import pytest

from jobpilot.core import db
from jobpilot.enrich import enrich_descriptions, target_for
from jobpilot.sources.base import Company
from tests import fakes

UUID = "19dbac7d-b4fb-4d21-9247-dc610bf55fed"


@pytest.mark.parametrize("ats,url,company,api", [
    ("greenhouse", "https://job-boards.greenhouse.io/scaleai/jobs/4735665005", "Scale AI",
     "https://boards-api.greenhouse.io/v1/boards/scaleai/jobs/4735665005"),
    ("greenhouse", "https://job-boards.eu.greenhouse.io/acme/jobs/9", "Acme",
     "https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs/9"),
    ("greenhouse", "https://careers.upstart.com/jobs?gh_jid=8213476", "Upstart",
     "https://boards-api.greenhouse.io/v1/boards/upstart/jobs/8213476"),
    ("greenhouse", "https://stripe.com/jobs/search?gh_jid=1", "Stripe, Inc.",
     "https://boards-api.greenhouse.io/v1/boards/stripejobs/jobs/1"),  # token from companies.yaml
    ("lever", f"https://jobs.lever.co/acronaviation/{UUID}/apply", "Acron",
     f"https://api.lever.co/v0/postings/acronaviation/{UUID}?mode=json"),
    ("ashby", f"https://jobs.ashbyhq.com/collinear-ai/{UUID}/application?embed=true", "Collinear",
     "https://api.ashbyhq.com/posting-api/job-board/collinear-ai"),
])
def test_target_for(ats, url, company, api):
    t = target_for(ats, url, company, {"stripe": "stripejobs"})
    assert t.api_url == api


def test_target_for_unknown():
    assert target_for("workday", "https://x.myworkdayjobs.com/a", "X", {}) is None
    assert target_for("lever", "https://jobs.lever.co/acme", "Acme", {}) is None


def test_enrich(paths):
    lever_id = "abc-1"
    routes = {
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/5": {"content": "&lt;p&gt;Use Python.&lt;/p&gt;"},
        f"https://api.lever.co/v0/postings/blueowl/{UUID}": {"descriptionPlain": "Go and Postgres.", "lists": []},
        "https://api.ashbyhq.com/posting-api/job-board/zebra": {"jobs": [{"id": UUID, "descriptionPlain": "PyTorch"}]},
    }
    rows = [
        (1, "greenhouse", "https://boards.greenhouse.io/acme/jobs/5"),
        (2, "lever", f"https://jobs.lever.co/blueowl/{UUID}"),
        (3, "ashby", f"https://jobs.ashbyhq.com/zebra/{UUID}"),
        (4, "greenhouse", "https://boards.greenhouse.io/acme/jobs/404"),
        (5, "workday", "https://x.myworkdayjobs.com/job/1"),
    ]
    with db.connect(paths.db) as conn:
        for i, ats, url in rows:
            conn.execute("""INSERT INTO jobs (id, dedupe_key, company_key, url_key, title, company, url, ats,
                            date_found, last_seen) VALUES (?, ?, 'acme', ?, 'Intern', 'Acme', ?, ?, 'n', 'n')""",
                         (i, f"k{i}", url, url, ats))
        conn.commit()
        s = enrich_descriptions(conn, fakes.make_client(routes), [Company("Acme", "greenhouse", "acme")],
                                log=lambda m: None)
        assert (s.attempted, s.filled, s.failed) == (4, 3, 1)
        got = dict(conn.execute("SELECT id, description FROM jobs").fetchall())
        assert got[1] == "Use Python." and got[2] == "Go and Postgres." and got[3] == "PyTorch"
        status = dict(conn.execute("SELECT id, enrich_status FROM jobs").fetchall())
        assert status == {1: "ok", 2: "ok", 3: "ok", 4: "failed", 5: ""}
        # Failed ones aren't retried unless asked.
        again = enrich_descriptions(conn, fakes.make_client(routes), [], log=lambda m: None)
        assert again.attempted == 0
