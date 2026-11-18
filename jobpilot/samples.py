"""A made-up resume for demos and tests. Every name, school, company and number here is fictional."""

from __future__ import annotations

from pathlib import Path

SAMPLE_RESUME = {
    "name": "Jordan Rivera",
    "contact": "Springfield, IL | (555) 010-4477 | jordan.rivera@example.com | github.com/jrivera-demo | linkedin.com/in/jordan-rivera-demo",
    "sections": [
        ("Education", [
            ("head", "State University of Example | Springfield, IL"),
            ("head", "Bachelor of Science in Computer Science | Aug 2023 - May 2027"),
            ("bullet", "GPA: 3.62/4.0"),
            ("bullet", "Relevant Coursework: Data Structures, Algorithms, Databases, Operating Systems, Machine Learning"),
        ]),
        ("Experience", [
            ("head", "Software Engineering Intern | Northwind Logistics | Chicago, IL | Jun 2025 - Aug 2025"),
            ("bullet", "Built a Python service that parsed 40k daily shipment events into PostgreSQL, cutting manual reconciliation by about 6 hours a week."),
            ("bullet", "Wrote REST endpoints in FastAPI with pytest coverage; reviewed pull requests for two other interns."),
            ("head", "Undergraduate Teaching Assistant | State University of Example | Aug 2024 - Present"),
            ("bullet", "Run weekly lab sections for 30 students in Intro to Programming (Java)."),
            ("bullet", "Hold office hours and grade assignments; wrote a small autograder script in Python."),
        ]),
        ("Projects", [
            ("head", "CampusEats | Jan 2025 - Apr 2025"),
            ("bullet", "React + Node.js web app for ordering from dining halls; 300 student users during a pilot."),
            ("bullet", "Deployed on AWS with Docker; set up GitHub Actions CI."),
            ("head", "Course Grade Predictor"),
            ("bullet", "Trained a scikit-learn model on anonymized course data to flag at-risk students; wrote up results in a Jupyter notebook."),
        ]),
        ("Skills", [
            ("line", "Languages: Python, Java, JavaScript, SQL, C"),
            ("line", "Frameworks: FastAPI, React, Node.js, pandas, scikit-learn"),
            ("line", "Tools: Git, Docker, AWS, PostgreSQL, Linux, GitHub Actions"),
        ]),
    ],
}


def sample_resume_text() -> str:
    lines = [SAMPLE_RESUME["name"], SAMPLE_RESUME["contact"], ""]
    for heading, items in SAMPLE_RESUME["sections"]:
        lines.append(heading.upper())
        for kind, text in items:
            lines.append(f"• {text}" if kind == "bullet" else text)
        lines.append("")
    return "\n".join(lines)


def write_sample_docx(path: str | Path) -> Path:
    import docx

    path = Path(path)
    d = docx.Document()
    d.add_heading(SAMPLE_RESUME["name"], level=0)
    d.add_paragraph(SAMPLE_RESUME["contact"])
    for heading, items in SAMPLE_RESUME["sections"]:
        d.add_heading(heading.upper(), level=1)
        for kind, text in items:
            if kind == "bullet":
                d.add_paragraph(text, style="List Bullet")
            else:
                p = d.add_paragraph()
                p.add_run(text).bold = kind == "head"
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))
    return path


def write_sample_pdf(path: str | Path) -> Path:
    """Needs fpdf2 (dev extra)."""
    from fpdf import FPDF

    path = Path(path)
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, SAMPLE_RESUME["name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, SAMPLE_RESUME["contact"], new_x="LMARGIN", new_y="NEXT")
    for heading, items in SAMPLE_RESUME["sections"]:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, heading.upper(), new_x="LMARGIN", new_y="NEXT")
        for kind, text in items:
            pdf.set_font("Helvetica", "B" if kind == "head" else "", 10)
            prefix = "- " if kind == "bullet" else ""
            pdf.multi_cell(0, 5, prefix + text, new_x="LMARGIN", new_y="NEXT")
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path
