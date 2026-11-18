# How matching works

```bash
jobpilot enrich          # fetch missing descriptions (Greenhouse/Lever/Ashby postings only)
jobpilot match           # score new or changed jobs
jobpilot matches --with-description --reasons
jobpilot match show 123  # skill-by-skill breakdown for one job
jobpilot gaps            # Skill Gap Report; --export gaps.md or gaps.csv
jobpilot runs            # run log with token costs (Claude engine)
```

## Local engine (default, free, offline)

1. **Skills the job asks for.** The description is split into *required* (under headings like
   "Minimum qualifications", "Requirements", "What you'll need"), *preferred* ("Preferred",
   "Nice to have", or any line that says "a plus"), and everything else ("mentioned"). Skills are found
   with the list in [`jobpilot/matching/data/skills.yaml`](../jobpilot/matching/data/skills.yaml):
   108 skills with aliases. Short or ambiguous names like C, R and Go use careful case-sensitive
   patterns, so "Series C", "R&D" and "go-to" don't count. If a description has no recognizable
   headings, every skill it mentions counts as required.

2. **What you have.** Only from your confirmed `profile.yaml`:
   - **have**: the skill appears in an experience or project bullet, or in your skills list.
   - **partial**: only in coursework or your degree, or you have a closely related skill from the same
     family (Docker → Kubernetes, TensorFlow → PyTorch, AWS → GCP).
   - **missing**: none of the above.
   - Some skills imply others: PostgreSQL → SQL, React → JavaScript.
   Every "have" or "partial" shows the exact profile text it came from (`jobpilot match show`).

3. **Score (0-100).**
   - 70% skills: required coverage counts 75%, preferred 25% (have = 1, partial = 0.5).
   - 15% role/field fit: does the job's field match your target fields?
   - 15% location: your locations and remote preference.
   - −25 if the posting asks for a grad degree (PhD, Master's) and you don't have one.
     Postings open to BS/MS/PhD aren't penalized.

4. **Confidence.** *High* means a full description with 5+ skills found. *Medium* means 3+ skills.
   *Low* means few skills, or no description at all. With no description, the skills part is held
   near the middle (40-60%) because a title alone says little.

Jobs scoring at least `matching.min_score` (default 50) move from **Found** to **Matched**.
Jobs you've approved, rejected or otherwise acted on keep their status. Scores are redone
automatically when your profile or a job's description changes.

To change the skill list, copy `skills.yaml` to `data/skills.yaml` and edit it. JobPilot uses your copy if it exists.

## Claude engine (optional)

You don't need this. It's here for later if you get an API key.

```bash
pip install -e ".[claude]"
# add ANTHROPIC_API_KEY=... to .env
jobpilot match --engine claude --limit 50
```

- The model is configurable (`matching.claude_model`, default `claude-haiku-5-5`, about $0.10 per million input
  tokens and $0.50 per million output). Requests run in parallel (`claude_concurrency`, default 4).
  Your profile goes in a cached system prompt so it isn't paid for in full on every job.
- **Spending is capped.** A run stops once its estimated cost reaches `claude_spend_cap_usd`
  (default $1.00) or `claude_max_jobs_per_run` (default 200). Tokens and cost for every run are in
  `jobpilot runs`.
- **Same honesty rule.** Claude must quote your profile for every skill it says you have. If the quote
  isn't actually in your profile, that skill is marked missing and the reason says so.
