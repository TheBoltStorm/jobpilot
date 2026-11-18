"""Managing resume versions: copy into data/resumes/, parse, record in SQLite."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from jobpilot.config import Paths
from jobpilot.core import db
from jobpilot.onboarding.resume_parser import ParsedResume, parse_resume, sha256_file

ALLOWED = {".pdf", ".docx"}


def safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip()).strip("-").lower()
    if not cleaned:
        raise ValueError("Resume label must contain letters or numbers.")
    return cleaned


def add_resume(paths: Paths, src: str | Path, label: str = "default", make_default: bool = False) -> tuple[str, ParsedResume]:
    src = Path(src).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"No file at {src}")
    if src.suffix.lower() not in ALLOWED:
        raise ValueError(f"Resume must be PDF or DOCX, got {src.suffix}")
    label = safe_label(label)
    dest = paths.resumes / f"{label}{src.suffix.lower()}"
    for old in paths.resumes.glob(f"{label}.*"):  # replacing a version with a different format
        if old != dest:
            old.unlink()
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    text, parsed = parse_resume(dest)
    with db.connect(paths.db) as conn:
        db.upsert_resume(conn, label=label, path=str(dest), sha256=sha256_file(dest), text=text,
                         parsed=parsed.to_dict(), make_default=make_default)
    return label, parsed
