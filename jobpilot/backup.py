"""One-file backup of your JobPilot data: the database, profile, settings and resume files.

Left out on purpose: the browser profile (sign-in cookies for job sites), the browser-button key,
and any .env file (API keys). Restore by unzipping into a fresh data folder.
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from jobpilot.config import Paths

SKIP_DIRS = {"browser_profile", "__pycache__"}
SKIP_FILES = {"extension_key.txt", ".env"}


def default_name() -> str:
    return f"jobpilot-backup-{datetime.now():%Y-%m-%d-%H%M}.zip"


def make_backup(paths: Paths, dest: Path) -> int:
    """Write the zip. Returns how many files went in. The database is copied safely even while in use."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        if paths.db.is_file():
            copy = Path(tmp) / paths.db.name
            src, out = sqlite3.connect(paths.db), sqlite3.connect(copy)
            try:
                src.backup(out)
            finally:
                out.close()
                src.close()
            z.write(copy, paths.db.name)
            n += 1
        for f in sorted(paths.home.rglob("*")):
            rel = f.relative_to(paths.home)
            if not f.is_file() or rel.parts[0] in SKIP_DIRS or f.name in SKIP_FILES or f == dest:
                continue
            if f.name.startswith(paths.db.name):  # the db (copied above) and its -wal/-journal files
                continue
            if f.suffix == ".zip" and f.name.startswith("jobpilot-backup-"):
                continue
            z.write(f, rel.as_posix())
            n += 1
    return n
