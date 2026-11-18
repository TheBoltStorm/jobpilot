"""After you apply: outcome stages (yours only), follow-ups (drafted, never sent), stats."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from jobpilot.core import db
from jobpilot.review import outcomes, plan
from jobpilot.review.outcomes import FollowupRules
from jobpilot.review.status import GateError
from tests.test_review import add_job, make_profile

TODAY = date(2026, 11, 18)


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


def submitted(conn, days_ago: int, **kw) -> int:
    job = add_job(conn, **kw)
    plan.prepare(conn, job, make_profile())
    conn.execute("UPDATE jobs SET status = 'submitted' WHERE id = ?", (job,))
    conn.execute("UPDATE applications SET submitted_at = ? WHERE job_id = ?",
                 ((TODAY - timedelta(days=days_ago)).isoformat() + "T10:00:00+00:00", job))
    conn.commit()
    return job


def test_submitted_jobs_start_waiting(conn):
    job = submitted(conn, 1)
    assert outcomes.ensure(conn) == 1 and outcomes.ensure(conn) == 0
    assert outcomes.load(conn, job)["stage"] == "waiting"


def test_only_submitted_jobs_have_outcomes(conn):
    job = add_job(conn)
    with pytest.raises(GateError, match="submitted"):
        outcomes.set_stage(conn, job, "interview")


def test_followup_timing(conn):
    rules = FollowupRules(first_after_days=7, then_every_days=7, max_followups=2, quiet_after_days=30)
    fresh, old = submitted(conn, 3), submitted(conn, 9)
    due = {d.job_id: d for d in outcomes.due(conn, rules, TODAY)}
    assert old in due and fresh not in due and due[old].kind == "followup"
    outcomes.followup_sent(conn, old, (TODAY - timedelta(days=1)).isoformat())
    assert old not in {d.job_id for d in outcomes.due(conn, rules, TODAY)}  # just followed up
    later = TODAY + timedelta(days=7)
    assert old in {d.job_id for d in outcomes.due(conn, rules, later)}  # second follow-up
    outcomes.followup_sent(conn, old, later.isoformat())
    # Two follow-ups sent: no third; after 30 days suggest "No response".
    much_later = TODAY + timedelta(days=40)
    d = {x.job_id: x for x in outcomes.due(conn, rules, much_later)}[old]
    assert d.kind == "quiet"


def test_scheduled_step_and_mute_pause_followups(conn):
    rules = FollowupRules()
    job = submitted(conn, 20)
    outcomes.set_stage(conn, job, "interview", on=(TODAY - timedelta(days=10)).isoformat())
    outcomes.set_next(conn, job, (TODAY + timedelta(days=3)).isoformat(), "onsite")
    assert job not in {d.job_id for d in outcomes.due(conn, rules, TODAY)}
    assert [r["job_id"] for r in outcomes.upcoming(conn, TODAY)] == [job]
    # Right after the interview: a thank-you note.
    after = TODAY + timedelta(days=4)
    assert {d.job_id: d.kind for d in outcomes.due(conn, rules, after)}[job] == "thank_you"
    outcomes.mute(conn, job)
    assert not outcomes.due(conn, rules, after + timedelta(days=30))


def test_closed_stages_need_nothing(conn):
    job = submitted(conn, 60)
    outcomes.set_stage(conn, job, "rejected", note="form email")
    assert not outcomes.due(conn, FollowupRules(), TODAY)
    row = outcomes.load(conn, job)
    assert row["responded_at"]  # a rejection is still a reply
    ev = conn.execute("SELECT actor, action, note FROM application_events WHERE job_id = ? ORDER BY id DESC", (job,)).fetchone()
    assert ev["actor"] == "user" and "Not selected" in ev["action"] and ev["note"] == "form email"


def test_bad_input(conn):
    job = submitted(conn, 1)
    with pytest.raises(GateError):
        outcomes.set_stage(conn, job, "hired!!")
    with pytest.raises(GateError, match="dates"):
        outcomes.set_next(conn, job, "next tuesday")


def test_draft_is_facts_only():
    p = make_profile()
    subj, body = outcomes.draft("followup", "Data Intern", "Acme", date(2026, 11, 2), p)
    assert "Data Intern" in subj and "Acme" in body and "November 2" in body
    assert p.full_name in body and p.contact.email in body
    _, thanks = outcomes.draft("thank_you", "Data Intern", "Acme", None, p)
    assert "Thank you" in thanks


def test_nothing_ever_sends():
    src = (Path(__file__).parents[1] / "jobpilot" / "review" / "outcomes.py").read_text(encoding="utf-8")
    for bad in ("smtplib", "import httpx", "requests", "sendmail", "mailto"):
        assert bad not in src


def test_stats(conn):
    a, b, c = submitted(conn, 30), submitted(conn, 30), submitted(conn, 30)
    outcomes.ensure(conn)
    outcomes.set_stage(conn, a, "interview", on=(TODAY - timedelta(days=20)).isoformat())
    outcomes.set_stage(conn, b, "rejected", on=(TODAY - timedelta(days=25)).isoformat())
    st = outcomes.stats(conn)
    o = st["overall"]
    assert (o.applied, o.responded, o.interviews, o.offers) == (3, 2, 1, 0)
    assert st["median_days_to_reply"] == 10
    assert {"Source", "Site", "Field", "Match score", "Resume", "Minimums"} <= set(st["groups"])


def test_export_has_outcome_columns(conn, tmp_path):
    from jobpilot.review import export

    job = submitted(conn, 5)
    outcomes.ensure(conn)
    outcomes.set_stage(conn, job, "screen")
    out = tmp_path / "log.csv"
    export.to_csv(conn, out)
    text = out.read_text(encoding="utf-8-sig")
    assert "outcome" in text.splitlines()[0] and "Recruiter screen" in text


def test_dashboard_applied_flow(paths):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        job = add_job(c, title="Quiet Intern")
        plan.prepare(c, job, make_profile())
        c.execute("UPDATE jobs SET status = 'submitted' WHERE id = ?", (job,))
        c.execute("UPDATE applications SET submitted_at = ? WHERE job_id = ?",
                  ((date.today() - timedelta(days=10)).isoformat(), job))
        c.commit()
    app = create_app(paths)
    client = TestClient(app)
    tok = app.state.csrf_token
    r = client.get("/applied")
    assert r.status_code == 200 and "Quiet Intern" in r.text and "DRAFT" in r.text and "I sent it" in r.text
    assert client.post(f"/applied/{job}", data={"csrf": "wrong", "action": "followup_sent"}).status_code == 403
    r = client.post(f"/applied/{job}", data={"csrf": tok, "action": "followup_sent", "back": "/applied"})
    assert r.status_code == 200 and "Recorded your follow-up" in r.text
    r = client.post(f"/applied/{job}", data={"csrf": tok, "action": "outcome", "stage": "interview",
                                              "back": f"/jobs/{job}"})
    assert r.status_code == 200 and "After you applied" in r.text
    r = client.post(f"/applied/{job}", data={"csrf": tok, "action": "outcome", "stage": "nope",
                                              "back": "https://evil.example/"})
    assert r.status_code == 200 and "unknown stage" in r.text  # bad redirect target falls back to /applied
    assert "follow-ups to send" in client.get("/").text
