"""Export the applications log to CSV or Excel.

The log lists jobs and what happened to them. It never includes your answers
(contact details, work authorization, EEO, salary...), so it's safe to keep or share.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from jobpilot.review.outcomes import BY_KEY
from jobpilot.review.status import LABELS

COLUMNS = ["job_id", "company", "title", "location", "status", "score", "posting_url", "ats", "resume",
           "questions_left_for_you", "date_found", "approved_at", "filled_at", "submitted_at", "rejected_at",
           "outcome", "outcome_since", "first_reply_at", "follow_ups", "next_step", "notes"]

TRACKED = ("ready_to_fill", "needs_human", "account_wall", "custom_site", "approved", "filled", "submitted", "rejected")


def log_rows(conn: sqlite3.Connection) -> list[list]:
    rows = conn.execute(
        f"""SELECT j.*, m.score, a.resume_label, a.fields_json, a.approved_at, a.filled_at, a.submitted_at,
                   a.rejected_at, a.notes, o.stage, o.stage_at, o.responded_at, o.followups, o.next_at, o.next_note
            FROM jobs j LEFT JOIN matches m ON m.job_id = j.id LEFT JOIN applications a ON a.job_id = j.id
            LEFT JOIN outcomes o ON o.job_id = j.id
            WHERE a.job_id IS NOT NULL OR j.status IN ({','.join('?' * len(TRACKED))})
            ORDER BY COALESCE(a.submitted_at, a.approved_at, j.date_found) DESC""", TRACKED).fetchall()
    out = []
    for r in rows:
        fields = json.loads(r["fields_json"] or "[]")
        left = sum(1 for f in fields if f.get("state") == "needs_human" and not f.get("optional"))
        out.append([r["id"], r["company"], r["title"], r["location"], LABELS.get(r["status"], r["status"]),
                    r["score"] if r["score"] is not None else "", r["url"], r["ats"], r["resume_label"] or "", left,
                    r["date_found"][:10], r["approved_at"] or "", r["filled_at"] or "", r["submitted_at"] or "",
                    r["rejected_at"] or "", BY_KEY[r["stage"]].label if r["stage"] in BY_KEY else "",
                    r["stage_at"] or "", r["responded_at"] or "", r["followups"] if r["stage"] else "",
                    f"{r['next_at']} {r['next_note']}".strip() if r["next_at"] else "", r["notes"] or ""])
    return out


def event_rows(conn: sqlite3.Connection) -> list[list]:
    return [[r["job_id"], r["company"], r["title"], r["at"], r["actor"], r["action"],
             LABELS.get(r["from_status"], r["from_status"]), LABELS.get(r["to_status"], r["to_status"]), r["note"]]
            for r in conn.execute("""SELECT e.*, j.company, j.title FROM application_events e
                                     JOIN jobs j ON j.id = e.job_id ORDER BY e.id""")]


EVENT_COLUMNS = ["job_id", "company", "title", "at", "who", "action", "from", "to", "note"]


def to_csv(conn: sqlite3.Connection, path: Path) -> int:
    rows = log_rows(conn)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows([[_safe_cell(v) for v in r] for r in rows])
    return len(rows)


def to_xlsx(conn: sqlite3.Connection, path: Path) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    rows = log_rows(conn)
    for sheet, cols, data in ((ws, COLUMNS, rows), (wb.create_sheet("History"), EVENT_COLUMNS, event_rows(conn))):
        sheet.append(cols)
        for c in sheet[1]:
            c.font = Font(bold=True)
        for row in data:
            sheet.append([_safe_cell(v) for v in row])
        sheet.freeze_panes = "A2"
        for i, col in enumerate(cols, 1):
            width = max([len(str(col))] + [len(str(r[i - 1])) for r in data[:200]])
            sheet.column_dimensions[sheet.cell(1, i).column_letter].width = min(60, width + 2)
    wb.save(path)
    return len(rows)


def _safe_cell(v):
    """Stop job text that starts with = from turning into a formula in Excel."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v
