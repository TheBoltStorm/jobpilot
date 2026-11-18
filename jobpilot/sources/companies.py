"""data/companies.yaml: which Greenhouse/Lever/Ashby boards to check."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from jobpilot.sources.base import Company
from jobpilot.sources.fields import tags_for

HEADER = """\
# Companies whose public job boards JobPilot checks. Edit freely.
# ats: greenhouse | lever | ashby. token: the board name in the company's job-board URL, e.g.
#   boards.greenhouse.io/<token>, jobs.lever.co/<token>, jobs.ashbyhq.com/<token>
"""


def load_seed() -> list[Company]:
    text = resources.files("jobpilot.sources").joinpath("data/companies_seed.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return [Company(name=c["name"], ats=ats, token=c["token"], fields=list(c.get("fields", [])))
            for ats, rows in data.items() for c in rows]


def starter_companies(target_fields: list[str]) -> list[Company]:
    """Seed companies matching any of the user's target fields (all of them if none given)."""
    seed = load_seed()
    wanted = tags_for(target_fields)
    if not wanted:
        return seed
    return [c for c in seed if wanted & set(c.fields)]


def load_companies(path: Path) -> list[Company]:
    if not path.is_file():
        return []
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    out = []
    for r in rows:
        if not (r.get("ats") and r.get("token")):
            continue
        out.append(Company(name=r.get("name") or r["token"], ats=r["ats"].lower(), token=str(r["token"]),
                           fields=list(r.get("fields") or [])))
    return out


def save_companies(companies: list[Company], path: Path) -> None:
    rows = [{"name": c.name, "ats": c.ats, "token": c.token, "fields": c.fields} for c in companies]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + yaml.safe_dump(rows, sort_keys=False, default_flow_style=None, width=120),
                    encoding="utf-8")
