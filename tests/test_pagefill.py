"""The browser button: reading any site's form, planning answers, filling only what you tick."""

from pathlib import Path

import pytest

from jobpilot.core import db
from jobpilot.review import pagefill, plan
from tests.test_review import add_job, make_profile

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "generic_form.html"
PAGE_JS = ROOT / "extension" / "page.js"
URL = "https://careers.example.com/apply/7"


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path="x.pdf", sha256="0", text="", parsed={}, make_default=True)
        yield c


@pytest.fixture
def page():
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
        pg = browser.new_page()
        pg.route("https://careers.example.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body=FIXTURE.read_text(encoding="utf-8")))
        pg.goto(URL)
        pg.add_script_tag(path=str(PAGE_JS))
        yield pg
        browser.close()


def by_label(fields, text):
    return next(f for f in fields if text.lower() in f["label"].lower())


def test_scan_reads_labels_of_every_kind(page):
    got = page.evaluate("window.__jobpilot.scan()")
    labels = [f["label"] for f in got["fields"]]
    assert not got["hasPassword"]
    for want in ("First Name", "Last Name", "Email address", "Phone number", "LinkedIn profile",
                 "Why do you want to work here?", "authorized to work", "visa sponsorship", "I agree", "Resume"):
        assert any(want.lower() in lab.lower() for lab in labels), (want, labels)
    assert by_label(got["fields"], "sponsorship")["kind"] == "radio"
    assert by_label(got["fields"], "sponsorship")["options"] == ["Yes", "No"]
    assert by_label(got["fields"], "authorized")["options"] == ["Yes", "No"]
    assert not any(f["name"] == "token" for f in got["fields"])  # hidden fields are ignored


def test_plan_fills_profile_facts_only(page, conn):
    profile = make_profile()
    got = page.evaluate("window.__jobpilot.scan()")
    p = pagefill.plan_page(conn, profile, URL, got["title"], got["fields"])
    f = {x["label"]: x for x in p["fields"]}
    first = by_label(p["fields"], "First Name")
    assert first["value"] == profile.contact.first_name and first["fill"]
    assert by_label(p["fields"], "Email")["value"] == profile.contact.email
    assert not by_label(p["fields"], "Phone")["can_fill"]  # not in this profile: never made up
    for protected in ("authorized", "sponsorship", "I agree"):
        x = by_label(p["fields"], protected)
        assert not x["can_fill"] and x["value"] == "", protected
    assert not by_label(p["fields"], "Resume")["can_fill"]
    assert p["job"] is None and len(f) == len(p["fields"])


def test_fill_types_only_ticked_answers_and_never_submits(page, conn):
    profile = make_profile()
    got = page.evaluate("window.__jobpilot.scan()")
    p = pagefill.plan_page(conn, profile, URL, got["title"], got["fields"])
    answers = [{"key": x["key"], "value": x["value"], "kind": x["kind"]} for x in p["fields"] if x["fill"]]
    left = [x["key"] for x in p["fields"] if not x["fill"] and not x["optional"]]
    res = page.evaluate("([a, l]) => window.__jobpilot.fill(a, l)", [answers, left])
    assert res["filled"] == len(answers) and not res["failed"]
    assert page.input_value("#fn") == profile.contact.first_name
    assert page.input_value("[name=contact_email]") == profile.contact.email
    assert page.input_value("#auth") == ""  # work authorization stays yours
    assert not page.is_checked("[name=agree]")
    assert page.evaluate("window.submitted") is False


def test_your_answer_for_this_job_is_reused_but_consent_never(conn):
    profile = make_profile()
    job = add_job(conn, title="Data Intern", company="Example Co")
    conn.execute("UPDATE jobs SET url = ?, url_key = ? WHERE id = ?", (URL, "careers.example.com/apply/7", job))
    plan.prepare(conn, job, profile)
    app = plan.load_application(conn, job)
    from jobpilot.review.plan import PlanField

    app.fields.append(PlanField("x1", "Will you now or in the future require visa sponsorship?", "No", "you", "human",
                                "sponsorship"))
    app.fields.append(PlanField("x2", "I agree to the terms and conditions", "Yes", "you", "human", "legal"))
    plan.save_application(conn, app)
    from jobpilot.sources.dedupe import canonical_url

    conn.execute("UPDATE jobs SET url_key = ? WHERE id = ?", (canonical_url(URL), job))
    fields = [{"key": "a", "kind": "radio", "label": "Will you now or in the future require visa sponsorship?",
               "options": ["Yes", "No"]},
              {"key": "b", "kind": "radio", "label": "I agree to the terms and conditions", "options": ["Yes", "No"]}]
    p = pagefill.plan_page(conn, profile, URL, "", fields)
    assert p["job"]["id"] == job
    sponsor, agree = p["fields"]
    assert sponsor["value"] == "No" and sponsor["fill"] and sponsor["state"] == "human"
    assert agree["value"] == "" and not agree["can_fill"]


def test_sign_in_pages_are_refused(conn):
    p = pagefill.plan_page(conn, make_profile(), URL, "", [{"key": "a", "kind": "password", "label": "Password"}])
    assert p["refused"] and not p["fields"]


def test_extension_never_submits():
    for name in ("page.js", "popup.js", "background.js"):
        src = (ROOT / "extension" / name).read_text(encoding="utf-8")
        assert ".submit(" not in src and "requestSubmit" not in src and "KeyboardEvent" not in src
        assert "type=submit" not in src.replace(" ", "")
    manifest = (ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    assert "<all_urls>" not in manifest and "cookies" not in manifest


def test_api_needs_the_key_and_refuses_websites(paths):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    client = TestClient(create_app(paths))
    key = pagefill.extension_key(paths.home)
    assert pagefill.extension_key(paths.home) == key  # stable
    assert client.get("/api/ext/hello").status_code == 401
    assert client.get("/api/ext/hello", headers={"x-jobpilot-key": "nope"}).status_code == 401
    ok = {"x-jobpilot-key": key}
    assert client.get("/api/ext/hello", headers=ok).json()["profile"] is True
    evil = {**ok, "origin": "https://evil.example"}
    assert client.get("/api/ext/hello", headers=evil).status_code == 403
    ext = {**ok, "origin": "chrome-extension://abcdef"}
    r = client.post("/api/ext/plan", headers=ext, json={"url": URL, "title": "t", "fields": [
        {"key": "0|jp0", "kind": "text", "label": "First Name"}]})
    assert r.status_code == 200 and r.json()["fields"][0]["fill"]
    r = client.post("/api/ext/job", headers=ext, json={"url": URL, "title": "Data Intern", "company": "Example Co",
                                                      "description": "Python and SQL"})
    assert r.status_code == 200 and r.json()["new"] and r.json()["path"].startswith("/jobs/")
    r = client.post("/api/ext/status", headers=ext, json={"url": URL})
    assert r.json()["job"]["title"] == "Data Intern"
    assert client.post("/api/ext/status", json={"url": URL}).status_code == 401
    r = client.post("/api/ext/filled", headers=ext, json={"url": URL, "filled": 3, "left": 2})
    assert r.json()["job_id"] == int(r.json()["job_id"])


def test_dashboard_pages(paths):
    from fastapi.testclient import TestClient

    from jobpilot.onboarding.profile import save_profile
    from jobpilot.web.app import create_app

    save_profile(make_profile(), paths.profile)
    app = create_app(paths)
    client = TestClient(app)
    home = client.get("/").text
    for nav in ("Home", "Find jobs", "Apply", "Applications", "Insights", "Settings", "+ Add a job", "Paste its link"):
        assert nav in home, nav
    assert "Runs</a>" not in home and "Skill gaps</a>" not in home  # moved out of the main menu
    assert client.get("/apply").status_code == 200
    assert "Advanced settings" in client.get("/settings").text and "Copy key" in client.get("/settings").text
    r = client.get("/qualify?region=US+West", follow_redirects=False)
    assert r.status_code == 303 and "show=qualify" in r.headers["location"] and "region=US+West" in r.headers["location"]
    assert "All jobs" in client.get("/jobs?show=all").text
    # Adding a link for a site JobPilot can't read asks for the title and company.
    r = client.post("/add", data={"csrf": app.state.csrf_token, "url": "https://careers.example.com/jobs/9"})
    assert r.status_code == 400 and "title and company" in r.text
    r = client.post("/add", data={"csrf": app.state.csrf_token, "url": "https://careers.example.com/jobs/9",
                                  "title": "Analyst", "company": "Example Co"})
    assert r.status_code == 200 and "Analyst" in r.text and "Added." in r.text
    assert client.post("/add", data={"csrf": "x", "url": "https://a.example/1"}).status_code == 403
