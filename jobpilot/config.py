"""Paths and environment. All personal data lives under JOBPILOT_HOME (default ./data)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def profile(self) -> Path:
        return self.home / "profile.yaml"

    @property
    def db(self) -> Path:
        return self.home / "jobpilot.db"

    @property
    def resumes(self) -> Path:
        return self.home / "resumes"

    def ensure(self) -> "Paths":
        self.resumes.mkdir(parents=True, exist_ok=True)
        return self


def get_paths(home: str | Path | None = None) -> Paths:
    _load_dotenv(Path.cwd() / ".env")
    root = Path(home or os.environ.get("JOBPILOT_HOME") or Path.cwd() / "data")
    return Paths(root.resolve()).ensure()
