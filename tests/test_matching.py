import json

import pytest

from jobpilot.core import db
from jobpilot.matching.engine import profile_hash, run_local
from jobpilot.matching.gaps import gap_report, to_csv, to_markdown
from jobpilot.matching.local import profile_evidence, score_job
from jobpilot.matching.skills import load_taxonomy, split_sections
from jobpilot.onboarding.interview import profile_from_resume
from jobpilot.onboarding.profile import Targets
from jobpilot.onboarding.resume_parser import parse_text
from jobpilot.samples import sample_resume_text

TAX = load_taxonomy()

JOB_DESC = """About the team
We build logistics software. Benefits include an annual bonus.

Minimum Qualifications:
• Experience with Python and SQL
• Familiarity with Kubernetes and Go
• Coursework in operating systems

Preferred Qualifications
• Experience with PyTorch
• AWS experience
"""


@pytest.fixture
def profile():
    p = profile_from_resume(parse_text(sample_resume_text()))
    p.targets = Targets(fields=["software engineering"], role_types=["internship"], locations=["Chicago"],
                        remote_preference="hybrid")
    return p


def job(**kw):
    base = {"id": 1, "title": "Software Engineer Intern", "company": "Acme", "field": "", "description": JOB_DESC,
            "location": "Chicago, IL", "remote": "", "role_type": "internship"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- skill detection

@pytest.mark.parametrize("text,expected,absent", [
    ("Experience in C and C++", {"c", "cpp"}, set()),
    ("Series C startup, C-suite exposure", set(), {"c"}),
    ("We use R and Python", {"r", "python"}, set()),
    ("R&D team", set(), {"r"}),
    ("Write services in Go", {"go"}, set()),
    ("Go above and beyond; your go-to person", set(), {"go"}),
    ("the rest of the team", set(), {"rest"}),
    ("Build REST APIs in Node.js", {"rest", "nodejs"}, set()),
    ("Spring 2027 internship", set(), {"spring"}),
    ("Spring Boot microservices", {"spring", "microservices"}, set()),
    ("JavaScript and TypeScript", {"javascript", "typescript"}, {"java"}),
    (".NET and C#", {"csharp"}, set()),
    ("Apache Spark and Kafka", {"spark", "kafka"}, set()),
])
def test_find(text, expected, absent):
    found = set(TAX.find(text))
    assert expected <= found
    assert not (absent & found)


def test_implies_and_siblings():
    assert {"sql", "postgresql"} <= TAX.expand_implied({"postgresql"})
    assert "tensorflow" in TAX.siblings("pytorch")


def test_split_sections():
    s = split_sections(JOB_DESC)
    assert "Kubernetes" in s["required"] and "Python" in s["required"]
    assert "PyTorch" in s["preferred"] and "AWS" in s["preferred"]
    assert "bonus" in s["other"]  # benefits "bonus" doesn't make anything preferred


# ---------------------------------------------------------------- scoring

def test_evidence_comes_from_profile(profile):
    ev = profile_evidence(profile, TAX)
    assert "Northwind" in ev.strong["postgresql"]
    assert "sql" in ev.strong  # implied by PostgreSQL
    assert ev.listed["c"].startswith("Skills:")
    assert "operating_systems" in ev.coursework


def test_score_breakdown(profile):
    r = score_job(job(), profile, TAX)
    status = {s.key: (s.status, s.importance) for s in r.skills}
    assert status["python"] == ("have", "required")
    assert status["kubernetes"] == ("partial", "required")  # related: Docker
    assert status["go"] == ("missing", "required")
    assert status["operating_systems"] == ("partial", "required")  # coursework only
    assert status["pytorch"] == ("missing", "preferred")  # scikit-learn is not in the deep-learning family
    assert status["aws"] == ("have", "preferred")
    assert "missing Go" in r.reason and "in Chicago" in r.reason
    assert 50 < r.score < 90
    for s in r.skills:
        if s.status != "missing":
            assert s.evidence  # every have/partial points at profile text


def test_title_only_is_low_confidence_and_capped(profile):
    r = score_job(job(description="", title="Machine Learning Engineer Intern"), profile, TAX)
    assert r.confidence == "low" and r.score <= 72
    assert r.reason.startswith("No description yet")


def test_grad_degree_penalty(profile):
    base = score_job(job(), profile, TAX).score
    phd = score_job(job(title="PhD Software Engineer Intern"), profile, TAX).score
    assert base - phd == 25
    both = score_job(job(title="BS/MS/PhD Software Engineer Intern"), profile, TAX).score
    assert both == base


def test_location_and_field_fit(profile):
    near = score_job(job(), profile, TAX).score
    far = score_job(job(location="Austin, TX"), profile, TAX)
    assert far.score < near and "outside your locations" in far.reason
    remote = score_job(job(location="Remote", remote="remote"), profile, TAX).score
    assert remote == near
    off = score_job(job(title="Account Executive Intern"), profile, TAX)
    assert "outside your target fields" in off.reason


# ---------------------------------------------------------------- engine + gaps

@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        yield c


def _insert(conn, i, title, desc, status="found", location="Chicago, IL"):
    conn.execute(
        """INSERT INTO jobs (id, dedupe_key, company_key, url_key, title, company, location, url, description,
               date_found, last_seen, status) VALUES (?, ?, 'acme', ?, ?, 'Acme', ?, ?, ?, 'now', 'now', ?)""",
        (i, f"k{i}", f"u{i}", title, location, f"https://x/{i}", desc, status),
    )


def test_run_local_and_rescore_on_profile_change(conn, profile):
    _insert(conn, 1, "Software Engineer Intern", JOB_DESC)
    _insert(conn, 2, "Account Executive", "Sell things. Excel required.", location="Paris")
    _insert(conn, 3, "Backend Intern", JOB_DESC, status="approved")
    conn.commit()
    s = run_local(conn, profile, TAX, min_score=50)
    assert s.scored == 2  # approved job is left alone
    st = dict(conn.execute("SELECT id, status FROM jobs").fetchall())
    assert st == {1: "matched", 2: "found", 3: "approved"}
    assert run_local(conn, profile, TAX, 50).scored == 0  # nothing changed
    profile.skills.append("Go")
    assert run_local(conn, profile, TAX, 50).scored == 2  # profile changed -> rescore
    skills = json.loads(conn.execute("SELECT skills_json FROM matches WHERE job_id = 1").fetchone()[0])["skills"]
    assert next(s for s in skills if s["key"] == "go")["status"] == "have"


def test_profile_hash_ignores_contact_info(profile):
    h = profile_hash(profile)
    profile.contact.phone = "555-0000"
    assert profile_hash(profile) == h


def test_gap_report(conn, profile, tmp_path):
    for i in range(1, 4):
        _insert(conn, i, f"Software Engineer Intern {i}", JOB_DESC)
    _insert(conn, 4, "Platform Intern", "Requirements:\n• Rust and Go experience")
    conn.commit()
    run_local(conn, profile, TAX, 0)
    rows, partial, total = gap_report(conn, TAX, min_score=0)
    assert total == 4
    top = rows[0]
    assert top.key == "go" and top.jobs == 4 and top.required_in == 4
    assert "Tour of Go" in top.learn
    assert all(g.key != "python" for g in rows + partial)  # you have it
    assert "kubernetes" in {g.key for g in partial}  # Docker -> partial credit
    md = to_markdown(rows, partial, total, 0)
    assert "| 1 | **Go**" in md and "partly have" in md and "by field" in md
    out = tmp_path / "gaps.csv"
    to_csv(rows, partial, out)
    assert out.read_text(encoding="utf-8").splitlines()[1].startswith("Go,missing,")


def test_snippets_cut_at_word_boundaries():
    text = "We are looking for someone with hands-on, production experience writing Python services at scale every day."
    snip = TAX.find(text)["python"]
    assert "Python" in snip
    body = snip.strip("…")
    assert text.find(body) != -1  # a real substring
    assert text[text.find(body) - 1] in (" ", "") if text.find(body) > 0 else True
    end = text.find(body) + len(body)
    assert end == len(text) or text[end] == " "
