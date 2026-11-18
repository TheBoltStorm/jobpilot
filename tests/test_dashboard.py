import pytest
from fastapi.testclient import TestClient

from jobpilot.core import db
from jobpilot.onboarding.profile import save_profile
from jobpilot.settings import load_settings
from jobpilot.web.app import create_app
from tests.test_review import add_job, make_profile


@pytest.fixture
def setup(paths):
    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as conn:
        db.upsert_resume(conn, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        job = add_job(conn, title="Backend Intern <script>alert(1)</script>")
        other = add_job(conn, company="Globex", ats="workday", score=60)
    app = create_app(paths)
    return TestClient(app), app.state.csrf_token, job, other, paths


def status(paths, job):
    with db.connect(paths.db) as conn:
        return conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0]


@pytest.mark.parametrize("url", ["/", "/jobs", "/jobs?status=matched&q=Acme&sort=date", "/gaps", "/runs", "/settings",
                                 "/jobs?min_score=50&described=1&field=software"])
def test_pages_render(setup, url):
    client, *_ = setup
    r = client.get(url)
    assert r.status_code == 200, r.text[:500]
    assert "never submits" in r.text


def test_review_page_escapes_and_shows_gate(setup):
    client, _, job, _, paths = setup
    r = client.get(f"/jobs/{job}")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
    assert "NEEDS_HUMAN" in r.text and "only you can answer" in r.text
    assert status(paths, job) == "needs_human"  # viewing prepares it


def test_review_redirects_to_best_job(setup):
    client, token, job, other, _ = setup
    client.post("/prepare", data={"csrf": token})
    r = client.get("/review", follow_redirects=False)
    assert r.headers["location"] == f"/jobs/{job}"


def test_post_needs_token(setup):
    client, token, job, _, paths = setup
    assert client.post(f"/jobs/{job}", data={"action": "approve", "confirm": "yes"}).status_code == 403
    assert client.post(f"/jobs/{job}", data={"action": "approve", "confirm": "yes", "csrf": "wrong"}).status_code == 403
    assert status(paths, job) == "matched"


def test_post_from_another_site_refused(setup):
    client, token, job, _, paths = setup
    data = {"action": "approve", "confirm": "yes", "csrf": token}
    assert client.post(f"/jobs/{job}", data=data, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(f"/jobs/{job}", data=data, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert status(paths, job) == "matched"


def test_other_hosts_refused(setup):
    client, *_ = setup
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_approve_needs_confirm_and_keeps_edits(setup):
    client, token, job, _, paths = setup
    r = client.post(f"/jobs/{job}", data={"csrf": token, "action": "approve", "fv_phone": "555-0100"})
    assert r.status_code == 400 and "Tick the box" in r.text
    assert status(paths, job) == "needs_human"
    assert 'value="555-0100"' in client.get(f"/jobs/{job}").text
    r = client.post(f"/jobs/{job}", data={"csrf": token, "action": "approve", "confirm": "yes"}, follow_redirects=False)
    assert r.status_code == 303 and status(paths, job) == "approved"
    # Marking submitted needs its own confirmation.
    r = client.post(f"/jobs/{job}", data={"csrf": token, "action": "submitted"})
    assert r.status_code == 400 and status(paths, job) == "approved"
    client.post(f"/jobs/{job}", data={"csrf": token, "action": "submitted", "confirm_submitted": "yes"})
    assert status(paths, job) == "submitted"


def test_reject_skip_reopen(setup):
    client, token, job, other, paths = setup
    client.post(f"/jobs/{job}", data={"csrf": token, "action": "reject", "reason": "not for me"})
    assert status(paths, job) == "rejected"
    client.post(f"/jobs/{job}", data={"csrf": token, "action": "reopen"})
    assert status(paths, job) == "needs_human"
    r = client.post(f"/jobs/{job}", data={"csrf": token, "action": "skip"}, follow_redirects=False)
    assert r.headers["location"].startswith("/review")


def test_drafts_round_trip(setup):
    client, token, job, _, paths = setup
    client.get(f"/jobs/{job}")
    client.post(f"/jobs/{job}", data={"csrf": token, "action": "save", "draft_count": "0",
                                      "dq_new": "Why Acme?", "dt_new": "I build logistics tools."})
    page = client.get(f"/jobs/{job}").text
    assert "Why Acme?" in page and "I build logistics tools." in page
    client.post(f"/jobs/{job}", data={"csrf": token, "action": "save", "draft_count": "1", "dq_0": "Why Acme?",
                                      "dt_0": "x", "dd_0": "1"})
    assert "Why Acme?" not in client.get(f"/jobs/{job}").text


def test_settings_validation_and_save(setup):
    client, token, *_, paths = setup
    s = load_settings(paths.settings)
    form = {"csrf": token, "user_agent": s.user_agent, "min_seconds_between_requests": "0.1",
            "request_timeout_seconds": "30", "max_age_days": "45", "min_score": "60", "engine": "local",
            "claude_model": "claude-haiku-5-5", "claude_concurrency": "4", "claude_max_jobs_per_run": "200",
            "claude_spend_cap_usd": "1", "enrich_per_run": "300", "src_greenhouse": "on",
            "github_lists": "SimplifyJobs/New-Grad-Positions dev new_grad\n", "rss_feeds": ""}
    r = client.post("/settings", data=form)
    assert r.status_code == 400 and "min_seconds_between_requests" in r.text
    assert load_settings(paths.settings).max_age_days == 60
    form["min_seconds_between_requests"] = "2"
    assert client.post("/settings", data=form, follow_redirects=False).status_code == 303
    new = load_settings(paths.settings)
    assert new.max_age_days == 45 and new.matching.min_score == 60 and new.min_seconds_between_requests == 2
    assert new.sources.greenhouse.enabled and not new.sources.lever.enabled
    assert [l.repo for l in new.sources.github_lists.lists] == ["SimplifyJobs/New-Grad-Positions"]


def test_exports(setup):
    client, token, job, *_ = setup
    client.get(f"/jobs/{job}")
    r = client.get("/export/applications.csv")
    assert r.status_code == 200 and "Acme" in r.text and "attachment" in r.headers["content-disposition"]
    r = client.get("/export/applications.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert client.get("/gaps/export.md").status_code == 200
    assert client.get("/gaps/export.csv").status_code == 200
