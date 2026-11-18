from __future__ import annotations

import pytest

from jobpilot.config import get_paths
from jobpilot.samples import sample_resume_text, write_sample_docx, write_sample_pdf


@pytest.fixture
def paths(tmp_path):
    return get_paths(tmp_path / "home")


@pytest.fixture(scope="session")
def sample_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("resumes")
    return {"docx": write_sample_docx(d / "fake.docx"), "pdf": write_sample_pdf(d / "fake.pdf")}


@pytest.fixture
def sample_text():
    return sample_resume_text()


class ScriptedIO:
    """Feeds canned answers; any unanswered prompt gets '' (keep default)."""

    def __init__(self, answers: dict[str, str] | None = None, sequence: list[str] | None = None):
        self.answers = answers or {}
        self.sequence = list(sequence or [])
        self.prompts: list[str] = []
        self.output: list[str] = []

    def ask(self, prompt: str, default: str = "") -> str:
        self.prompts.append(prompt)
        for key, val in self.answers.items():
            if prompt.startswith(key):
                return default if val == "" else ("" if val == "-" else val)
        if self.sequence:
            val = self.sequence.pop(0)
            return default if val == "" else ("" if val == "-" else val)
        return default

    def say(self, msg: str = "") -> None:
        self.output.append(msg)


@pytest.fixture
def scripted_io():
    return ScriptedIO
