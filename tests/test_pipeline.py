"""Phase 6: `jobpilot run` and `jobpilot doctor`."""

import shutil
import subprocess

import pytest

from jobpilot.core import db
from jobpilot.onboarding.profile import save_profile
from jobpilot.pipeline import doctor, run_pipeline
from tests.test_review import make_profile


def test_run_chains_steps_in_order():
    calls = []

    def fake_main(argv):
        calls.append(argv)
        return 0

    assert run_pipeline("H", main=fake_main, log=lambda s: None) == 0
    assert [c[2] for c in calls] == ["discover", "enrich", "match", "prepare", "forms"]
    assert all(c[:2] == ["--home", "H"] for c in calls)
    assert not any("autofill" in c for c in calls)  # the pipeline never fills anything


def test_run_stops_when_matching_fails():
    calls = []

    def fake_main(argv):
        calls.append(argv[0])
        return 1 if argv[0] == "match" else 0

    assert run_pipeline(None, skip_discover=True, main=fake_main, log=lambda s: None) == 1
    assert calls == ["enrich", "match"]


def test_run_keeps_going_after_a_discovery_problem():
    def fake_main(argv):
        return 1 if argv[0] == "discover" else 0

    out = []
    assert run_pipeline(None, main=fake_main, log=out.append) == 1
    assert any("discover" in line and "problems" in line for line in out)


def test_doctor_flags_missing_setup(paths):
    out = []
    assert doctor(paths, check_browser=False, log=out.append) == 1
    text = "\n".join(out)
    assert "jobpilot onboard" in text and "resume add" in text


def test_doctor_all_good(paths):
    save_profile(make_profile(), paths.profile)
    resume = paths.resumes / "general.pdf"
    resume.write_bytes(b"%PDF-1.4")
    with db.connect(paths.db) as c:
        db.upsert_resume(c, label="general", path=str(resume), sha256="0", text="", parsed={}, make_default=True)
    paths.companies.write_text("- {name: Acme, ats: greenhouse, token: acme}\n", encoding="utf-8")
    out = []
    assert doctor(paths, check_browser=False, log=out.append) == 0, out


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_doctor_catches_tracked_personal_data(tmp_path):
    from jobpilot.config import Paths

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    paths = Paths(tmp_path / "mydata").ensure()
    save_profile(make_profile(), paths.profile)
    out = []
    assert doctor(paths, check_browser=False, log=out.append) == 1
    assert any("NOT gitignored" in line for line in out)
    (tmp_path / ".gitignore").write_text("mydata/\n", encoding="utf-8")
    out = []
    doctor(paths, check_browser=False, log=out.append)
    assert not any("NOT gitignored" in line for line in out)
