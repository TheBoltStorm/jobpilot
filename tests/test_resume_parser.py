import pytest

from jobpilot.onboarding.resume_parser import extract_text, parse_resume, parse_text


def _check_sample(p):
    c = p.contact
    assert c.name == "Jordan Rivera"
    assert c.email == "jordan.rivera@example.com"
    assert c.phone == "(555) 010-4477"
    assert c.location == "Springfield, IL"
    assert c.github == "github.com/jrivera-demo"
    assert c.linkedin == "linkedin.com/in/jordan-rivera-demo"

    assert len(p.education) == 1
    ed = p.education[0]
    assert ed.school == "State University of Example"
    assert ed.degree == "Bachelor of Science"
    assert ed.field_of_study == "Computer Science"
    assert (ed.start, ed.end, ed.gpa) == ("Aug 2023", "May 2027", "3.62")
    assert "Machine Learning" in ed.coursework

    assert [e.title for e in p.experience] == ["Software Engineering Intern", "Undergraduate Teaching Assistant"]
    assert [e.organization for e in p.experience] == ["Northwind Logistics", "State University of Example"]
    assert p.experience[0].location == "Chicago, IL"
    assert p.experience[1].end == "Present"
    assert len(p.experience[0].bullets) == 2
    assert p.experience[0].bullets[0].endswith("6 hours a week.")  # wrapped PDF line rejoined

    assert [e.title for e in p.projects] == ["CampusEats", "Course Grade Predictor"]
    assert {"Python", "FastAPI", "Docker", "GitHub Actions"} <= set(p.skills)
    assert p.skill_groups["Languages"][:2] == ["Python", "Java"]
    assert p.warnings == []


def test_parse_plain_text(sample_text):
    _check_sample(parse_text(sample_text))


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_parse_files(sample_files, fmt):
    _, parsed = parse_resume(sample_files[fmt])
    _check_sample(parsed)


def test_items_record_source_lines(sample_text):
    p = parse_text(sample_text)
    lines = sample_text.split("\n")
    assert lines[p.contact.lines["name"] - 1] == "Jordan Rivera"
    assert "Northwind" in lines[p.experience[0].line - 1]


def test_unsure_header_is_left_blank_not_guessed():
    text = "Sam Lee\nsam@example.com\nEXPERIENCE\nBlue Fern | Green Leaf | Jan 2024 - Mar 2024\n• Did a thing."
    p = parse_text(text)
    e = p.experience[0]
    assert e.title == "" and e.organization == ""  # two candidates, no title keyword: ask the human
    assert e.header.startswith("Blue Fern")
    assert any("unsure" in w for w in p.warnings)


def test_no_sections_warns():
    p = parse_text("Just some text\nwith nothing useful")
    assert any("No section headings" in w for w in p.warnings)


def test_heading_variants():
    text = "A B\na@b.co\nWork History:\nEngineer | Acme | 2022 - 2023\nTechnical Skills\nGo; Rust | Zig"
    p = parse_text(text)
    assert p.experience[0].title == "Engineer"
    assert p.experience[0].organization == "Acme"
    assert p.skills == ["Go", "Rust", "Zig"]


def test_unsupported_format(tmp_path):
    f = tmp_path / "r.rtf"
    f.write_text("x")
    with pytest.raises(ValueError):
        extract_text(f)
