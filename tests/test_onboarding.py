import pytest
from pydantic import ValidationError

from jobpilot.core import db
from jobpilot.onboarding.interview import run_interview
from jobpilot.onboarding.profile import Profile, load_profile, save_profile
from jobpilot.onboarding.resume_parser import parse_text
from jobpilot.onboarding.resumes import add_resume

TARGETS = {
    "Target fields": "software engineering, data science",
    "Target role titles": "backend intern",
    "Role types": "internship, co-op",
    "Preferred locations": "Chicago, Remote",
    "Remote preference": "hybrid",
}


def test_accepting_defaults_copies_only_resume_facts(sample_text, scripted_io):
    io = scripted_io(TARGETS)
    p = run_interview(io, parse_text(sample_text))
    assert (p.contact.first_name, p.contact.last_name) == ("Jordan", "Rivera")
    assert (p.contact.city, p.contact.state) == ("Springfield", "IL")
    assert p.contact.country == ""  # not on the resume -> not invented
    assert p.education[0].degree == "Bachelor of Science"
    assert p.experience[0].company == "Northwind Logistics"
    assert p.projects[1].name == "Course Grade Predictor"
    assert p.targets.role_types == ["internship", "co-op"]
    assert p.targets.remote_preference == "hybrid"
    assert p.short_answers == {}


def test_user_can_drop_and_edit_entries(sample_text, scripted_io):
    answers = dict(TARGETS)
    # Drop the TA job (second "Keep this entry?" prompt for experience).
    keeps = iter(["", "", "n", "", ""])  # edu keep, exp1 keep, exp2 drop, proj1, proj2
    io = scripted_io(answers)
    orig_ask = io.ask

    def ask(prompt, default=""):
        if prompt.startswith("Keep this entry?"):
            return next(keeps)
        if prompt.startswith("Job title") and default == "Software Engineering Intern":
            return "Software Engineer Intern"
        return orig_ask(prompt, default)

    io.ask = ask
    p = run_interview(io, parse_text(sample_text))
    assert [e.title for e in p.experience] == ["Software Engineer Intern"]


def test_protected_short_answer_refused(sample_text, scripted_io):
    seq = []
    io = scripted_io(TARGETS)
    questions = iter(["Do you require visa sponsorship?", "Willing to relocate?", ""])
    orig_ask = io.ask

    def ask(prompt, default=""):
        if prompt.startswith("New question"):
            return next(questions)
        if prompt.startswith("Your answer"):
            return "Yes"
        return orig_ask(prompt, default)

    io.ask = ask
    p = run_interview(io, parse_text(sample_text))
    assert p.short_answers == {"Willing to relocate?": "Yes"}
    assert any("protected question" in o for o in io.output)


def test_profile_rejects_protected_keys_on_load(tmp_path):
    with pytest.raises(ValidationError):
        Profile(short_answers={"Salary expectations": "100k"})


def test_invalid_role_type_rejected():
    with pytest.raises(ValidationError):
        Profile.model_validate({"targets": {"role_types": ["gig"]}})


def test_profile_roundtrip(tmp_path, sample_text, scripted_io):
    p = run_interview(scripted_io(TARGETS), parse_text(sample_text))
    path = tmp_path / "profile.yaml"
    save_profile(p, path)
    assert path.read_text(encoding="utf-8").startswith("# JobPilot profile")
    assert load_profile(path) == p


def test_rerun_keeps_confirmed_values(sample_text, scripted_io):
    first = run_interview(scripted_io({**TARGETS, "Country": "USA"}), parse_text(sample_text))
    second = run_interview(scripted_io({}), parse_text(sample_text), existing=first)
    assert second.contact.country == "USA"
    assert second.targets.fields == ["software engineering", "data science"]


def test_multiple_resume_versions(paths, sample_files):
    add_resume(paths, sample_files["docx"], "General")
    add_resume(paths, sample_files["pdf"], "data-science", make_default=True)
    with db.connect(paths.db) as conn:
        rows = {r["label"]: r["is_default"] for r in db.list_resumes(conn)}
        assert rows == {"general": 0, "data-science": 1}
        assert db.get_resume(conn)["label"] == "data-science"
        assert db.set_default_resume(conn, "general")
        assert db.get_resume(conn)["label"] == "general"
    assert (paths.resumes / "general.docx").is_file()


def test_first_resume_becomes_default(paths, sample_files):
    add_resume(paths, sample_files["docx"], "v1")
    with db.connect(paths.db) as conn:
        assert db.get_resume(conn)["label"] == "v1"


def test_add_resume_rejects_bad_input(paths, tmp_path):
    with pytest.raises(FileNotFoundError):
        add_resume(paths, tmp_path / "missing.pdf")
    bad = tmp_path / "r.txt"
    bad.write_text("hi")
    with pytest.raises(ValueError):
        add_resume(paths, bad)
