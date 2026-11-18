"""Skill Gap Report: which missing skills show up most across your matched jobs."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from jobpilot.matching.skills import Taxonomy

WEIGHT = {"required": 1.0, "preferred": 0.5, "mentioned": 0.25}
GENERIC_LEARN = "Find the official getting-started guide, then build a small project that uses it."


@dataclass
class GapRow:
    key: str
    name: str
    category: str
    jobs: int = 0  # matched jobs that ask for it (and you're missing / partly have it)
    required_in: int = 0
    weight: float = 0.0
    by_field: Counter = field(default_factory=Counter)
    example_jobs: list[str] = field(default_factory=list)
    learn: str = ""


def gap_report(conn: sqlite3.Connection, tax: Taxonomy, min_score: int = 0, field_tag: str | None = None,
               include_closed: bool = False) -> tuple[list[GapRow], list[GapRow], int]:
    """Return (missing skills ranked, partly-had skills ranked, number of jobs considered)."""
    sql = """SELECT j.id, j.title, j.company, m.tags, m.skills_json FROM matches m JOIN jobs j ON j.id = m.job_id
             WHERE m.score >= ? AND j.description != ''"""  # title-only jobs would just add noise
    if not include_closed:
        sql += " AND j.status NOT IN ('posting_closed', 'rejected')"
    buckets: dict[str, dict[str, GapRow]] = {"missing": {}, "partial": {}}
    total = 0
    for r in conn.execute(sql, (min_score,)):
        tags = [t for t in (r["tags"] or "").split(",") if t] or ["other"]
        if field_tag and field_tag not in tags:
            continue
        total += 1
        seen: set[str] = set()
        for s in json.loads(r["skills_json"]).get("skills", []):
            key = s["key"]
            if key in seen or s["status"] == "have":
                continue
            seen.add(key)
            sk = tax.skills.get(key)
            row = buckets[s["status"]].setdefault(
                key, GapRow(key, sk.name if sk else s["name"], sk.cat if sk else "Other",
                            learn=(sk.learn if sk and sk.learn else GENERIC_LEARN)))
            row.jobs += 1
            row.weight += WEIGHT[s["importance"]]
            row.required_in += s["importance"] == "required"
            for t in tags:
                row.by_field[t] += 1
            if len(row.example_jobs) < 3:
                row.example_jobs.append(f"{r['company']} - {r['title']}")
    def rank(d: dict[str, GapRow]) -> list[GapRow]:
        return sorted(d.values(), key=lambda g: (-g.weight, -g.jobs, g.name))

    return rank(buckets["missing"]), rank(buckets["partial"]), total


def by_field(rows: list[GapRow]) -> dict[str, list[GapRow]]:
    out: dict[str, list[GapRow]] = defaultdict(list)
    for g in rows:
        for tag in g.by_field:
            out[tag].append(g)
    return {t: sorted(gs, key=lambda g: -g.by_field[t]) for t, gs in sorted(out.items())}


def to_markdown(missing: list[GapRow], partial: list[GapRow], total: int, min_score: int, top: int = 25) -> str:
    lines = [
        "# Skill Gap Report", "",
        f"Based on {total} matched jobs with descriptions (score {min_score}+). Ranked by how often each skill",
        "is asked for, counting required skills fully, preferred ones half, and mere mentions a quarter.", "",
        "## Skills you're missing", "",
        "| # | Skill | Asked for in | Required in | How to learn it |",
        "|---|---|---|---|---|",
    ]
    for i, g in enumerate(missing[:top], 1):
        lines.append(f"| {i} | **{g.name}** ({g.category}) | {g.jobs} jobs | {g.required_in} | {g.learn} |")
    if partial:
        lines += ["", "## Skills you partly have (worth strengthening)", "",
                  "| Skill | Asked for in | Required in | How to strengthen it |", "|---|---|---|---|"]
        for g in partial[:top]:
            lines.append(f"| **{g.name}** | {g.jobs} jobs | {g.required_in} | {g.learn} |")
    lines += ["", "## Missing skills by field", ""]
    for tag, gs in by_field(missing).items():
        lines.append(f"**{tag}**: " + ", ".join(f"{g.name} ({g.by_field[tag]})" for g in gs[:8]))
        lines.append("")
    return "\n".join(lines)


def to_csv(missing: list[GapRow], partial: list[GapRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["skill", "you_have", "category", "jobs_asking", "required_in", "weight", "fields",
                    "example_jobs", "how_to_learn"])
        for status, rows in (("missing", missing), ("partial", partial)):
            for g in rows:
                w.writerow([g.name, status, g.category, g.jobs, g.required_in, round(g.weight, 2),
                            "; ".join(f"{t}:{n}" for t, n in g.by_field.most_common()), " | ".join(g.example_jobs),
                            g.learn])
