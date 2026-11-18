"""Phase 6: tailored resume versions. Reorder only; the content must never change."""

import json
from collections import Counter

import pytest

from jobpilot.core import db
from jobpilot.matching.skills import load_taxonomy
from jobpilot.review import plan, tailor
from jobpilot.review.status import GateError
from tests.test_review import add_job, make_profile

# The posting wants React, AWS and Docker most; the sample resume mentions them late.
SKILLS = [
    {"key": "react", "name": "React", "status": "have", "importance": "required"},
    {"key": "aws", "name": "AWS", "status": "have", "importance": "required"},
    {"key": "docker", "name": "Docker", "status": "have", "importance": "preferred"},
    {"key": "go", "name": "Go", "status": "missing", "importance": "required"},
]


@pytest.fixture
def tax():
    return load_taxonomy()


@pytest.fixture
def profile():
    return make_profile()


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


def job_with_skills(conn, **kw):
    job = add_job(conn, **kw)
    conn.execute("UPDATE matches SET skills_json = ? WHERE job_id = ?", (json.dumps({"skills": SKILLS}), job))
    conn.commit()
    return job


def test_tailor_only_reorders(profile, tax):
    after = tailor.tailor(profile, SKILLS, tax)
    assert Counter(tailor.blocks(after)) == Counter(tailor.blocks(profile))
    assert tailor.blocks(after) != tailor.blocks(profile)
    # Jobs stay in date order; the original profile isn't touched.
    assert [x.company for x in after.experience] == [x.company for x in profile.experience]
    assert profile.skills != after.skills
    assert after.skills[:2] == [s for s in profile.skills if s in ("React", "AWS")][:2]
    campus = next(p for p in after.projects if p.name == "CampusEats")
    assert after.projects[0] is campus or after.projects[0].name == "CampusEats"


def test_no_relevant_skills_means_no_change(profile, tax):
    after = tailor.tailor(profile, [{"key": "go", "status": "missing", "importance": "required"}], tax)
    assert tailor.blocks(after) == tailor.blocks(profile)
    assert tailor.diff(profile, after).empty


def test_same_content_catches_any_edit(profile):
    changed = profile.model_copy(deep=True)
    changed.experience[0].bullets[0] += " Led the team."
    assert not tailor.same_content(profile, changed)
    dropped = profile.model_copy(deep=True)
    dropped.skills.pop()
    assert not tailor.same_content(profile, dropped)


def test_diff_lists_moves(profile, tax):
    d = tailor.diff(profile, tailor.tailor(profile, SKILLS, tax))
    assert d.moves and all(m.old != m.new for m in d.moves)
    assert any(m.section == "Skills" and m.text == "React" for m in d.moves)


def test_propose_writes_no_file_until_approved(conn, profile, paths, tax):
    job = job_with_skills(conn)
    with conn:
        plan.prepare(conn, job, profile)
        d = tailor.propose(conn, job, profile, tax)
    assert d.moves and not list(paths.resumes.glob("*.docx"))
    row = tailor.load(conn, job)
    assert row["label"] == f"general-for-job-{job}" and row["approved_at"] is None
    assert db.get_resume(conn, row["label"]) is None


def test_approve_creates_docx_with_exactly_the_same_lines(conn, profile, paths, tax):
    from docx import Document

    job = job_with_skills(conn)
    with conn:
        plan.prepare(conn, job, profile)
        tailor.propose(conn, job, profile, tax)
        label = tailor.approve(conn, job, profile, paths.resumes)
    path = paths.resumes / f"{label}.docx"
    assert path.is_file()
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    for kind, line in tailor.blocks(profile):
        assert line in text, line
    assert db.get_resume(conn, label) is not None
    assert plan.load_application(conn, job).resume_label == label
    with pytest.raises(GateError):
        tailor.discard(conn, job)


def test_approve_refuses_if_profile_changed(conn, profile, paths, tax):
    job = job_with_skills(conn)
    with conn:
        plan.prepare(conn, job, profile)
        tailor.propose(conn, job, profile, tax)
    profile.skills.append("Kubernetes")
    with pytest.raises(GateError, match="profile changed"):
        tailor.approve(conn, job, profile, paths.resumes)
    assert not list(paths.resumes.glob("*.docx"))


def test_approve_refuses_tampered_proposal(conn, profile, paths, tax):
    job = job_with_skills(conn)
    with conn:
        plan.prepare(conn, job, profile)
        tailor.propose(conn, job, profile, tax)
    bad = json.loads(tailor.load(conn, job)["tailored_json"])
    bad["experience"][0]["bullets"][0] = "Single-handedly founded the company."
    conn.execute("UPDATE tailored_resumes SET tailored_json = ? WHERE job_id = ?", (json.dumps(bad), job))
    with pytest.raises(GateError, match="exactly your resume"):
        tailor.approve(conn, job, profile, paths.resumes)
    assert not list(paths.resumes.glob("*.docx"))


def test_tailoring_a_submitted_job_is_refused(conn, profile, tax):
    job = job_with_skills(conn, status="submitted")
    with pytest.raises(GateError):
        tailor.propose(conn, job, profile, tax)


def test_dashboard_tailor_flow(paths, monkeypatch):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        job = job_with_skills(c)
    app = create_app(paths)
    client = TestClient(app, base_url="http://127.0.0.1")
    tok = app.state.csrf_token
    page = client.get(f"/jobs/{job}")
    assert "Suggest a tailored version" in page.text
    r = client.post(f"/jobs/{job}", data={"csrf": tok, "action": "tailor"}, headers={"Origin": "http://127.0.0.1"})
    assert r.status_code == 200 and "Approve and create DOCX" in r.text and "#" in r.text
    r = client.post(f"/jobs/{job}", data={"csrf": tok, "action": "tailor_approve"},
                    headers={"Origin": "http://127.0.0.1"})
    assert r.status_code == 200 and f"general-for-job-{job}" in r.text
    assert (paths.resumes / f"general-for-job-{job}.docx").is_file()
