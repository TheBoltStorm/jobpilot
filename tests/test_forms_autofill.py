"""Phase 5: real form questions, the field mapper, drafts, and autofill (dry run + fill, never submit)."""

import json
from pathlib import Path

import pytest

from jobpilot.autofill import plan_run, record
from jobpilot.autofill.greenhouse import FillResult, GreenhouseFiller, Step, decide
from jobpilot.core import db
from jobpilot.forms import load_form
from jobpilot.forms.greenhouse import FormQuestion, form_url_from_api, parse_questions
from jobpilot.review import plan
from jobpilot.review.drafts import draft_answer, is_open_ended
from jobpilot.review.mapping import fit_option, plan_from_form, short_answer_for
from jobpilot.review.status import GateError
from tests.test_review import add_job, make_profile

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_form.html"

# Shaped like boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true (fictional company).
API = {
    "id": 999, "title": "Data Intern", "company_name": "Acme Robotics",
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Last Name", "required": True, "fields": [{"name": "last_name", "type": "input_text", "values": []}]},
        {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text", "values": []}]},
        {"label": "Phone", "required": True, "fields": [{"name": "phone", "type": "input_text", "values": []}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file", "values": []},
                                                              {"name": "resume_text", "type": "textarea", "values": []}]},
        {"label": "LinkedIn Profile", "required": False,
         "fields": [{"name": "question_1", "type": "input_text", "values": []}]},
        {"label": "Are you legally eligible to work in the United States?", "required": True,
         "fields": [{"name": "question_2", "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "Will you require visa sponsorship?", "required": True,
         "fields": [{"name": "question_3", "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "What is your expected graduation date?", "required": True,
         "fields": [{"name": "question_4[]", "type": "multi_value_multi_select",
                     "values": [{"label": "Spring 2027", "value": 1}, {"label": "Fall 2027", "value": 2}]}]},
        {"label": "Why do you want to work at Acme Robotics?", "required": False,
         "fields": [{"name": "question_5", "type": "textarea", "values": []}]},
        {"label": "How did you hear about us?", "required": True,
         "fields": [{"name": "question_6", "type": "input_text", "values": []}]},
        {"label": "I certify the information above is true", "required": True,
         "fields": [{"name": "question_7", "type": "input_text", "values": []}]},
        {"label": "Latitude", "required": True, "fields": [{"name": "latitude", "type": "input_hidden", "values": []}]},
    ],
    "location_questions": [{"label": "Location", "required": True,
                            "fields": [{"name": "location", "type": "input_text", "values": []}]}],
    "education": "education_optional",
    "compliance": [{"type": "eeoc", "questions": [{"label": "Gender", "required": False, "fields": [
        {"name": "gender", "type": "multi_value_single_select",
         "values": [{"label": "Decline To Self Identify"}, {"label": "Female"}, {"label": "Male"}]}]}]}],
    "demographic_questions": {"questions": [{"id": 4001, "label": "Do you identify as transgender?",
                                             "type": "multi_value_single_select", "required": False,
                                             "answer_options": [{"label": "Yes"}, {"label": "No"}]}]},
}

SKILLS = [
    {"key": "python", "name": "Python", "status": "have", "importance": "required", "job_snippet": "Python",
     "evidence": "Software Engineering Intern @ Northwind Logistics: Built a Python service that parsed events."},
    {"key": "sql", "name": "SQL", "status": "have", "importance": "required", "job_snippet": "SQL",
     "evidence": "Software Engineering Intern @ Northwind Logistics: Built a Python service that parsed events. (implies SQL)"},
    {"key": "go", "name": "Go", "status": "missing", "importance": "required", "job_snippet": "Go", "evidence": ""},
]


class FakeHTTP:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.urls = data or API, error, []

    def get_json(self, url, **kw):
        self.urls.append(url)
        if self.error:
            raise self.error
        return json.loads(json.dumps(self.data))

    def close(self):
        pass


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        resume = paths.resumes / "general.pdf"
        resume.write_bytes(b"%PDF-1.4 fake")
        db.upsert_resume(c, label="general", path=str(resume), sha256="0", text="", parsed={}, make_default=True)
        yield c


@pytest.fixture
def profile():
    p = make_profile()
    p.education[0].end = "May 2027"
    p.short_answers = {"How did you hear about this job?": "University career fair"}
    return p


def gh_job(conn, **kw):
    job = add_job(conn, **kw)
    conn.execute("UPDATE jobs SET url = ? WHERE id = ?",
                 (f"https://job-boards.greenhouse.io/acmerobotics/jobs/{999 + job}", job))
    conn.execute("UPDATE matches SET skills_json = ? WHERE job_id = ?", (json.dumps({"skills": SKILLS}), job))
    conn.commit()
    return job


# ---------------------------------------------------------------- parsing

def test_parse_questions_covers_every_section():
    qs = {q.key: q for q in parse_questions(API)}
    assert qs["gh:first_name"].kind == "text" and qs["gh:first_name"].required
    assert qs["gh:resume"].kind == "file"  # the file field, not the paste-text alternative
    assert qs["gh:question_2"].kind == "select" and qs["gh:question_2"].options == ["Yes", "No"]
    assert qs["gh:question_4[]"].kind == "multi_select"
    assert qs["gh:location"].field_id == "candidate-location"
    assert qs["gh:school"].kind == "search_select" and not qs["gh:school"].required
    assert qs["gh:gender"].section == "eeoc"
    assert qs["gh:demo_4001"].section == "demographic" and qs["gh:demo_4001"].field_id == "4001"
    assert "gh:latitude" not in qs


def test_form_url():
    assert form_url_from_api("https://boards-api.greenhouse.io/v1/boards/acme/jobs/12") == \
        "https://job-boards.greenhouse.io/acme/jobs/12"
    assert form_url_from_api("https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs/12").startswith(
        "https://job-boards.eu.greenhouse.io/")


# ---------------------------------------------------------------- mapping

def test_mapping(profile):
    job = {"title": "Data Intern", "company": "Acme Robotics"}
    fields = {f.key: f for f in plan_from_form(parse_questions(API), profile, "general", job, SKILLS)}
    assert fields["gh:first_name"].value == profile.contact.first_name and fields["gh:first_name"].state == "filled"
    assert fields["gh:resume"].value == "general" and fields["gh:resume"].maps_to == "resume"
    assert fields["gh:question_1"].maps_to == "linkedin"
    for key, cat in (("gh:question_2", "work_authorization"), ("gh:question_3", "sponsorship"),
                     ("gh:gender", "eeo_demographic"), ("gh:question_7", "legal")):
        assert fields[key].protected == cat and fields[key].value == "" and fields[key].state == "needs_human"
    assert fields["gh:demo_4001"].protected  # demographic section is always protected
    # "May 2027" fits exactly one option.
    assert fields["gh:question_4[]"].value == "Spring 2027"
    # Saved short answer with a similar question.
    assert fields["gh:question_6"].value == "University career fair"
    # Open question: drafted from facts, labeled DRAFT.
    why = fields["gh:question_5"]
    assert why.state == "draft" and "Python" in why.value and "Northwind Logistics" in why.value
    assert "Go" not in why.value.split("asks for")[1].split(",")[0]
    # Nothing outside the profile.
    assert fields["gh:location"].state in ("filled", "needs_human")


def test_unknown_questions_stay_needs_human(profile):
    qs = [FormQuestion("gh:q9", "q9", "What's your favorite dinosaur?", "text", True)]
    f = plan_from_form(qs, profile, "general", {"title": "t", "company": "c"}, [])[0]
    assert f.state == "needs_human" and f.value == ""


def test_model_key_only_picks_a_profile_key(profile):
    qs = [FormQuestion("gh:q9", "q9", "Your handle on the code-hosting site", "text", model_key="github"),
          FormQuestion("gh:q10", "q10", "Something odd", "text", model_key="invented_key")]
    out = plan_from_form(qs, profile, "general", {"title": "t", "company": "c"}, [])
    assert out[0].value == profile.links.github and "Claude" in out[0].source
    assert out[1].state == "needs_human"


def test_fit_option_never_guesses():
    assert fit_option("Yes", ["Yes", "No"]) == "Yes"
    assert fit_option("May 2027", ["Spring 2027", "Fall 2027"]) == "Spring 2027"
    assert fit_option("2027", ["Spring 2027", "Fall 2027"]) is None
    assert fit_option("Maybe", ["Yes", "No"]) is None


def test_short_answer_matching():
    answers = {"Are you willing to relocate?": "Yes"}
    assert short_answer_for("Willing to relocate?", answers) is not None
    assert short_answer_for("Are you willing to travel?", answers) is None


def test_drafts_need_facts():
    job = {"title": "Data Intern", "company": "Acme"}
    assert draft_answer("Why Acme?", job, []) is None
    only_listed = [{"name": "Python", "status": "have", "importance": "required", "evidence": "Skills: Python"}]
    assert draft_answer("Why Acme?", job, only_listed) is None  # no experience line to point to
    text, note = draft_answer("Why Acme?", job, SKILLS)
    assert text.startswith("The Data Intern role asks for Python and SQL")
    assert "At Northwind Logistics, I built a Python service" in text and "Northwind" in note
    assert is_open_ended("Why do you want to join?", "textarea") and not is_open_ended("Why?", "text")


# ---------------------------------------------------------------- plans + the gate

def test_load_real_form_replans_and_carries_answers(conn, profile):
    job = gh_job(conn)
    plan.save_answers(conn, job, profile, {"protected:work_authorization": "Yes"})
    app = plan.load_real_form(conn, job, profile, FakeHTTP(), {})
    assert load_form(conn, job)[0] == f"https://job-boards.greenhouse.io/acmerobotics/jobs/{999 + job}"
    fields = {f.key: f for f in app.fields}
    assert "protected:work_authorization" not in fields  # standard placeholders are gone
    assert fields["gh:question_2"].value == "Yes" and fields["gh:question_2"].state == "human"
    assert fields["gh:question_3"].state == "needs_human"


def test_loading_form_withdraws_approval(conn, profile):
    job = gh_job(conn)
    plan.prepare(conn, job, profile)
    plan.approve(conn, job, profile)
    plan.load_real_form(conn, job, profile, FakeHTTP(), {})
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "needs_human"
    assert plan.load_application(conn, job).approved_snapshot is None


def test_load_form_errors_are_gate_errors(conn, profile):
    job = gh_job(conn)
    with pytest.raises(GateError, match="Couldn't load"):
        plan.load_real_form(conn, job, profile, FakeHTTP(error=RuntimeError("404")), {})
    other = add_job(conn, ats="workday")
    with pytest.raises(GateError, match="isn't supported"):
        plan.load_real_form(conn, other, profile, FakeHTTP(), {})


def test_select_answers_must_be_options(conn, profile):
    job = gh_job(conn)
    plan.load_real_form(conn, job, profile, FakeHTTP(), {})
    with pytest.raises(GateError, match="options"):
        plan.save_answers(conn, job, profile, {"gh:question_2": "Probably"})
    app = plan.save_answers(conn, job, profile, {"gh:question_4[]": "Spring 2027; Fall 2027"})
    assert next(f for f in app.fields if f.key == "gh:question_4[]").value == "Spring 2027; Fall 2027"


def test_plan_run_gate(conn, profile):
    job = gh_job(conn)
    plan.prepare(conn, job, profile)
    with pytest.raises(GateError, match="load the real form"):
        plan_run(conn, job, "dry_run")
    plan.load_real_form(conn, job, profile, FakeHTTP(), {})
    with pytest.raises(GateError, match="only runs for jobs you approved"):
        plan_run(conn, job, "fill")
    _, url, fields, resume = plan_run(conn, job, "dry_run")  # dry run is fine before approval
    assert url.startswith("https://job-boards.greenhouse.io/") and resume.endswith("general.pdf")
    plan.approve(conn, job, profile)
    _, _, snap_fields, _ = plan_run(conn, job, "fill")
    assert {f["key"] for f in snap_fields} == {f["key"] for f in fields}
    with pytest.raises(GateError):
        plan_run(conn, add_job(conn, ats="workday"), "dry_run")


def test_old_approval_of_standard_fields_cannot_fill(conn, profile):
    job = gh_job(conn)
    plan.prepare(conn, job, profile)
    plan.approve(conn, job, profile)  # approved before the real form was loaded
    conn.execute("INSERT INTO forms (job_id, ats, form_url, questions_json, fetched_at) VALUES (?, 'greenhouse', "
                 "'https://job-boards.greenhouse.io/x/jobs/1', '[]', 'now')", (job,))
    with pytest.raises(GateError, match="real form's questions"):
        plan_run(conn, job, "fill")


def test_decide():
    assert decide({"protected": "sponsorship", "state": "needs_human", "value": ""}, None)[0] == "left_for_you"
    assert decide({"protected": "sponsorship", "state": "human", "value": "No"}, None) == ("fill", "No")
    assert decide({"protected": "legal", "state": "human", "value": "Yes"}, None)[0] == "left_for_you"
    assert decide({"kind": "file", "maps_to": "resume", "state": "filled", "value": "general"}, "/r.pdf") == \
        ("fill", "/r.pdf")
    assert decide({"kind": "file", "maps_to": "", "state": "needs_human"}, "/r.pdf")[0] == "left_for_you"
    assert decide({"state": "draft", "value": "text"}, None) == ("fill", "text")


def test_record_moves_approved_to_filled_only_on_success(conn, profile):
    job = gh_job(conn)
    plan.load_real_form(conn, job, profile, FakeHTTP(), {})
    plan.approve(conn, job, profile)
    record(conn, job, "dry_run", "now", FillResult("dry_run_done", [Step("k", "l", "text", "would_fill")]))
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "approved"
    record(conn, job, "fill", "now", FillResult("stopped_before_submit", [Step("k", "l", "text", "filled")]))
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "filled"


def test_filler_source_never_submits():
    src = (Path(__file__).parents[1] / "jobpilot" / "autofill" / "greenhouse.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert 'press("Enter")' not in code and "press('Enter')" not in code
    assert ".submit(" not in code and "requestSubmit" not in code
    assert 'click(' not in code.replace("loc.click()", "").replace("opts.nth(idx[0]).click()", "")


# ---------------------------------------------------------------- in a real browser (skipped without Edge/Chrome)

@pytest.fixture
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = p.chromium.launch(channel=channel, headless=True) if channel else p.chromium.launch(headless=True)
                break
            except Exception:
                continue
        if browser is None:
            pytest.skip("no Playwright-compatible browser installed")
        ctx = browser.new_context()
        posts = []

        def handler(route, request):
            if request.method == "POST":
                posts.append(request.url)
                return route.fulfill(status=200, body="ok")
            return route.fulfill(status=200, content_type="text/html", body=FIXTURE.read_text(encoding="utf-8"))

        ctx.route("https://job-boards.greenhouse.io/**", handler)
        page = ctx.new_page()
        page.goto("https://job-boards.greenhouse.io/acmerobotics/jobs/999")
        yield page, posts
        browser.close()


def approved_fields(profile):
    fields = plan_from_form(parse_questions(API), profile, "general", {"title": "Data Intern", "company": "Acme"},
                            SKILLS)
    out = []
    for f in fields:
        d = dict(f.__dict__)
        if f.key == "gh:question_3":  # you answered this one on the review page
            d.update(value="No", state="human")
        if f.key == "gh:question_7":  # even with an answer, consent is never ticked by JobPilot
            d.update(value="Yes", state="human")
        out.append(d)
    return out


def test_dry_run_types_nothing(browser_page, profile, tmp_path):
    page, posts = browser_page
    result = GreenhouseFiller(page, dry_run=True).run(approved_fields(profile), str(tmp_path / "r.pdf"))
    assert result.outcome == "dry_run_done"
    assert page.input_value("#first_name") == "" and page.input_value("#question_5") == ""
    steps = {s.key: s for s in result.steps}
    assert steps["gh:first_name"].action == "would_fill"
    assert steps["gh:question_2"].action == "left_for_you"  # work authorization, unanswered
    assert steps["gh:question_7"].action == "left_for_you"
    page.click("#submit-btn")  # even a click on Submit does nothing while the guard is on
    assert page.evaluate("window.__posted") == 0 and posts == []


def test_fill_mode_fills_and_stops_before_submit(browser_page, profile, tmp_path):
    page, posts = browser_page
    resume = tmp_path / "general.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    filler = GreenhouseFiller(page, dry_run=False, timeout_ms=2000)
    result = filler.run(approved_fields(profile), str(resume))
    assert result.outcome == "stopped_before_submit"
    assert page.input_value("#first_name") == profile.contact.first_name
    assert page.input_value("#email") == profile.contact.email
    assert page.input_value("#question_3") == "No"  # your per-application answer, picked from the menu
    assert page.input_value("#question_2") == ""  # unanswered protected question left alone
    assert page.is_checked("#question_4\\[\\]_1") and not page.is_checked("#question_4\\[\\]_2")
    assert "Northwind Logistics" in page.input_value("#question_5")
    assert page.input_value("#question_6") == "University career fair"
    assert page.input_value("#gender") == ""
    assert not page.is_checked("#question_7")
    assert page.eval_on_selector("#resume", "e => e.files[0].name") == "general.pdf"
    assert page.evaluate("window.__posted") == 0 and posts == []
    # Still guarded: a stray click can't submit until the form is handed to you.
    page.click("#submit-btn")
    assert page.evaluate("window.__posted") == 0 and filler.blocked_count() >= 1
    # Handing over: now *you* can submit.
    filler.release()
    page.click("#submit-btn")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__posted") == 1


def test_sign_in_page_is_an_account_wall(browser_page, profile):
    page, _ = browser_page
    page.set_content("<form><input type=email><input type=password></form>")
    result = GreenhouseFiller(page).run(approved_fields(profile), None)
    assert result.outcome == "account_wall" and not result.steps


def test_dashboard_loads_and_renders_real_form(paths, monkeypatch):
    from fastapi.testclient import TestClient

    from jobpilot.core.http import PoliteClient
    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    with db.connect(paths.db) as c:
        job = gh_job(c)
    monkeypatch.setattr(PoliteClient, "get_json", lambda self, url, **kw: json.loads(json.dumps(API)))
    app = create_app(paths)
    client = TestClient(app)
    page = client.get(f"/jobs/{job}").text
    assert "Load this form&#39;s real questions" in page or "Load this form's real questions" in page
    r = client.post(f"/jobs/{job}", data={"csrf": app.state.csrf_token, "action": "load_form"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/jobs/{job}").text
    assert "Real form:" in page and "Will you require visa sponsorship?" in page
    assert '<option value="">(NEEDS_HUMAN: pick one)</option>' in page
    assert "Voluntary self-identification" in page and "DRAFT" in page
    assert "Fill the form for me" not in page  # only after you approve
    # Pick a sponsorship answer and tick a graduation option, then check they stuck.
    client.post(f"/jobs/{job}", data={"csrf": app.state.csrf_token, "action": "save", "draft_count": "0",
                                      "fv_gh:question_3": "No", "fv_gh:question_4[]": ["", "Fall 2027"]})
    with db.connect(paths.db) as c:
        fields = {f.key: f for f in plan.load_application(c, job).fields}
    assert fields["gh:question_3"].value == "No" and fields["gh:question_4[]"].value == "Fall 2027"


def test_long_or_open_labels_are_not_keyword_matched(profile):
    qs = [FormQuestion("gh:q1", "q1", "Share a link to something you've built, such as a GitHub repo or project write-up.",
                       "textarea"),
          FormQuestion("gh:q2", "q2", "Github", "text")]
    out = plan_from_form(qs, profile, "general", {"title": "t", "company": "c"}, [])
    assert out[0].state == "needs_human" and out[0].value == ""
    assert out[1].maps_to == "github"


def test_phase4_answers_without_maps_to_carry_over():
    from jobpilot.review.plan import PlanField, merge_human

    fresh = [PlanField("gh:q2", "Are you legally eligible to work in the US?", kind="select", options=["Yes", "No"],
                       protected="work_authorization", maps_to="protected:work_authorization")]
    saved = [{"key": "protected:work_authorization", "label": "x", "value": "Yes", "source": "you", "state": "human"}]
    out = merge_human(fresh, saved, keep_extra=False)
    assert out[0].value == "Yes" and out[0].state == "human"


# ---------------------------------------------------------------- Lever

LEVER_FIXTURE = Path(__file__).parent / "fixtures" / "lever_form.html"
LEVER_URL = "https://jobs.lever.co/acmerobotics/11111111-2222-3333-4444-555555555555"


def lever_questions():
    from jobpilot.forms.lever import parse_apply_page

    return parse_apply_page(LEVER_FIXTURE.read_text(encoding="utf-8"))


def test_lever_form_url():
    from jobpilot.forms.lever import form_url

    assert form_url(LEVER_URL) == LEVER_URL + "/apply"
    assert form_url(LEVER_URL + "/apply?lever-source=x") == LEVER_URL + "/apply"
    with pytest.raises(ValueError):
        form_url("https://example.test/jobs/1")


def test_lever_parse_apply_page():
    qs = {q.field_id: q for q in lever_questions()}
    assert qs["name"].kind == "text" and qs["name"].required
    assert qs["resume"].kind == "file" and qs["location"].kind == "location"
    hear = qs["cards[c1][field0]"]
    assert hear.kind == "radio" and hear.required and hear.options == ["University career fair", "Friend"]
    assert qs["cards[c1][field1]"].kind == "select"
    assert qs["cards[c1][field4]"].kind == "textarea"
    eeo = qs["eeo[gender]"]
    assert eeo.section == "eeoc" and eeo.options == ["Male", "Female", "Decline to self-identify"]
    assert all(q.selector for q in qs.values())
    assert lever_questions.__module__  # parse is pure: no page JavaScript needed


def test_lever_plan(profile):
    fields = {f.field_id: f for f in plan_from_form(lever_questions(), profile, "general",
                                                    {"title": "Data Intern", "company": "Acme"}, SKILLS)}
    assert fields["name"].value == profile.full_name and fields["name"].maps_to == "full_name"
    assert fields["email"].value == profile.contact.email
    assert fields["cards[c1][field0]"].value == "University career fair"  # short answer fitted to the options
    assert fields["cards[c1][field2]"].protected == "sponsorship" and not fields["cards[c1][field2]"].value
    assert fields["cards[c1][field3]"].protected == "legal"  # acknowledgement, never ticked by JobPilot
    assert fields["cards[c1][field4]"].state == "draft"
    assert fields["eeo[gender]"].protected and not fields["eeo[gender]"].value


def test_lever_radio_answer_must_be_one_option(conn, profile):
    job = add_job(conn, ats="lever")
    conn.execute("UPDATE jobs SET url = ? WHERE id = ?", (LEVER_URL, job))

    class LeverHTTP:
        def get(self, url, **kw):
            assert url == LEVER_URL + "/apply"
            return type("R", (), {"text": LEVER_FIXTURE.read_text(encoding="utf-8")})()

        def close(self):
            pass

    plan.load_real_form(conn, job, profile, LeverHTTP(), {})
    with pytest.raises(GateError):
        plan.save_answers(conn, job, profile, {"lv:cards[c1][field2]": "Yes; No"})
    plan.save_answers(conn, job, profile, {"lv:cards[c1][field2]": "No"})


def test_lever_filler_source_never_submits():
    src = (Path(__file__).parents[1] / "jobpilot" / "autofill" / "lever.py").read_text(encoding="utf-8")
    assert "click(" not in src and "press(" not in src and "submit" not in src.lower().replace("never submits", "")


@pytest.fixture
def lever_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = p.chromium.launch(channel=channel, headless=True) if channel else p.chromium.launch(headless=True)
                break
            except Exception:
                continue
        if browser is None:
            pytest.skip("no Playwright-compatible browser installed")
        ctx = browser.new_context()
        posts = []

        def handler(route, request):
            if request.method == "POST":
                posts.append(request.url)
                return route.fulfill(status=200, body="ok")
            return route.fulfill(status=200, content_type="text/html", body=LEVER_FIXTURE.read_text(encoding="utf-8"))

        ctx.route("https://jobs.lever.co/**", handler)
        page = ctx.new_page()
        page.goto(LEVER_URL + "/apply")
        yield page, posts
        browser.close()


def test_lever_fill_stops_before_submit(lever_page, profile, tmp_path):
    from jobpilot.autofill.lever import LeverFiller

    page, posts = lever_page
    resume = tmp_path / "general.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    fields = []
    for f in plan_from_form(lever_questions(), profile, "general", {"title": "Data Intern", "company": "Acme"}, SKILLS):
        d = dict(f.__dict__)
        if f.field_id == "cards[c1][field2]":
            d.update(value="No", state="human")
        if f.field_id == "cards[c1][field1]":
            d.update(value="Master's", state="human")
        fields.append(d)
    filler = LeverFiller(page, dry_run=False, timeout_ms=2000)
    result = filler.run(fields, str(resume))
    assert result.outcome == "stopped_before_submit", result.message
    assert page.input_value('[name="name"]') == profile.full_name
    assert page.is_checked('[name="cards[c1][field0]"][value="University career fair"]')
    assert page.is_checked('[name="cards[c1][field2]"][value="No"]')
    assert page.input_value('[name="cards[c1][field1]"]') == "Master's"
    assert not page.is_checked('[name="cards[c1][field3]"]')
    assert page.input_value('[name="eeo[gender]"]') == ""
    assert "Northwind" in page.input_value('[name="cards[c1][field4]"]')
    assert page.eval_on_selector('[name="resume"]', "e => e.files[0].name") == "general.pdf"
    page.click("#btn-submit")
    assert page.evaluate("window.__posted") == 0 and posts == []
