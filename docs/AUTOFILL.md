# Autofill

JobPilot fills application forms you've approved, then stops so you can check the form and click
Submit yourself. It never submits. This page explains how it works and what it will and won't do.

## The flow

1. **Load the real questions.** On a job's review page, click *Load this form's real questions*, or run:
   ```bash
   jobpilot forms fetch 123            # one job
   jobpilot forms fetch --limit 25     # the best-scoring prepared jobs
   jobpilot forms show 123
   ```
   For Greenhouse, this reads the public job-board API (`boards-api.greenhouse.io/...?...questions=true`).
   For Lever, it reads the posting's apply page (`jobs.lever.co/{company}/{id}/apply`): the questions
   are in the page's HTML, so no JavaScript runs. Lever's robots.txt allows this and asks for a
   1-second crawl delay, which JobPilot honors.
   Both requests are rate-limited and follow robots.txt, like discovery. You get every question, its
   options, and the voluntary EEO and demographic sections, without opening a browser.
2. **Review.** The review page lists each question next to its planned answer and where that answer came from:
   - **Profile:** matched to a known profile key (first name, email, LinkedIn, graduation date...).
     For dropdowns, the profile value has to clearly fit exactly one option. "May 2027" fits
     "Spring 2027"; anything ambiguous is left for you.
   - **DRAFT:** for open questions like "Why do you want to work here?". The draft is built only from
     skills the posting asks for and resume lines that show you have them. If there are no such facts,
     there's no draft.
   - **NEEDS_HUMAN:** anything the profile doesn't cover. Nothing is made up.
   - **Only you can answer:** work authorization, sponsorship, salary, EEO/demographic, veteran and
     disability questions. Your answer applies to that one application and never goes into your profile.
   - **Legally binding or consent questions** ("I certify...", background-check consent) are never ticked
     by JobPilot, even if you typed an answer. You tick them on the form.
3. **Approve.** You tick "I've checked every field and draft above". JobPilot stores an exact
   snapshot of what you approved. Changing any answer, or reloading the form, withdraws the approval.
4. **Dry run.** Types nothing:
   ```bash
   jobpilot autofill 123              # visible browser; fields outlined blue (would fill) / orange (yours)
   jobpilot autofill 123 --headless   # no window, just the report
   ```
5. **Fill.** Approved jobs only:
   ```bash
   jobpilot autofill 123 --fill
   ```
   It types the approved answers, picks dropdown options, ticks checkboxes and attaches your resume.
   Then it hands the page to you and waits. You check everything, fill in whatever's outlined in
   orange, and click **Submit** yourself. Afterwards, mark it submitted on its review page.

## What stops submission

- The filler only types, picks options, ticks boxes and attaches files. It never clicks Submit,
  never presses Enter, and never calls a form's submit function. A test checks the source code for this.
- While JobPilot works, a guard in the page cancels any submit event, any click on a submit button,
  and the Enter key.
- At the same time, a network guard aborts anything that looks like an application submission to
  Greenhouse, Lever or Ashby.
- Fill mode only runs for jobs you approved, and only uses the approved snapshot. There's no setting
  that changes any of this.
- The guards come off only at the end of fill mode, so that you can submit. A dry run keeps them on.

## What it won't do

- **CAPTCHAs:** JobPilot stops and tells you. It doesn't solve them.
- **Sign-in pages:** the job moves to *Account wall*, and JobPilot doesn't sign in or create accounts.
  The browser uses its own profile, kept in your data folder (`browser_profile/`, gitignored). If a
  site needs you signed in, sign in there yourself.
- **Custom career sites** (Workday, iCIMS...) stay in *Custom site*: apply on the site yourself.
- **Ashby forms:** Ashby serves its application forms only through `/api/`, which `jobs.ashbyhq.com`'s
  robots.txt disallows. So JobPilot doesn't read them, and Ashby jobs show only the standard fields.
  Apply on the site yourself.

## Lever specifics

- Custom questions are radio buttons, checkboxes or dropdowns. An answer has to be exactly one
  of the options, or it's left for you.
- A question whose only option is an acknowledgement ("I have read and understand...") counts as
  legally binding: JobPilot never ticks it.
- *Current location* is typed, but Lever suggests places as you type, and you pick the suggestion yourself.
- Voluntary surveys and EEO dropdowns are always yours to answer.

## Browser

Playwright drives a browser already on your computer: Microsoft Edge by default on Windows, set in
`settings.yaml` → `autofill.browser_channel` (`msedge`, `chrome` or `chromium`). Only `chromium`
needs a separate download (`python -m playwright install chromium`). `autofill.slow_mo_ms` slows
actions down so you can watch.

## Optional: Claude for unclear labels

If you've set the matching engine to `claude` and have `ANTHROPIC_API_KEY` in `.env`, then when a
form is loaded, labels the local rules don't recognize go to Claude. Claude may only answer with
one of the fixed profile keys (e.g. `github`) or "none". The value still comes from your profile,
and the review page shows "matched by Claude" next to it. This happens once per form, not on every page view.
