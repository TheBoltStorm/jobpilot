"""Score stored jobs against the profile and save results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

from jobpilot.core.db import now_iso
from jobpilot.matching.local import SCORER_VERSION, MatchResult, profile_evidence, score_job
from jobpilot.matching.skills import Taxonomy
from jobpilot.onboarding.profile import Profile

# Jobs in these statuses get (re)scored, so the review page always shows a current score.
SCORABLE = ("found", "matched", "ready_to_fill", "needs_human", "account_wall", "custom_site")
# Only these move between found and matched. Prepared jobs keep their bucket; anything you've
# approved, submitted or rejected isn't touched at all.
RECLASSIFY = ("found", "matched")


def profile_hash(profile: Profile) -> str:
    """Changes when anything that affects scoring changes, so stale scores get redone."""
    relevant = profile.model_dump(include={"education", "experience", "projects", "skills", "certifications", "targets"})
    relevant["_scorer"] = SCORER_VERSION
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class MatchSummary:
    scored: int = 0
    matched: int = 0
    skipped_fresh: int = 0
    engine: str = "local"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def jobs_to_score(conn: sqlite3.Connection, phash: str, rescore: bool, limit: int | None) -> list[sqlite3.Row]:
    sql = f"""
        SELECT j.* FROM jobs j LEFT JOIN matches m ON m.job_id = j.id
        WHERE j.status IN ({','.join('?' * len(SCORABLE))})
          AND (? OR m.job_id IS NULL OR m.profile_hash != ? OR m.description_len != LENGTH(j.description))
        ORDER BY j.date_posted DESC, j.id DESC
    """
    params: list = [*SCORABLE, int(rescore), phash]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def save_match(conn: sqlite3.Connection, job: sqlite3.Row, result: MatchResult, engine: str, phash: str,
               min_score: int) -> bool:
    conn.execute(
        """INSERT INTO matches (job_id, score, confidence, reason, tags, skills_json, engine, profile_hash,
                                description_len, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET score=excluded.score, confidence=excluded.confidence,
             reason=excluded.reason, tags=excluded.tags, skills_json=excluded.skills_json, engine=excluded.engine,
             profile_hash=excluded.profile_hash, description_len=excluded.description_len, scored_at=excluded.scored_at""",
        (job["id"], result.score, result.confidence, result.reason, ",".join(result.tags),
         json.dumps(result.skills_json()), engine, phash, len(job["description"] or ""), now_iso()),
    )
    # Title-only estimates never count as a match: a job needs a description to be judged.
    new_status = "matched" if result.score >= min_score and job["description"] else "found"
    conn.execute(
        f"UPDATE jobs SET status = ? WHERE id = ? AND status IN ({','.join('?' * len(RECLASSIFY))})",
        (new_status, job["id"], *RECLASSIFY),
    )
    return new_status == "matched" and job["status"] in RECLASSIFY


def score_one(conn: sqlite3.Connection, job_id: int, profile: Profile, tax: Taxonomy, min_score: int) -> int:
    """Score one job now (e.g. one you just added). A job you added stays on your list whatever its score."""
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    result = score_job(job, profile, tax)
    phash = profile_hash(profile)
    conn.execute(
        """INSERT INTO matches (job_id, score, confidence, reason, tags, skills_json, engine, profile_hash,
                                description_len, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, 'local', ?, ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET score=excluded.score, confidence=excluded.confidence,
             reason=excluded.reason, tags=excluded.tags, skills_json=excluded.skills_json, engine=excluded.engine,
             profile_hash=excluded.profile_hash, description_len=excluded.description_len, scored_at=excluded.scored_at""",
        (job_id, result.score, result.confidence, result.reason, ",".join(result.tags),
         json.dumps(result.skills_json()), phash, len(job["description"] or ""), now_iso()))
    return result.score


def run_local(conn: sqlite3.Connection, profile: Profile, tax: Taxonomy, min_score: int,
              rescore: bool = False, limit: int | None = None, log: Callable[[str], None] = print) -> MatchSummary:
    phash = profile_hash(profile)
    ev = profile_evidence(profile, tax)
    summary = MatchSummary()
    started = now_iso()
    rows = jobs_to_score(conn, phash, rescore, limit)
    with conn:
        for job in rows:
            result = score_job(job, profile, tax, ev)
            summary.scored += 1
            summary.matched += save_match(conn, job, result, "local", phash, min_score)
        conn.execute(
            "INSERT INTO match_runs (engine, started_at, finished_at, jobs_scored) VALUES ('local', ?, ?, ?)",
            (started, now_iso(), summary.scored),
        )
    return summary
