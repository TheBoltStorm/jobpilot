"""Applications board, pace numbers, logging outside applications, remembered answers, resumes page."""

from datetime import date, timedelta

import pytest

from jobpilot.core import db
from jobpilot.review import outcomes, plan, tracker
from jobpilot.review.plan import PlanField
from tests.test_outcomes import TODAY, submitted
from tests.test_review import add_job, make_profile


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


def test_board_tiles_and_columns(conn):
    a, b, c, d = (submitted(conn, n) for n in (1, 2, 3, 4))
    outcomes.ensure(conn)
    outcomes.set_stage(conn, b, "assessment")
    outcomes.set_stage(conn, c, "final")
    outcomes.set_stage(conn, d, "no_response")
    rows = tracker.applications(conn)
    cols = {col.key: [r["job_id"] for r in items] for col, items in tracker.board(rows)}
    assert cols["waiting"] == [a] and cols["assessment"] == [b] and cols["interview"] == [c] and cols["rejected"] == [d]
    t = tracker.tiles(conn)
    assert (t["applications"], t["assessments"], t["interviews"], t["offers"]) == (4, 1, 1, 0)
    assert [r["job_id"] for r in tracker.applications(conn, q="nothing-like-this")] == []


def test_pace_numbers(conn):
    for days in (0, 0, 1, 2, 9):
        submitted(conn, days)
    assert tracker.applied_on(conn, TODAY) == 2
    assert tracker.streak(conn, TODAY) == 3
    series = tracker.weekly_series(conn, 4, TODAY)
    assert len(series) == 4 and sum(n for _, n in series) == 5


def test_week_strip_shows_next_steps(conn):
    job = submitted(conn, 5)
    outcomes.ensure(conn)
    outcomes.set_next(conn, job, TODAY.isoformat(), "Interview 1:00 PM")
    w = tracker.week(conn, TODAY)
    assert len(w["days"]) == 7 and sum(d["today"] for d in w["days"]) == 1
    assert any(d["dot"] for d in w["days"])
    assert w["events"][0]["when"] == "Today" and w["events"][0]["what"] == "Interview 1:00 PM"


def test_avatar_is_local_initials():
    assert tracker.avatar("American Express")[0] == "AE"
    assert tracker.avatar("IBM")[0] == "I" and tracker.avatar("")[0] == "?"


def test_applied_elsewhere_is_yours_and_tracked(conn):
    profile = make_profile()
    job = add_job(conn)
    plan.applied_elsewhere(conn, job, profile, (TODAY - timedelta(days=3)).isoformat())
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "submitted"
    actors = {r[0] for r in conn.execute("SELECT actor FROM application_events WHERE job_id = ? AND to_status IN "
                                         "('approved', 'submitted')", (job,))}
    assert actors == {"user"}
    outcomes.ensure(conn)
    assert outcomes.load(conn, job)["stage"] == "waiting"
    sub = conn.execute("SELECT submitted_at FROM applications WHERE job_id = ?", (job,)).fetchone()[0]
    assert sub.startswith((TODAY - timedelta(days=3)).isoformat())


def test_remembered_answers_skip_protected_and_profile_facts():
    p = make_profile()
    assert plan.remember_answers(p, {"How did you hear about us?": "A friend", "What are your salary expectations?": "1"}) == 1
    assert "How did you hear about us?" in p.short_answers and len(p.short_answers) >= 1
    assert not any("salary" in q.lower() for q in p.short_answers)
    assert plan.rememberable(PlanField("q", "How did you hear about us?", maps_to=""))
    assert not plan.rememberable(PlanField("q", "Are you authorized to work in the US?", protected="work_authorization"))
    assert not plan.rememberable(PlanField("q", "Phone", maps_to="phone"))


def test_dashboard_pipeline_insights_resumes(paths, sample_files):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import load_profile, save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        job = add_job(c, title="Pipeline Intern", company="Board Co")
        plan.prepare(c, job, make_profile())
        c.execute("UPDATE jobs SET status = 'submitted' WHERE id = ?", (job,))
        c.execute("UPDATE applications SET submitted_at = ? WHERE job_id = ?", (date.today().isoformat(), job))
        other = add_job(c, title="Second Intern", company="Next Co")
        third = add_job(c, title="Third Intern", company="Later Co")
        c.commit()
    app = create_app(paths)
    client = TestClient(app)
    tok = app.state.csrf_token

    page = client.get("/applied").text
    assert 'class="kanban"' in page and "Board Co" in page and "Coming up" in page and "Interviews" in page
    assert "Pipeline Intern" in client.get("/applied?view=list").text
    # Drag and drop posts the column's stage and gets JSON back.
    r = client.post(f"/applied/{job}", data={"csrf": tok, "action": "outcome", "stage": "interview"},
                    headers={"x-jobpilot-fetch": "1"})
    assert r.json() == {"ok": True}
    r = client.post(f"/applied/{job}", data={"csrf": tok, "action": "outcome", "stage": "bogus"},
                    headers={"x-jobpilot-fetch": "1"})
    assert r.status_code == 400 and "unknown stage" in r.json()["error"]
    assert client.post(f"/applied/{job}", data={"csrf": "x", "action": "outcome", "stage": "offer"},
                       headers={"x-jobpilot-fetch": "1"}).status_code == 403
    client.post(f"/applied/{job}", data={"csrf": tok, "action": "outcome", "stage": "interview", "next_at":
                                         date.today().isoformat(), "next_note": "Onsite"})
    assert "Onsite" in client.get("/applied").text
    client.post(f"/applied/{job}", data={"csrf": tok, "action": "next_done"})
    with db.connect(paths.db) as c:
        assert outcomes.load(c, job)["next_at"] is None

    ins = client.get("/insights").text
    assert "Applications per week" in ins and "Funnel" in ins and "applied today" in ins
    assert "/10" in client.get("/").text  # daily goal on Home

    # Approve & next needs the confirm box, then moves on to the next job.
    r = client.post(f"/jobs/{other}", data={"csrf": tok, "action": "approve_next", "draft_count": "0"})
    assert r.status_code == 400 and "Tick the box" in r.text
    r = client.post(f"/jobs/{other}", data={"csrf": tok, "action": "approve_next", "confirm": "yes", "draft_count": "0"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/jobs/{third}")

    # "Remember" saves a non-protected answer to your profile.
    with db.connect(paths.db) as c:
        a = plan.prepare(c, third, make_profile())
        a.fields.append(PlanField("x:hear", "How did you hear about us?", "Online", "you", "human", kind="text"))
        plan.save_application(c, a)
        c.commit()
    client.post(f"/jobs/{third}", data={"csrf": tok, "action": "save", "draft_count": "0",
                                         "fv_x:hear": "Career fair", "rem_x:hear": "on"})
    assert load_profile(paths.profile).short_answers.get("How did you hear about us?") == "Career fair"
    assert "Career fair" in client.get("/resumes").text

    # Applied somewhere else: logged straight onto the board.
    r = client.post("/add", data={"csrf": tok, "url": "https://careers.example.com/jobs/55", "title": "Ops Intern",
                                  "company": "Elsewhere Inc", "applied": "on"})
    assert r.status_code == 200 and "Logged as applied" in r.text and "Elsewhere Inc" in r.text

    # Resumes: upload a version and make it the default.
    with open(sample_files["docx"], "rb") as fh:
        r = client.post("/resumes", data={"csrf": tok, "label": "data-science", "action": "upload"},
                        files={"file": ("cv.docx", fh, "application/octet-stream")})
    assert r.status_code == 200 and "data-science" in r.text
    r = client.post("/resumes", data={"csrf": tok, "action": "default", "label": "data-science"})
    with db.connect(paths.db) as c:
        assert db.get_resume(c)["label"] == "data-science"
    r = client.post("/resumes", data={"csrf": tok, "label": "x", "action": "upload"},
                    files={"file": ("cv.exe", b"MZ", "application/octet-stream")})
    assert "PDF or DOCX" in r.text
