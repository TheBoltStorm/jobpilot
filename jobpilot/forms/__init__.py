"""Real application forms: read questions from public ATS APIs and store them per job."""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

from jobpilot.core.db import now_iso
from jobpilot.core.http import PoliteClient
from jobpilot.enrich import target_for
from jobpilot.forms.greenhouse import FormQuestion, form_url_from_api, parse_questions

FORM_ATS = ("greenhouse",)  # Lever and Ashby forms come later; their jobs show standard fields for now


def fetch_form(conn: sqlite3.Connection, http: PoliteClient, job: sqlite3.Row, gh_tokens: dict[str, str],
               llm_map: Callable[[list[FormQuestion]], dict[str, str]] | None = None,
               profile=None) -> list[FormQuestion]:
    """Fetch and store the real questions for one job. Raises on failure.

    With `llm_map` (the optional Claude mapper) and a profile, labels the local rules can't
    place get a suggested profile key, stored with the question so it's only asked once.
    """
    if job["ats"] not in FORM_ATS:
        raise ValueError(f"reading {job['ats'] or 'this site'}'s forms isn't supported yet")
    target = target_for(job["ats"], job["url"], job["company"], gh_tokens)
    if target is None:
        raise ValueError("couldn't work out the Greenhouse board for this posting")
    data = http.get_json(target.api_url + "?questions=true")
    questions = parse_questions(data)
    if not questions:
        raise ValueError("the posting has no application questions (it may be closed)")
    if llm_map and profile is not None:
        from jobpilot.review.mapping import unmapped

        todo = unmapped(questions, profile)
        if todo:
            try:
                suggested = llm_map(todo)
            except Exception:  # optional helper; unmapped questions just stay NEEDS_HUMAN
                suggested = {}
            for q in questions:
                q.model_key = suggested.get(q.key, "")
    conn.execute(
        """INSERT INTO forms (job_id, ats, form_url, questions_json, fetched_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET form_url=excluded.form_url, questions_json=excluded.questions_json,
             fetched_at=excluded.fetched_at""",
        (job["id"], job["ats"], form_url_from_api(target.api_url),
         json.dumps([q.to_dict() for q in questions]), now_iso()))
    return questions


def load_form(conn: sqlite3.Connection, job_id: int) -> tuple[str, list[FormQuestion]] | None:
    row = conn.execute("SELECT form_url, questions_json FROM forms WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    return row["form_url"], [FormQuestion(**q) for q in json.loads(row["questions_json"])]


def fetch_forms(conn: sqlite3.Connection, http: PoliteClient, gh_tokens: dict[str, str], statuses: tuple[str, ...],
                limit: int, log: Callable[[str], None] = print, llm_map=None, profile=None) -> tuple[int, int]:
    rows = conn.execute(
        f"""SELECT j.* FROM jobs j LEFT JOIN forms f ON f.job_id = j.id LEFT JOIN matches m ON m.job_id = j.id
            WHERE f.job_id IS NULL AND j.ats IN ({','.join('?' * len(FORM_ATS))})
              AND j.status IN ({','.join('?' * len(statuses))})
            ORDER BY m.score DESC LIMIT ?""", (*FORM_ATS, *statuses, limit)).fetchall()
    ok = failed = 0
    for job in rows:
        try:
            with conn:
                fetch_form(conn, http, job, gh_tokens, llm_map, profile)
            ok += 1
        except Exception as exc:  # one bad posting shouldn't stop the batch
            failed += 1
            log(f"  job {job['id']} ({job['company']}): {exc}")
    return ok, failed
