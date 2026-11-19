# Installing and using JobPilot

This guide takes you from nothing installed to sending your first application. It takes about 15 minutes.
Commands are shown for Windows (PowerShell), with macOS/Linux versions where they differ.

## 1. What you need

| Thing | Why | Get it |
|---|---|---|
| **Python 3.11 or newer** | JobPilot is a Python program | [python.org/downloads](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** in the installer. |
| **Git** (optional) | To download and update the code | [git-scm.com](https://git-scm.com/downloads). You can use a ZIP download instead. |
| **Microsoft Edge or Google Chrome** | Autofill opens forms in it, and the browser button runs in it | Edge is already on Windows. |
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
| **Home** | What to do next: applications to review or submit, follow-ups due, and good fits. Paste a job link here to add it. |
| **Find jobs** | Tabs for jobs **you qualify** for, ones you're **almost** qualified for, **all jobs**, and **skills to learn**. Search, pick a region, or open **Filters** for more. |
| **Apply** | Everything waiting on you, grouped by what it needs: your answers, your OK, or your Submit click. |
| **Applications** | Applications you've submitted, as a pipeline you drag cards across or a list. Tiles, a **Coming up** week strip, search (press `/`), filters and follow-up drafts. **+ Add** logs one you applied to elsewhere. |
| **Insights** | Your pace against your daily goal, applications per week, the funnel to offers, and what gets replies. |
| **Settings** | Which jobs you see, your daily goal, follow-up timing, the browser button, and **Resumes** (versions, the default, and answers you asked it to remember). Sources, the matching engine and rate limits are under **Advanced settings**. |

**+ Add a job** (top right) takes a link to any posting you found. Greenhouse, Lever and Ashby postings
are read automatically; for other sites you type the title and company. The job goes straight to **Apply**.
From a terminal: `jobpilot add https://...`

## 7. Apply to a job

1. **Review.** Open **Apply** and click **Review**. Fields marked **NEEDS_HUMAN** are yours to answer:
   work authorization, sponsorship, salary, demographic questions, legal consent, and anything
   JobPilot couldn't find in your profile. It never fills those from defaults. Edit anything, then click
   **Approve** (or **Skip** / **Reject**).
   - Going fast? **Approve & next** (Ctrl+Enter) approves and opens the next job; **Approve & fill**
     approves and starts filling the form right away. You still tick the confirm box each time.
   - Tick **Remember** under an answer you typed (like "How did you hear about us?") and JobPilot uses
     it for the same question next time. Protected questions can't be remembered.
2. **Optional: tailor your resume.** On the job's page, **Suggest a tailored version** reorders your
   real content so the most relevant parts come first. Nothing is invented. Approve it to create a DOCX.
3. **Fill the form.**
   - Click **Fill the form for me** (or **Approve & fill** in one step). A browser window opens and
     JobPilot fills what you approved. This works on any site: on Greenhouse and Lever it uses the form
     it loaded; elsewhere (Ashby, Workday, company sites) it reads the form in that window.
   - **Multi-page forms:** keep the window open. Click **Next** yourself; JobPilot fills each new page as
     it appears and never changes a field that already has something in it.
   - **Sign-in pages (Workday and others):** sign in, or make your account, yourself in that window.
     JobPilot never types passwords; it carries on with the form once you're in. The window keeps its
     own profile, so you stay signed in next time. Workday accounts are per company, so expect one sign-in
     per employer.
   - **Prefer your own browser?** Click **Open the application**, then the **JobPilot browser button**
     (set it up once, below).
   - **Several approved?** On **Apply**, **Fill all approved, one by one** opens each approved form in
     turn and skips any whose posting has closed.
4. **You submit.** JobPilot stops on the last page. Look it over, then click **Submit** on the site
   yourself. JobPilot can't submit for you, and there's no setting to make it.
5. **Mark it.** Back on the job's page in the dashboard, tick the box and click **Mark as submitted**.
   It now shows on **Applications**. Applied on a site yourself? Use **+ Add** on Applications (or
   **Already applied on the site yourself?** on the job's page) to log it.

## The browser button (fill forms on any site)

The browser button is a small Edge/Chrome extension that comes with JobPilot. On any application page you
have open, it reads the form, asks the dashboard on your computer what it can fill, and shows you every
value. It fills only what you tick, outlines what's left for you in orange, and never submits.
It also has **Save this job**, which adds the posting you're looking at to JobPilot.

Set it up once:

1. Start the dashboard (`jobpilot dashboard`) and open **Settings → Browser button**.
2. In Edge go to `edge://extensions` (Chrome: `chrome://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and choose the `extension` folder inside your `jobpilot` folder.
4. Click the puzzle-piece icon in the toolbar and pin **JobPilot**.
5. Click the JobPilot button, paste the key from Settings, and click **Connect**.

Then, on an application form: press **Alt+Shift+J** (or click the button). It reads the form right away;
check the list and press **Enter** to fill the ticked fields. On page 2 of a form, do the same: fields
that already have something in them are left alone. On job sites the button shows **FILL**, or a green
**GO** when it's a job you already approved in JobPilot.

What it won't do: fill sign-in or sign-up pages, create accounts, solve CAPTCHAs, attach files, or answer
questions only you can answer (work authorization, sponsorship, salary, EEO, background checks, legal
consent). If you answered one of those on the job's page in JobPilot, it reuses that answer for that job,
except legal or consent questions, which you always tick yourself. Written DRAFT answers start unticked.
The dashboard must be running for the button to work.

## 8. After you apply

On **Applications**, drag each card to its new column as you hear back (or pick the stage in **List**
view). Add a next step with a date and it appears under **Coming up**; click **Done** when it's over. When one has been quiet for a week, a **To do** item shows a short draft follow-up email.
Copy it, send it from your own email, then click **I sent it**. JobPilot never sends email.

## Your daily routine

```bash
cd ~/Documents/jobpilot
.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
jobpilot run
jobpilot dashboard
```

Then in the dashboard: **Sort** the new jobs (Y / L / N), **Answer them once** for questions many
applications share, approve with **Approve & next** (Ctrl+Enter), and **Fill all approved, one by one**.
Click Submit on each form yourself.

## Updating

```bash
cd ~/Documents/jobpilot
git pull
pip install -e .
```

(ZIP users: download the new ZIP and copy your old `data/` folder and `.env` into it.)
Your data is upgraded automatically the next time you run any command.

## Backing up, moving, removing

- **Back up:** `jobpilot backup` (or **Settings → Download a backup**) writes one zip with your database,
  profile, settings and resumes. Browser sign-ins, the browser-button key and `.env` are left out; copy
  `.env` yourself if you use an API key.
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
| The browser button says it can't reach JobPilot | Start the dashboard (`jobpilot dashboard`) and keep it running. |
| The browser button finds no fields | Some forms load inside a frame from another site. Open the form's own page (often an "Apply" link) and try again. |
| Odd characters in the terminal on Windows | Run `$env:PYTHONIOENCODING="utf-8"` first. |
| Port 8765 is busy | `jobpilot dashboard --port 8800` |
| Anything else | `jobpilot doctor` checks your setup and says what to fix. |

Every command has help: `jobpilot --help`, or for one command, for example `jobpilot autofill --help`.
The full command list is in the [README](../README.md#commands).
