# JobPilot

A local job-search assistant. It finds jobs that match your resume and target fields, pre-fills
applications, and shows you which skills you're missing. **It never submits anything for you.**
Every application goes through your review, and even after you approve one, JobPilot fills the form
and stops on the last page so you click Submit yourself.

All personal data stays on your machine, in `data/` (gitignored).

## Status

| Phase | What | State |
|---|---|---|
| 1 | Onboarding + resume parsing | ✅ done |
| 2 | Job discovery sources + dedupe | next |
| 3 | Matching + skill-gap report | planned (works without an API key; see below) |
| 4 | Review dashboard | planned |
| 5 | Autofill (Greenhouse, dry run) | planned |
| 6 | Scaling + polish | planned |

## Setup

Requires Python 3.11+.

```bash
git clone <this repo> jobpilot && cd jobpilot
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env          # macOS/Linux: cp .env.example .env  (all keys optional)
```

## First run

```bash
jobpilot onboard --resume path/to/resume.pdf
```

JobPilot parses your resume locally (no API needed), then walks you through a short interview:
it shows what it found and you confirm, fix or drop each item, then add what it couldn't find
(target fields, role types, locations, remote preference, reusable short answers).
The result is `data/profile.yaml`, which you can edit any time.

No resume handy? Make a fictional one to try it out:

```bash
jobpilot sample-resume            # writes samples/sample_resume.docx and .pdf
jobpilot onboard --resume samples/sample_resume.pdf
```

### Commands

| Command | Does |
|---|---|
| `jobpilot onboard [--resume FILE] [--label NAME]` | Parse a resume and run (or re-run) the interview. Re-runs keep what you already confirmed. |
| `jobpilot resume add FILE --label NAME [--default]` | Add another resume version (e.g. `data-science`). |
| `jobpilot resume list` / `show [NAME]` / `default NAME` | Manage resume versions. `*` marks the default. |
| `jobpilot profile show` / `edit` / `validate` / `path` | Look at or edit `profile.yaml`. |

## Rules JobPilot follows

1. **You submit, always.** There's no auto-submit code path and no config flag to add one.
2. **Nothing made up.** Profile fields come from your resume or your answers. If the parser isn't sure
   (e.g. which part of a line is the job title), it leaves the field blank and asks you.
   Fields a form asks for that aren't in your profile become `NEEDS_HUMAN`.
3. **Protected questions are always yours.** Work authorization, sponsorship, EEO/demographics,
   veteran and disability status, salary, background checks and anything legally binding are never
   stored in the profile and never auto-filled. The profile refuses to save them
   (see [`jobpilot/core/protected.py`](jobpilot/core/protected.py)).
4. **No CAPTCHA solving, no bot-detection workarounds, no accounts created for you.**
5. **Polite sources only.** Public APIs and feeds that allow it, rate-limited, robots.txt respected.
   No LinkedIn/Indeed scraping.
6. **Local data.** SQLite + `profile.yaml` under `data/`. Keys only in `.env`.

## Matching without an API key

Phase 3 will ship a free local matcher (skill taxonomy + keyword evidence from your resume) as the
default. If you later add `ANTHROPIC_API_KEY` to `.env`, JobPilot can use Claude (Haiku by default)
for sharper skill extraction and scoring, with token cost shown per run.

## Layout

```
jobpilot/
  cli.py                 command line
  config.py              paths (data/), .env loading
  core/db.py             SQLite + migrations
  core/protected.py      protected-question classifier
  onboarding/
    resume_parser.py     PDF/DOCX -> text -> sections -> structured data (with source lines)
    profile.py           profile.yaml schema (pydantic)
    interview.py         setup interview
    resumes.py           resume versions
  samples.py             fictional sample resume
tests/                   pytest, fake data only
```

## Tests

```bash
pytest
```
