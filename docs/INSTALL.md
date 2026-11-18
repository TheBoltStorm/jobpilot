# Installing and using JobPilot

This guide takes you from nothing installed to sending your first application. It takes about 15 minutes.
Commands are shown for Windows (PowerShell), with macOS/Linux versions where they differ.

## 1. What you need

| Thing | Why | Get it |
|---|---|---|
| **Python 3.11 or newer** | JobPilot is a Python program | [python.org/downloads](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** in the installer. |
| **Git** (optional) | To download and update the code | [git-scm.com](https://git-scm.com/downloads). You can use a ZIP download instead. |
| **Microsoft Edge or Google Chrome** | Autofill drives a real browser window | Edge is already on Windows. |
| **Your resume** as PDF or DOCX | JobPilot builds your profile from it | No resume? It can make a fictional sample one. |

You don't need an API key. Everything runs free and offline on your computer, except fetching job listings.

Check Python is installed by opening a terminal (Windows: **PowerShell**; macOS: **Terminal**):

```bash
python --version
```

It should print `Python 3.11` or higher. On Windows, if it says Python isn't found, try `py --version`.
If that works, use `py` wherever this guide says `python`. On macOS/Linux, use `python3`.

## 2. Download the code

Pick a folder to keep it in, for example your Documents folder.

**With Git:**

```bash
cd ~/Documents
git clone https://github.com/TheBoltStorm/jobpilot.git
cd jobpilot
```

The repository is private, so Git asks you to sign in to GitHub the first time (a browser window
opens on Windows). If you don't have access, ask the owner to add you as a collaborator.

**Without Git:** on the repository's GitHub page, click **Code → Download ZIP**. Unzip it, then open a
terminal in the unzipped `jobpilot` folder (Windows: right-click the folder → **Open in Terminal**).

## 3. Install

Run these from inside the `jobpilot` folder. They create a private Python environment for JobPilot so
it doesn't affect anything else on your computer.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

After activation, your prompt starts with `(.venv)`. **Each time you open a new terminal to use JobPilot,
`cd` into the folder and run the activate line again.** Always run JobPilot from inside this folder:
it looks for `data/` and `.env` in the folder you're in.

If PowerShell says *"running scripts is disabled on this system"*, you can skip activation and run
JobPilot directly as `.venv\Scripts\jobpilot` (for example `.venv\Scripts\jobpilot doctor`).

The `.env` file holds optional keys, and every key in it is optional. Leave it as it is unless you want
the paid Claude matcher or the USAJOBS source.

Check the install:

```bash
jobpilot doctor
```

At this point it reports that you have no profile yet. That's expected; the next step creates one.

## 4. Set up your profile (once)

```bash
jobpilot onboard --resume "C:\path\to\your_resume.pdf"
```

JobPilot reads your resume on your computer and shows you what it found. You confirm, fix or drop
each item, then answer a few questions it couldn't get from the resume: target fields, internship or
full-time, locations, remote preference, and short reusable answers. It never guesses. Anything it isn't
sure of, it asks you.

Your answers are saved to `data/profile.yaml`. You can change them later with `jobpilot profile edit`
or by running `onboard` again. Everything in `data/` stays on your computer and is never committed to Git.

**Just trying it out?** Use the fictional sample resume:

```bash
jobpilot sample-resume
jobpilot onboard --resume samples/sample_resume.pdf
```

Then run `jobpilot doctor` again. Fix anything it marks as failed; each line tells you how.

## 5. Find and score jobs

```bash
jobpilot companies init     # once: a starter list of company job boards for your fields
jobpilot run                # find jobs, fetch descriptions, score them, load application forms
```

The first `run` takes a few minutes because it's polite to every site (rate-limited, robots.txt
respected). Later runs only pick up what's new. `run` never fills or submits anything.

## 6. Open the dashboard

```bash
jobpilot dashboard
```

Your browser opens at http://localhost:8765. The dashboard runs only on your computer. Press **Ctrl+C** in the
terminal to stop it.

| Page | What it's for |
|---|---|
| **Board** | Your overview: what's waiting, jobs you qualify for, follow-ups due. |
| **Jobs** | Every job, filterable by status, score, field, region and area. |
| **You qualify** | Jobs where you meet every minimum requirement, or are one short. |
| **Review** | Go through prepared applications one at a time. |
| **Applied** | Applications you've submitted, their stages, follow-up drafts and stats. |
| **Skill gaps** | Skills that matching jobs ask for and you don't show yet, with how to learn them. |
| **Settings** | Rate limits, posting age, follow-up timing and more. |

## 7. Apply to a job

1. **Review.** On **Review**, check every answer. Fields marked **NEEDS_HUMAN** are yours to answer:
   work authorization, sponsorship, salary, demographic questions, legal consent, and anything
   JobPilot couldn't find in your profile. It never fills those from defaults. Edit anything, then click
   **Approve** (or **Skip** / **Reject**).
2. **Optional: tailor your resume.** On the job's page, **Suggest a tailored version** reorders your
   real content so the most relevant parts come first. Nothing is invented. Approve it to create a DOCX.
3. **Fill the form.** In the terminal:

   ```bash
   jobpilot autofill 123           # dry run first: shows what it would fill, types nothing
   jobpilot autofill 123 --fill    # opens the application in Edge and fills your approved answers
   ```

   (`123` is the job's number, shown in its page address and in `jobpilot jobs list`.)
   Autofill only works on Greenhouse and Lever forms. For other sites, the job page links to the posting and
   you apply there yourself.
4. **You submit.** JobPilot stops on the last page. Look it over, then click **Submit** on the site
   yourself. JobPilot can't submit for you, and there's no setting to make it.
5. **Mark it.** Back on the job's page in the dashboard, click **Mark as submitted**. It now shows on **Applied**.

## 8. After you apply

On **Applied**, update each application's stage as you hear back (screen, interview, offer, not
selected...). When one has been quiet for a week, a **To do** item shows a short draft follow-up email.
Copy it, send it from your own email, then click **I sent it**. JobPilot never sends email.

## Your daily routine

```bash
cd ~/Documents/jobpilot
.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
jobpilot run
jobpilot dashboard
```

## Updating

```bash
cd ~/Documents/jobpilot
git pull
pip install -e .
```

(ZIP users: download the new ZIP and copy your old `data/` folder and `.env` into it.)
Your data is upgraded automatically the next time you run any command.

## Backing up, moving, removing

- **Back up:** copy the `data/` folder and `.env`. That's everything personal.
- **Keep your data somewhere else:** set `JOBPILOT_HOME=D:\somewhere` in `.env`.
- **Uninstall:** delete the `jobpilot` folder. Nothing is installed outside it.
  Copy `data/` out first if you want to keep your profile and history.

## Troubleshooting

| Problem | Fix |
|---|---|
| `jobpilot` is not recognized | The environment isn't active. Run the activate line from step 3, or use `.venv\Scripts\jobpilot`. |
| `python` is not recognized | Reinstall Python with "Add to PATH" ticked, or use `py` instead. |
| Autofill says the browser didn't start | Install Edge or Chrome, or set `autofill.browser_channel` in `data/settings.yaml` (`msedge`, `chrome`, or `chromium`; for `chromium` run `python -m playwright install chromium`). |
| Autofill stops on a CAPTCHA or sign-in page | That's on purpose. Finish that step yourself in the open browser window. |
| Odd characters in the terminal on Windows | Run `$env:PYTHONIOENCODING="utf-8"` first. |
| Port 8765 is busy | `jobpilot dashboard --port 8800` |
| Anything else | `jobpilot doctor` checks your setup and says what to fix. |

Every command has help: `jobpilot --help`, or for one command, for example `jobpilot autofill --help`.
The full command list is in the [README](../README.md#commands).
