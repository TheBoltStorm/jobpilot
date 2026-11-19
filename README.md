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
| 2 | Job discovery sources + dedupe | ✅ done |
| 3 | Matching + skill-gap report | ✅ done (free local engine; Claude optional) |
| 4 | Review dashboard | ✅ done |
| 5 | Autofill (Greenhouse, dry run) | ✅ done (fill mode works; you always submit) |
| 6 | Lever forms, tailored resumes, one-command pipeline, setup check | ✅ done |

## Setup

**New to this? Follow [docs/INSTALL.md](docs/INSTALL.md).** It walks through installing Python,
downloading JobPilot, setting up your profile, and applying to your first job.

Quick version (Python 3.11+, Windows PowerShell; macOS/Linux notes in the guide):

```bash
git clone https://github.com/TheBoltStorm/jobpilot.git
cd jobpilot
python -m venv .venv
.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
pip install -e .                # add ".[dev]" to run the tests
copy .env.example .env          # macOS/Linux: cp; every key is optional
jobpilot doctor
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

## Every day: one command

```bash
jobpilot doctor             # checks profile, resume, privacy (gitignore), settings and the autofill browser
jobpilot run                # discover -> enrich -> match -> prepare -> load real forms
jobpilot dashboard          # then review what's waiting
```

`jobpilot run` only collects and prepares. It never fills or submits anything. Use
`--skip-discover` to work on jobs you already have, and `--enrich-limit` / `--forms-limit` to keep it short.

## Finding jobs

```bash
jobpilot companies init     # starter list of company job boards matched to your target fields
jobpilot sources            # what's enabled and why each source is allowed
jobpilot discover           # fetch, filter to your targets, dedupe (about 1-2 minutes)
jobpilot jobs list --summary
```

Discovery pulls from the GitHub internship and new-grad lists (SimplifyJobs, vanshb03) and the
public Greenhouse, Lever and Ashby job-board APIs. USAJOBS, RSS feeds and CSV imports are optional.
It keeps only postings that match your role types and target fields, merges the same job seen in
several places, and marks jobs "posting closed" when they disappear from a full listing.
See [docs/SOURCES.md](docs/SOURCES.md) for what's included, what's excluded, and why.
To add a source, see [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md).

Settings (rate limits, max posting age, which lists to read) live in `data/settings.yaml`.
The companies to check live in `data/companies.yaml`. Both are created on first use and are yours to edit.

## Matching and skill gaps

```bash
jobpilot enrich             # fetch descriptions the GitHub lists don't include (Greenhouse/Lever/Ashby)
jobpilot match              # score jobs 0-100 against your profile (free, offline)
jobpilot matches --with-description --reasons
jobpilot match show 123     # which skills you have (with the resume line that proves it), partly have, or lack
jobpilot gaps               # missing skills ranked across matched jobs, by field, with how to learn each
jobpilot gaps --export gaps.md
```

Scoring never takes your word for anything the profile doesn't show. A skill counts as "have" only
when your profile says so, and the breakdown quotes the line. Jobs without a description get a
low-confidence, title-only estimate. Details, and the optional Claude engine (needs an API key;
spending is capped and logged), are in [docs/MATCHING.md](docs/MATCHING.md).

## Reviewing in the dashboard

```bash
jobpilot prepare            # sort matched jobs into Ready to fill / Needs human / Custom site
jobpilot dashboard          # opens http://127.0.0.1:8765 (only reachable from this computer)
jobpilot export applications.xlsx   # applications log (.csv or .xlsx), no answers included
```

The dashboard has six main pages:

- **Home.** What to do next, today's count against your daily goal (Settings, default 10) with your
  streak, follow-ups due, good fits, and a box to paste a job link.
- **Find jobs.** Tabs for jobs you qualify for, ones you're one requirement short of, all jobs, and
  skills to learn. Search, region chips, and more filters behind **Filters**.
- **Apply.** Everything waiting on you, with **New** badges on postings from the last two days and
  **applied before** on reposts of a role you already applied to. From here:
  - **Sort N quickly**: one card at a time. Press **Y** keep, **L** later, **N** pass, **O** review now.
    Newest postings come first, because early applications get more replies.
  - **Answer them once**: a question that's waiting in many applications (say, "Phone" or "How did you
    hear about us?") answered once for every job you tick. Protected questions start with every job
    unticked, so you choose which ones your answer is true for. Legal and consent questions never show here.
  - **Check which are still open**: looks up each posting (politely, robots.txt respected) and moves
    closed ones out of your queue. Approved ones only get a warning.

  Each job's page shows:
  - its description, match score, skill gaps and minimum requirements;
  - every field autofill would type, next to where the value came from (your profile, your resume, or you);
  - DRAFT written answers and private notes;
  - **Approve & fill** (approve and open the form in one click), **Approve & next** (Ctrl+Enter: approve
    and jump to the next job), **Approve**, **Save edits**, **Skip** and **Reject**;
  - a **Remember** box next to answers you typed, so the same question gets that answer next time
    (never for work authorization, sponsorship, salary, EEO, background or legal questions);
  - **Already applied on the site yourself?** to log it without filling anything;
  - **Tailored resume**: a version of your resume reordered for this job (see below);
  - how long it's been posted, **Check it's still open**, and other listings of the same role;
  - **People at the company**: who you know there and a DRAFT referral ask to copy (you send it).

  **Fill the form for me** works on any site now. Greenhouse and Lever use their own fillers; on every
  other site (Ashby, Workday, SmartRecruiters, Workable, company pages) JobPilot reads the form in the
  window it opens and fills it with the same rules as the browser button. It keeps filling each new page
  of a multi-page form while the window is open, never overwrites anything already in a field, and never
  clicks Next or Submit. If the site asks you to sign in, you sign in yourself in that window and
  JobPilot carries on. **Fill all approved, one by one** opens each approved form in turn, checking first
  that the posting is still open.
- **Applications.** Your submitted applications as a **Pipeline** (Applied, Screen, Assessment,
  Interviewing, Offer, Closed; drag a card to change its stage) or a **List**. Tiles for applications,
  assessments, interviews and offers, a **Coming up** week strip for next steps (click **Done** when
  finished), search (press `/`), filters by field and type, and sorting. **+ Add** logs an application
  you made somewhere else.
- **Insights.** Your pace (today, this week, this month, streak), applications per week, the funnel
  from applied to offer, and which sources, fields, resumes, referrals, and how soon after posting you
  applied get replies.
- **Settings.** Which jobs you see, follow-ups and the browser button. Sources, the matching engine,
  rate limits and the run log are under **Advanced settings**. **Resumes** (from Settings) lists your
  resume versions with how often each gets replies, lets you upload one or pick the default, and shows
  the answers you asked JobPilot to remember. Add your own there too: write `{company}`, `{title}` or
  `{skills}` in an answer and each job gets a filled-in DRAFT to check. **People** (from Applications or
  Settings) lists everyone you know at a company and whether you've asked for a referral. **Download a
  backup** saves your data to one zip.

**+ Add a job** takes a link to any posting (`jobpilot add URL` from a terminal). Greenhouse, Lever and
Ashby postings are read through their public APIs; for other sites you type the title and company.

**The browser button** (`extension/`, an Edge/Chrome extension) fills application forms on any site you
open. It shows every value first, fills only what you tick, skips sign-in pages, files and questions only
you can answer, and never submits. It talks only to the dashboard on your computer, using a key from
Settings. Setup: [docs/INSTALL.md](docs/INSTALL.md#the-browser-button-fill-forms-on-any-site).

How the gate works:

- Fields JobPilot can't fill from your profile show as `NEEDS_HUMAN`.
- Protected questions (work authorization, sponsorship, EEO, salary and so on) are always yours to
  answer. Your answers stay on that one application and never go into your profile.
- Approving needs a tick in "I've checked every field and draft above", and it records exactly what you approved.
- Changing anything after you approve withdraws the approval.
- Only you can approve, reject, or mark a job submitted. The code refuses those moves from anything else,
  and there's no setting to change that (see [`jobpilot/review/status.py`](jobpilot/review/status.py)).

The dashboard only answers on 127.0.0.1. Every button needs a token that's created when it starts,
and requests from other websites are refused, so a page you visit can't press Approve for you.

## Where jobs are, and which ones you qualify for

Every job's location is sorted into a **region** (Remote, US Northeast / Midwest / South / West, Canada,
Europe, Asia-Pacific, Latin America) and an **area**: a metro area for the big job markets
("SF Bay Area", "New York City area", "Seattle area"...), otherwise the city. A posting listing several
cities appears under each. On the Jobs and You qualify pages, click a region chip or pick an area.

```bash
jobpilot places                       # open jobs by region and area
jobpilot jobs list --area "Bay Area"  # or --region "US West", "Remote", "Europe"
```

**You qualify** checks each posting's *required* section against your profile: required skills,
degree level, years of experience, graduation date and GPA. A job is listed when every requirement
JobPilot found is met, and at least two were found. Coursework counts as having a skill, but a
related skill (Java for Kotlin) doesn't. Jobs without a description, or with nothing specific to check,
aren't listed. Work authorization, sponsorship and clearance are never judged for you: if a posting
mentions them, the job's page shows a note.

```bash
jobpilot qualify                      # meet every minimum
jobpilot qualify --close              # one requirement short, and which one
jobpilot qualify --region "US West"
```

## Tailored resumes

On a job's page, **Suggest a tailored version** (or `jobpilot tailor 123`) reorders your real content
so the parts this posting asks for come first: bullets within each job and project, the projects
themselves, and your skills list. Jobs stay in date order. Nothing is added, removed or reworded.
Before any file is written, the tailored version is checked line by line against the original, and
the page shows you what moved. **Approve and create DOCX** (or `jobpilot tailor 123 --approve`) writes a
plain DOCX built from your profile to `data/resumes/`, adds it as a resume version, and picks it for that
application. Your other versions don't change.

## After you apply

Once you've clicked Submit on the site and marked the job **Submitted**, it shows up on **Applications**.
There you set its stage yourself (recruiter screen, interview, offer, not selected, no response, ...)
and, if you like, the date of the next step. JobPilot never changes a stage on its own.

**Follow-ups.** When an application has been quiet for a while (7 days by default, then every 7 days,
at most 2 follow-ups; change this under Settings), the **To do** list shows a short DRAFT message. After an
interview, it suggests a thank-you note instead. Drafts use only facts: the role, the company, the date you
applied, your name and email. JobPilot never sends them. Copy the draft, send it from your own email, then
click **I sent it** so it records the date. After 30 quiet days it suggests marking the application
**No response**.

**What's working.** Response, interview and offer rates overall and by source, site, field, match score,
resume version and whether you met the minimums, plus how long replies usually take. The numbers mean
more once you have a few dozen applications.

```bash
jobpilot applied                          # your applications and their stages
jobpilot outcome 123 interview --next 2026-12-02 --next-note "onsite"
jobpilot followups --drafts               # what's due, with drafts you send yourself
jobpilot followups --sent 123             # record that you sent one
jobpilot stats
```

## Autofill

```bash
jobpilot forms fetch 123        # load the posting's real questions (Greenhouse API / Lever apply page)
jobpilot autofill 123           # dry run: outlines what it would fill, types nothing
jobpilot autofill 123 --fill    # approved jobs only: fills, then waits for you to click Submit
```

The review page shows each of the form's own questions and where its answer comes from: your
profile, a DRAFT built from facts, your answer, or NEEDS_HUMAN. Autofill only uses what you approved.
It never clicks Submit or presses Enter, and guards in the page and on the network block submission
while it works. CAPTCHAs and sign-in pages stop it. It uses Microsoft Edge (or Chrome) already on
your computer. Greenhouse and Lever forms are supported. Ashby forms aren't, because Ashby's robots.txt
disallows the API its forms load from; you apply to those on the site yourself.
Details: [docs/AUTOFILL.md](docs/AUTOFILL.md).

### Commands

| Command | Does |
|---|---|
| `jobpilot onboard [--resume FILE] [--label NAME]` | Parse a resume and run (or re-run) the interview. Re-runs keep what you already confirmed. |
| `jobpilot resume add FILE --label NAME [--default]` | Add another resume version (e.g. `data-science`). |
| `jobpilot resume list` / `show [NAME]` / `default NAME` | Manage resume versions. `*` marks the default. |
| `jobpilot profile show` / `edit` / `validate` / `path` | Look at or edit `profile.yaml`. |
| `jobpilot companies init [--force]` / `list` | Create or show `companies.yaml`. |
| `jobpilot sources` | List sources, whether each is on, and why it's allowed. |
| `jobpilot discover [--source NAME]` | Fetch new jobs (all enabled sources, or just one). |
| `jobpilot import-csv FILE [--name handshake] [--no-filter]` | Import a CSV you exported. |
| `jobpilot jobs list [--type internship] [--ats greenhouse] [--search text] [--region R] [--area A] [--summary]` | Browse stored jobs. |
| `jobpilot jobs show ID` | Full details, sources and description for one job. |
| `jobpilot enrich [--limit N] [--retry-failed]` | Fetch missing descriptions through public ATS APIs. |
| `jobpilot match [--engine local\|claude] [--rescore] [--limit N]` | Score new or changed jobs. |
| `jobpilot match show ID` | Score, reason, and skill-by-skill breakdown with evidence. |
| `jobpilot matches [--min-score N] [--type T] [--location X] [--field F] [--since DATE] [--with-description]` | Filtered list of scored jobs. |
| `jobpilot gaps [--field F] [--min-score N] [--top N] [--export FILE]` | Skill Gap Report. |
| `jobpilot runs` | Scoring run log with token cost. |
| `jobpilot prepare` | Sort matched jobs into Ready to fill / Needs human / Custom site. Fills and sends nothing. |
| `jobpilot dashboard [--port 8765] [--no-browser]` | Open the local review dashboard. |
| `jobpilot forms fetch [ID] [--limit N] [--all]` / `forms show ID` | Load (or show) a posting's real application questions. |
| `jobpilot autofill ID [--fill] [--headless] [--browser msedge\|chrome]` | Dry run (default) or fill an approved application. Never submits. |
| `jobpilot places [--region R] [--top N]` | Open jobs by region and area. |
| `jobpilot qualify [--close] [--region R] [--area A]` | Jobs where you meet every minimum requirement (or are one short). |
| `jobpilot add URL [--title T] [--company C] [--location L]` | Add a job from a link you found. |
| `jobpilot tailor ID [--approve\|--discard]` | Suggest a resume reordered for one job; approve to create the DOCX. |
| `jobpilot applied` | Your submitted applications and their stages. |
| `jobpilot outcome ID STAGE [--on DATE] [--note N] [--next DATE] [--next-note N]` | Record what happened with an application. Only you set this. |
| `jobpilot followups [--drafts] [--sent ID] [--mute ID]` | Follow-ups due, with drafts. JobPilot never sends them. |
| `jobpilot stats` | Response, interview and offer rates, and what's working. |
| `jobpilot run [--skip-discover] [--enrich-limit N] [--forms-limit N]` | The daily pipeline: discover, enrich, match, prepare, load forms. Fills nothing. |
| `jobpilot doctor [--no-browser]` | Check your setup and say how to fix anything missing. |
| `jobpilot export FILE.csv\|FILE.xlsx` | Export the applications log (no answers or personal details). |
| `jobpilot backup [FILE.zip]` | Save the database, profile, settings and resumes to one zip (browser sign-ins and keys left out). |

## Rules JobPilot follows

1. **You submit, always.** There's no auto-submit code path and no config flag to add one.
2. **Nothing made up.** Profile fields come from your resume or your answers. If the parser isn't sure
   (e.g. which part of a line is the job title), it leaves the field blank and asks you.
   Fields a form asks for that aren't in your profile become `NEEDS_HUMAN`.
3. **Protected questions are always yours.** Work authorization, sponsorship, EEO/demographics,
   veteran and disability status, salary, background checks and anything legally binding are never
   stored in the profile and never auto-filled. The profile refuses to save them
   (see [`jobpilot/core/protected.py`](jobpilot/core/protected.py)).
4. **No CAPTCHA solving, no bot-detection workarounds, no accounts created for you.** Autofill stops at
   CAPTCHAs and sign-in pages.
5. **Polite sources only.** Public APIs and feeds that allow it, rate-limited (never faster than one request
   per 0.5 s per site), robots.txt respected.
   No LinkedIn/Indeed scraping.
6. **Local data.** SQLite + `profile.yaml` under `data/`. Keys only in `.env`.

## No API key needed

Everything runs locally and for free by default. If you later add `ANTHROPIC_API_KEY` to `.env`,
`jobpilot match --engine claude` can use Claude (Haiku by default) for sharper scoring. Each run has
a spending cap, and token cost is logged per run (see [docs/MATCHING.md](docs/MATCHING.md)).

## Layout

```
jobpilot/
  cli.py                 command line
  config.py              paths (data/), .env loading
  settings.py            data/settings.yaml schema
  discovery.py           run sources -> filter -> dedupe -> store -> close missing
  core/db.py             SQLite + migrations
  core/http.py           polite HTTP (robots.txt, rate limit, retries)
  core/protected.py      protected-question classifier
  core/places.py         locations -> regions and metro areas
  sources/               one module per source + base.py, dedupe.py, companies seed list
  enrich.py              fetch missing descriptions via public ATS APIs
  matching/              skills.yaml list, local scorer, optional Claude engine, gap report,
                         minimum-requirements check (minimums.py)
  review/                statuses + the human gate, application plans, applications log export
                         + field mapper (mapping.py), fact-only drafts (drafts.py),
                         tailored resume versions (tailor.py)
  pipeline.py            `jobpilot run` and `jobpilot doctor`
  forms/                 real application questions (Greenhouse API, Lever apply page)
  autofill/              Playwright fillers (Greenhouse, Lever): dry run / fill, submit guards, run log
  web/                   dashboard (FastAPI, templates, static)
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
