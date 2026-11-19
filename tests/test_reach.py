"""Apply early, skip dead postings, don't apply twice, sort fast, referrals, templates, backup."""

import zipfile
from datetime import date

import pytest

from jobpilot.core import db
from jobpilot.review import contacts, drafts, fresh, outcomes, plan, triage
from tests.fakes import make_client
from tests.test_outcomes import submitted
from tests.test_review import add_job, make_profile

TODAY = date(2026, 11, 18)


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


def job_row(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def test_new_postings(conn):
    j = add_job(conn)
    conn.execute("UPDATE jobs SET date_posted = '2026-11-17' WHERE id = ?", (j,))
    assert fresh.is_new(job_row(conn, j), TODAY) and fresh.age_label(job_row(conn, j), TODAY) == "posted 1 day ago"
    conn.execute("UPDATE jobs SET date_posted = '' WHERE id = ?", (j,))  # falls back to when JobPilot found it
    assert not fresh.is_new(job_row(conn, j), TODAY) and fresh.age_days(job_row(conn, j), TODAY) == 17


def test_still_open_on_any_site(conn):
    j = add_job(conn, ats="other")
    conn.execute("UPDATE jobs SET url = 'https://careers.example.com/jobs/1' WHERE id = ?", (j,))
    row = job_row(conn, j)
    assert fresh.still_open(make_client({"https://careers.example.com/jobs/1": "<h1>Apply now</h1>"}), row) == "open"
    shut = "<p>Sorry, this position is no longer accepting applications.</p>"
    assert fresh.still_open(make_client({"https://careers.example.com/jobs/1": shut}), row) == "closed"
    assert fresh.still_open(make_client({"https://careers.example.com/jobs/1": 404}), row) == "closed"
    assert fresh.still_open(make_client({"https://careers.example.com/jobs/1": 403}), row) == "unknown"
    robots = {"https://careers.example.com/robots.txt": "User-agent: *\nDisallow: /", "https://careers.example.com/": "x"}
    assert fresh.still_open(make_client(robots), row) == "unknown"  # robots.txt says no: we don't look


def test_still_open_on_greenhouse_uses_the_api(conn):
    j = add_job(conn)
    conn.execute("UPDATE jobs SET url = 'https://job-boards.greenhouse.io/acme/jobs/42' WHERE id = ?", (j,))
    row = job_row(conn, j)
    api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/42"
    gh = {"id": 42, "title": "Software Engineer Intern", "company_name": "Acme", "content": "", "location": {"name": "x"}}
    assert fresh.still_open(make_client({api: gh}), row) == "open"
    assert fresh.still_open(make_client({api: 404}), row) == "closed"


def test_closed_postings_leave_your_queue_but_never_your_approvals(conn):
    profile = make_profile()
    a, b = add_job(conn), add_job(conn, title="Data Intern")
    plan.prepare(conn, a, profile)
    plan.prepare(conn, b, profile)
    plan.approve(conn, b, profile)
    fresh.record_check(conn, a, "closed")
    fresh.record_check(conn, b, "closed")
    assert job_row(conn, a)["status"] == "posting_closed"
    assert job_row(conn, b)["status"] == "approved"  # yours: just a warning
    assert fresh.last_check(conn, b)[0] == "closed"
    fresh.record_check(conn, b, "open")
    assert fresh.last_check(conn, b)[0] == "open"


def test_reposts_and_applied_before(conn):
    first = submitted(conn, 30)
    conn.execute("UPDATE jobs SET title = 'Software Engineer Intern (Summer 2026)', company_key = 'acme' WHERE id = ?",
                 (first,))
    again = add_job(conn, title="Software Engineer Intern - Summer 2027", company="Acme")
    other = add_job(conn, title="Data Science Intern", company="Acme")
    assert fresh.role_key("Software Engineer Intern (Summer 2027) - New York") == "software engineer intern"
    assert [r["id"] for r in fresh.reposts(conn, again)] == [first]
    assert fresh.applied_before(conn, again)["id"] == first
    assert fresh.applied_before(conn, other) is None


def test_sorting_puts_new_first_and_records_your_choice(conn):
    profile = make_profile()
    old = add_job(conn, title="Old", score=95)
    new = add_job(conn, title="New", score=60)
    conn.execute("UPDATE jobs SET date_posted = ? WHERE id = ?", (date.today().isoformat(), new))
    conn.execute("UPDATE jobs SET date_posted = '2026-01-01' WHERE id = ?", (old,))
    third = add_job(conn, title="Third", score=70)
    conn.execute("UPDATE jobs SET date_posted = '2026-01-01' WHERE id = ?", (third,))
    assert [r["id"] for r in triage.to_sort(conn)] == [new, old, third]
    triage.decide(conn, new, "keep", profile)
    triage.decide(conn, old, "pass", profile)
    triage.decide(conn, third, "later", profile)
    assert triage.to_sort(conn) == [] and triage.kept_count(conn) == 1
    assert job_row(conn, old)["status"] == "rejected"
    assert plan.queue(conn)[0]["id"] == new  # kept jobs first
    with pytest.raises(plan.GateError):
        triage.decide(conn, new, "approve", profile)  # sorting never approves


def test_contacts_and_referral_draft(conn):
    j = add_job(conn, company="Acme Robotics")
    conn.execute("UPDATE jobs SET company_key = 'acmerobotics' WHERE id = ?", (j,))  # as discovery stores it
    cid = contacts.add(conn, "Acme Robotics, Inc.", "Sam Lee", "Engineer", "sam@example.com", job_id=j)
    assert contacts.at_company(conn, "acme robotics")[0]["link"] == "mailto:sam@example.com"
    with pytest.raises(contacts.ContactError):
        contacts.add(conn, "Acme", "X", link="javascript:alert(1)")
    contacts.set_state(conn, cid, "asked")
    row = contacts.everyone(conn)[0]
    assert row["status"] == "asked" and row["asked_at"] and row["open_jobs"] == 1
    text = contacts.ask_draft(row, "Data Intern", "Acme Robotics", "https://x.example/1", make_profile())
    assert text.startswith("Hi Sam,") and "Data Intern" in text and "https://x.example/1" in text
    with pytest.raises(contacts.ContactError):
        contacts.set_state(conn, cid, "bogus")


def test_templates_become_drafts_for_each_job(conn):
    job = {"company": "Acme", "title": "Data Intern"}
    skills = [{"name": "Python", "status": "have", "importance": "required"},
              {"name": "SQL", "status": "have", "importance": "preferred"}]
    assert drafts.fill_template("I'd love to join {company} as a {title} using {skills}.", job, skills) == \
        "I'd love to join Acme as a Data Intern using Python and SQL."
    assert drafts.fill_template("{skills}", job, []) == "{skills}"  # nothing to say: you notice and fix it
    profile = make_profile()
    profile.short_answers["Why do you want to work here?"] = "Because {company} builds things I use."
    j = add_job(conn, company="Globex")
    a = plan.prepare(conn, j, profile)
    f = next(f for f in a.fields if f.maps_to.startswith("short:"))
    assert f.value == "Because Globex builds things I use." and f.state == plan.DRAFT


def test_reply_timing_in_insights(conn):
    j = submitted(conn, 3)
    conn.execute("UPDATE jobs SET date_posted = date('now', '-4 day') WHERE id = ?", (j,))
    st = outcomes.stats(conn)
    timing = {r.group for r in st["groups"]["Applied after posting"]}
    assert timing == {"same or next day"}
    assert {r.group for r in st["groups"]["Referral"]} == {"no referral"}


def test_backup_leaves_out_sign_ins_and_keys(paths, conn, tmp_path):
    from jobpilot.backup import make_backup
    from jobpilot.onboarding.profile import save_profile

    save_profile(make_profile(), paths.profile)
    (paths.home / "browser_profile").mkdir()
    (paths.home / "browser_profile" / "Cookies").write_text("secret")
    (paths.home / "extension_key.txt").write_text("k" * 30)
    (paths.resumes / "general.pdf").write_bytes(b"%PDF")
    out = tmp_path / "b.zip"
    assert make_backup(paths, out) >= 3
    names = zipfile.ZipFile(out).namelist()
    assert "jobpilot.db" in names and "profile.yaml" in names and "resumes/general.pdf" in names
    assert not any("browser_profile" in n or "extension_key" in n for n in names)


def test_dashboard_pages(paths):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import load_profile, save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        j = add_job(c, title="Sort Me Intern", company="Sortco")
        c.execute("UPDATE jobs SET date_posted = ? WHERE id = ?", (date.today().isoformat(), j))
        c.commit()
    app = create_app(paths)
    client = TestClient(app)
    tok = app.state.csrf_token

    page = client.get("/triage").text
    assert "Sort Me Intern" in page and 'data-key="y"' in page and "New" in page
    assert "Sort 1 quickly" in client.get("/apply").text
    r = client.post(f"/triage/{j}", data={"csrf": tok, "action": "keep"})
    assert r.status_code == 200 and "All sorted" in r.text

    r = client.post("/people", data={"csrf": tok, "action": "add", "company": "Sortco", "name": "Ada Park",
                                     "back": f"/jobs/{j}#people", "job_id": str(j)})
    assert r.status_code == 200 and "Ada Park" in r.text and "Referral ask draft" in r.text
    assert "Ada Park" in client.get("/people").text
    assert client.post("/people", data={"csrf": tok, "action": "add", "company": "", "name": "x"}).status_code == 200
    assert client.post("/people", data={"csrf": tok, "action": "add", "company": "a", "name": "b",
                                        "back": "https://evil.example"}, follow_redirects=False).headers[
        "location"].startswith("/people")

    r = client.post("/resumes", data={"csrf": tok, "action": "add_answer", "question": "Why us?",
                                      "answer": "Because {company}."})
    assert "Why us?" in r.text and load_profile(paths.profile).short_answers["Why us?"] == "Because {company}."
    r = client.post("/resumes", data={"csrf": tok, "action": "add_answer", "question": "What are your salary expectations?",
                                      "answer": "100"})
    assert "Only you can answer" in r.text

    r = client.get("/backup.zip")
    assert r.status_code == 200 and r.content[:2] == b"PK"
