"""Answer once for many jobs, and one-click fill on any site (multi-page, sign-in, never overwrite)."""

import pytest

from jobpilot.autofill import plan_run
from jobpilot.autofill.generic import GenericFiller
from jobpilot.core import db
from jobpilot.review import bulk, pagefill, plan
from jobpilot.review.status import GateError
from tests.test_pagefill import page  # noqa: F401  (Playwright page with the generic form)
from tests.test_pagefill import URL
from tests.test_review import add_job, make_profile


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


def test_shared_questions_group_across_jobs(conn):
    jobs = [add_job(conn, title=f"Intern {i}", company=f"Co {i}") for i in range(3)]
    for j in jobs:
        plan.prepare(conn, j, make_profile())
    labels = {q.label: q for q in bulk.shared_questions(conn)}
    assert labels["Phone"].count == 3  # not in this profile, so every application asks you
    assert not any("Background" in q for q in labels)  # consent: always on the form, by you
    assert labels["Will you now or in the future require visa sponsorship?"].protected == "sponsorship"
    assert len(bulk.shared_questions(conn, min_jobs=4)) == 0


def test_answer_once_fills_ticked_jobs_only(conn):
    profile = make_profile()
    a, b, c = (add_job(conn, title=f"Intern {i}", company=f"Co {i}") for i in range(3))
    for j in (a, b, c):
        plan.prepare(conn, j, profile)
    q = next(q for q in bulk.shared_questions(conn) if q.label == "Phone")
    assert bulk.answer(conn, profile, q.gid, "555-0100", {a, b}, remember=True) == 2
    got = {j: next(f for f in plan.load_application(conn, j).fields if f.key == "phone") for j in (a, b, c)}
    assert got[a].value == "555-0100" and got[a].state == plan.HUMAN and got[c].value == ""
    assert not profile.short_answers  # a profile fact, not a reusable answer
    assert [s.job_id for s in bulk.find(conn, q.gid).spots] == [c]
    with pytest.raises(GateError):
        bulk.answer(conn, profile, q.gid, "", {c})
    with pytest.raises(GateError):
        bulk.answer(conn, profile, q.gid, "555", set())


def test_protected_answers_are_never_remembered(conn):
    profile = make_profile()
    jobs = [add_job(conn, title=f"Intern {i}", company=f"Co {i}") for i in range(2)]
    for j in jobs:
        plan.prepare(conn, j, profile)
    q = next(q for q in bulk.shared_questions(conn) if q.protected == "sponsorship")
    bulk.answer(conn, profile, q.gid, "No", {jobs[0]}, remember=True)
    assert not any("sponsor" in k.lower() for k in profile.short_answers)
    sp = [next(f for f in plan.load_application(conn, j).fields if f.protected == "sponsorship") for j in jobs]
    assert sp[0].value == "No" and sp[0].state == plan.HUMAN and sp[1].value == ""


def test_answers_page(paths):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        ids = [add_job(c, title=f"Intern {i}", company=f"Co {i}") for i in range(2)]
        for j in ids:
            plan.prepare(c, j, make_profile())
        c.commit()
    app = create_app(paths)
    client = TestClient(app)
    assert "Answer them once" in client.get("/apply").text
    page_ = client.get("/answers").text
    assert "Phone" in page_ and "tick only the jobs" in page_
    with db.connect(paths.db) as c:
        gid = next(q.gid for q in bulk.shared_questions(c) if q.label == "Phone")
    r = client.post("/answers", data={"csrf": app.state.csrf_token, "gid": gid, "value": "555-0100",
                                      "job": [str(i) for i in ids]})
    assert r.status_code == 200 and "Answered." in r.text
    assert client.post("/answers", data={"csrf": "x", "gid": gid, "value": "x"}).status_code == 403


# ---- one-click fill on any site

def planner_for(conn, profile, job_id=None):
    return lambda url, title, fields: pagefill.plan_page(conn, profile, url, title, fields, job_id=job_id)


def test_generic_fill_never_overwrites_and_fills_new_pages(page, conn):  # noqa: F811
    profile = make_profile()
    page.fill("[name=contact_email]", "typed@by.me")  # you already typed this one
    filler = GenericFiller(planner_for(conn, profile))
    res = filler.fill_page(page)
    assert res.outcome == "stopped_before_submit" and res.count("filled") >= 1
    assert page.input_value("#fn") == profile.contact.first_name
    assert page.input_value("[name=contact_email]") == "typed@by.me"
    assert page.input_value("#auth") == ""  # work authorization stays yours
    assert not page.is_checked("[name=agree]") and page.evaluate("window.submitted") is False
    assert not filler.has_new_fields(page)
    # The next page of the form shows up (same window): it's filled too, the first one isn't redone.
    page.evaluate("""() => { document.body.insertAdjacentHTML('beforeend',
        '<label for="ln2">Last name</label><input id="ln2" name="last_name_2">'); }""")
    assert filler.has_new_fields(page)
    res2 = filler.fill_page(page)
    assert page.input_value("#ln2") == profile.contact.last_name
    assert all(s.label != "First Name" for s in res2.steps if s.action == "filled")


def test_generic_fill_leaves_sign_in_pages_alone(page, conn):  # noqa: F811
    page.set_content('<form><label>Email<input name="e"></label><label>Password<input type="password" name="p">'
                     '</label></form>')
    res = GenericFiller(planner_for(conn, make_profile())).fill_page(page)
    assert res.outcome == "sign_in" and page.input_value("[name=e]") == ""


def test_dry_run_types_nothing(page, conn):  # noqa: F811
    res = GenericFiller(planner_for(conn, make_profile()), dry_run=True).fill_page(page)
    assert res.outcome == "dry_run_done" and res.count("would_fill") >= 1
    assert page.input_value("#fn") == ""


def test_any_site_fill_needs_your_approval(conn):
    profile = make_profile()
    job = add_job(conn, title="Data Intern", company="Example Co")
    conn.execute("UPDATE jobs SET url = ?, ats = 'workday' WHERE id = ?", (URL, job))
    plan.prepare(conn, job, profile)
    with pytest.raises(GateError, match="approved"):
        plan_run(conn, job, "fill")
    plan.approve(conn, job, profile)
    got, url, fields, _ = plan_run(conn, job, "fill")
    assert url == URL and fields == []
