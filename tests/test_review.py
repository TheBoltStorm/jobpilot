import json

import pytest

from jobpilot.core import db
from jobpilot.onboarding.interview import profile_from_resume
from jobpilot.onboarding.resume_parser import parse_text
from jobpilot.review import export, plan
from jobpilot.review.status import ALLOWED, STATUS_KEYS, SYSTEM_ALLOWED, USER_ONLY, GateError, check, transition
from jobpilot.samples import sample_resume_text
from jobpilot.settings import Settings


def make_profile():
    p = profile_from_resume(parse_text(sample_resume_text()))
    p.contact.phone = ""  # so one standard field is NEEDS_HUMAN
    return p


def add_job(conn, title="Software Engineer Intern", company="Acme", ats="greenhouse", status="matched", score=80,
            description="Requirements: Python"):
    n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] + 1
    cur = conn.execute(
        """INSERT INTO jobs (dedupe_key, company_key, url_key, title, company, url, ats, description, date_found,
                             last_seen, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-11-01', '2026-11-01', ?)""",
        (f"k{n}", company.lower(), f"u{n}", title, company, f"https://example.test/jobs/{n}", ats, description, status))
    if score is not None:
        conn.execute("""INSERT INTO matches (job_id, score, confidence, reason, skills_json, engine, profile_hash,
                        description_len, scored_at) VALUES (?, ?, 'high', 'r', '{"skills": []}', 'local', 'h', 0, 'now')""",
                     (cur.lastrowid, score))
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


@pytest.fixture
def profile():
    return make_profile()


def answer_all(conn, job_id, profile):
    app = plan.load_application(conn, job_id) or plan.prepare(conn, job_id, profile)
    vals = {f.key: "my answer" for f in app.fields if f.unanswered}
    return plan.save_answers(conn, job_id, profile, vals)


# ---------------------------------------------------------------- the gate

@pytest.mark.parametrize("current", STATUS_KEYS)
@pytest.mark.parametrize("to", sorted(USER_ONLY))
def test_system_can_never_approve_submit_or_reject(current, to):
    with pytest.raises(GateError):
        check(current, to, "system")


def test_submitted_is_final_and_only_reachable_after_approval():
    assert ALLOWED["submitted"] == frozenset()
    reach_submitted = {s for s, tos in ALLOWED.items() if "submitted" in tos}
    assert reach_submitted == {"approved", "filled"}
    assert all("submitted" not in tos for tos in SYSTEM_ALLOWED.values())
    with pytest.raises(GateError):
        check("matched", "submitted", "user")


def test_only_approved_jobs_can_be_filled():
    assert {s for s, tos in ALLOWED.items() if "filled" in tos} == {"approved"}


def test_gate_has_no_off_switch():
    def keys(d, prefix=""):
        for k, v in d.items():
            yield prefix + k
            if isinstance(v, dict):
                yield from keys(v, prefix + k + ".")
    names = list(keys(Settings().model_dump()))
    assert not [n for n in names if any(w in n.lower() for w in ("submit", "gate", "auto_approve", "approve"))]


def test_unknown_actor_rejected(conn):
    job = add_job(conn)
    with pytest.raises(GateError):
        transition(conn, job, "approved", actor="autofill")


# ---------------------------------------------------------------- plan

def test_prepare_marks_protected_and_missing_as_needs_human(conn, profile):
    job = add_job(conn)
    app = plan.prepare(conn, job, profile)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0]
    assert status == "needs_human"
    by_key = {f.key: f for f in app.fields}
    assert by_key["email"].state == "filled" and by_key["email"].value == profile.contact.email
    assert by_key["email"].source == "profile: contact.email"
    assert by_key["phone"].state == "needs_human" and by_key["phone"].value == ""
    assert by_key["resume"].value == "general"
    protected = [f for f in app.fields if f.protected]
    assert {f.protected for f in protected} >= {"work_authorization", "sponsorship", "eeo_demographic", "salary"}
    assert all(f.value == "" and f.state == "needs_human" for f in protected)
    assert by_key["protected:work_authorization"] in app.unanswered


def test_custom_site_bucket(conn, profile):
    job = add_job(conn, ats="workday")
    plan.prepare(conn, job, profile)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "custom_site"


def test_found_jobs_get_a_plan_but_keep_status(conn, profile):
    job = add_job(conn, status="found", score=20)
    plan.prepare(conn, job, profile)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "found"


def test_answering_everything_makes_it_ready(conn, profile):
    job = add_job(conn)
    app = answer_all(conn, job, profile)
    assert not app.unanswered
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "ready_to_fill"
    wa = next(f for f in app.fields if f.key == "protected:work_authorization")
    assert wa.state == "human" and wa.source.startswith("you, on the review page")


def test_human_answers_survive_reprepare_and_clearing_restores_profile(conn, profile):
    job = add_job(conn)
    plan.save_answers(conn, job, profile, {"email": "other@example.test", "protected:sponsorship": "No"})
    profile.contact.first_name = "Changed"
    app = plan.prepare(conn, job, profile)
    by_key = {f.key: f for f in app.fields}
    assert by_key["email"].value == "other@example.test" and by_key["email"].state == "human"
    assert by_key["protected:sponsorship"].value == "No"
    assert by_key["first_name"].value == "Changed"
    app = plan.save_answers(conn, job, profile, {"email": ""})
    email = next(f for f in app.fields if f.key == "email")
    assert email.value == profile.contact.email and email.state == "filled"


def test_approve_snapshots_and_editing_withdraws_approval(conn, profile):
    job = add_job(conn)
    answer_all(conn, job, profile)
    app = plan.approve(conn, job, profile)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "approved"
    snap = json.loads(app.approved_snapshot)
    assert snap["resume"] == "general" and any(f["key"] == "email" for f in snap["fields"])
    plan.save_answers(conn, job, profile, {"email": "changed@example.test"})
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "ready_to_fill"
    assert plan.load_application(conn, job).approved_snapshot is None
    actions = [r[0] for r in conn.execute("SELECT action FROM application_events WHERE job_id = ? ORDER BY id", (job,))]
    assert "approved" in actions and "approval withdrawn" in actions


def test_saving_without_changes_keeps_approval(conn, profile):
    job = add_job(conn)
    answer_all(conn, job, profile)
    plan.approve(conn, job, profile)
    app = plan.load_application(conn, job)
    plan.save_answers(conn, job, profile, {f.key: f.value for f in app.fields}, app.drafts, app.notes)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "approved"


def test_submit_needs_approval_first(conn, profile):
    job = add_job(conn)
    plan.prepare(conn, job, profile)
    with pytest.raises(GateError):
        plan.mark_submitted(conn, job)
    plan.approve(conn, job, profile)
    plan.mark_submitted(conn, job)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "submitted"
    with pytest.raises(GateError):
        plan.reject(conn, job)


def test_reject_reopen_and_skip_ordering(conn, profile):
    a, b = add_job(conn, score=90), add_job(conn, score=70)
    plan.prepare(conn, a, profile)
    plan.prepare(conn, b, profile)
    assert [r["id"] for r in plan.queue(conn)] == [a, b]
    plan.skip(conn, a)
    assert [r["id"] for r in plan.queue(conn)] == [b, a]
    plan.reject(conn, b, "too far")
    assert [r["id"] for r in plan.queue(conn)] == [a]
    plan.reopen(conn, b, profile, min_score=50)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (b,)).fetchone()[0] == "needs_human"


def test_drafts_and_resume_choice(conn, profile):
    db.upsert_resume(conn, label="data", path="d.pdf", sha256="1", text="", parsed={}, make_default=False)
    job = add_job(conn)
    app = plan.save_answers(conn, job, profile, {}, [plan.Draft("Why us?", "I like logistics.", "you wrote this")],
                            resume_label="data")
    assert app.drafts[0].state == "draft" and app.resume_label == "data"
    assert next(f for f in app.fields if f.key == "resume").value == "data"
    with pytest.raises(GateError):
        plan.save_answers(conn, job, profile, {}, resume_label="nope")


# ---------------------------------------------------------------- export

def test_export_has_no_personal_answers(conn, profile, tmp_path):
    job = add_job(conn)
    plan.save_answers(conn, job, profile, {"protected:sponsorship": "SECRET-ANSWER"})
    out = tmp_path / "log.csv"
    assert export.to_csv(conn, out) == 1
    text = out.read_text(encoding="utf-8-sig")
    assert "Acme" in text and "SECRET-ANSWER" not in text and profile.contact.email not in text
    from openpyxl import load_workbook

    x = tmp_path / "log.xlsx"
    export.to_xlsx(conn, x)
    wb = load_workbook(x)
    assert wb.sheetnames == ["Applications", "History"]
    assert wb["Applications"]["B2"].value == "Acme"


def test_export_neutralizes_formulas(conn, profile, tmp_path):
    job = add_job(conn, title="=HYPERLINK(\"http://evil\")")
    plan.prepare(conn, job, profile)
    out = tmp_path / "log.csv"
    export.to_csv(conn, out)
    assert "'=HYPERLINK" in out.read_text(encoding="utf-8-sig")


def test_rescoring_refreshes_prepared_jobs_without_moving_them(conn, profile):
    from jobpilot.matching.engine import run_local
    from jobpilot.matching.skills import load_taxonomy

    job = add_job(conn)
    plan.prepare(conn, job, profile)
    conn.execute("UPDATE matches SET profile_hash = 'stale' WHERE job_id = ?", (job,))
    s = run_local(conn, profile, load_taxonomy(), min_score=99)
    assert s.scored == 1 and s.matched == 0
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "needs_human"
    assert conn.execute("SELECT profile_hash FROM matches WHERE job_id = ?", (job,)).fetchone()[0] != "stale"
