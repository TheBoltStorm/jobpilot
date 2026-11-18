"""Optional Claude engine for matching. Off unless ANTHROPIC_API_KEY is set and
settings.matching.engine is "claude". Install with: pip install -e ".[claude]"

Safety rules carried over from the local matcher:
- Claude only sees your confirmed profile and the job text.
- Any skill Claude marks "have"/"partial" must quote evidence that really appears in your
  profile. If the quote isn't found, the skill is downgraded to "missing". Nothing gets embellished.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from typing import Callable, Literal

import yaml
from pydantic import BaseModel, Field

from jobpilot.core.db import now_iso
from jobpilot.matching.engine import MatchSummary, jobs_to_score, profile_hash, save_match
from jobpilot.matching.local import MatchResult, SkillLine
from jobpilot.matching.skills import Taxonomy
from jobpilot.onboarding.profile import Profile
from jobpilot.settings import MatchingSettings
from jobpilot.sources.fields import job_tags

# USD per million tokens: (input, output). Cache reads ~0.1x input, cache writes ~1.25x input.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


class LLMSkill(BaseModel):
    name: str = Field(description="Skill name as the job states it, e.g. 'Kubernetes'")
    importance: Literal["required", "preferred"]
    status: Literal["have", "partial", "missing"]
    evidence: str = Field(description="Exact quote from the candidate profile proving it, or '' if missing")


class LLMMatch(BaseModel):
    score: int = Field(description="Overall fit 0-100")
    reason: str = Field(description="One or two plain sentences explaining the score")
    skills: list[LLMSkill]


SYSTEM = """You compare one job posting against one candidate profile for a job-search tool.

Rules:
- Use ONLY facts in the candidate profile below. Never assume skills or experience that are not written there.
- For each concrete skill the job asks for, decide: "have" (profile clearly shows it), "partial" (related
  experience or coursework only), or "missing".
- For "have" and "partial", `evidence` must be an exact quote copied from the profile. If you can't quote it,
  the status is "missing".
- Mark importance "required" for minimum/basic qualifications, "preferred" for nice-to-haves.
- Score 0-100 for overall fit, considering skills, the candidate's target roles/fields, locations, remote
  preference, role type, and degree level. Be calibrated: 80+ means a strong, realistic fit.
- Keep `reason` short, specific and plain. No hype.

Candidate profile (YAML):
"""


def cost_usd(model: str, usage) -> float:
    inp, out = PRICES.get(model, PRICES["claude-haiku-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * inp + cache_read * inp * 0.1 + cache_write * inp * 1.25
            + usage.output_tokens * out) / 1_000_000


def _profile_text(profile: Profile) -> str:
    data = profile.model_dump(mode="json", exclude={"default_resume", "version", "short_answers"})
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", s.lower()).strip()


def verify(llm: LLMMatch, profile_text: str, tax: Taxonomy, job) -> MatchResult:
    """Turn Claude's answer into a MatchResult, downgrading any claim without a real quote."""
    haystack = _norm(profile_text)
    lines: list[SkillLine] = []
    downgraded = 0
    for s in llm.skills:
        status, evidence = s.status, s.evidence.strip()
        if status != "missing" and (not evidence or _norm(evidence) not in haystack):
            status, evidence = "missing", ""
            downgraded += 1
        keys = list(tax.find(s.name))
        key = keys[0] if keys else "llm:" + _norm(s.name).replace(" ", "_")
        name = tax.skills[key].name if key in tax.skills else s.name
        lines.append(SkillLine(key, name, status, s.importance, s.name, evidence))
    reason = llm.reason.strip()
    if downgraded:
        reason += f" ({downgraded} skill claim(s) had no matching quote in your profile and were marked missing.)"
    score = max(0, min(100, int(llm.score)))
    confidence = "medium" if job["description"] else "low"
    return MatchResult(score, confidence, reason, sorted(job_tags(job["title"], job["field"] or "")), lines)


async def _score_all(client, rows, profile: Profile, tax: Taxonomy, cfg: MatchingSettings, conn, phash,
                     summary: MatchSummary, log) -> str:
    profile_text = _profile_text(profile)
    system = [{"type": "text", "text": SYSTEM + profile_text, "cache_control": {"type": "ephemeral"}}]
    sem = asyncio.Semaphore(cfg.claude_concurrency)
    stop = asyncio.Event()
    errors: list[str] = []

    async def one(job):
        if stop.is_set():
            return
        async with sem:
            if stop.is_set():
                return
            body = (f"Job title: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}"
                    f" ({job['remote'] or 'unspecified'})\nRole type: {job['role_type'] or 'unspecified'}\n\n"
                    f"Description:\n{job['description'] or '(no description available; judge from the title only)'}")
            try:
                resp = await client.messages.parse(
                    model=cfg.claude_model, max_tokens=4000, system=system,
                    messages=[{"role": "user", "content": body}], output_format=LLMMatch,
                )
            except Exception as exc:  # network/API errors: record, keep going
                errors.append(f"job {job['id']}: {type(exc).__name__}: {str(exc)[:120]}")
                return
            u = resp.usage
            summary.input_tokens += u.input_tokens + (getattr(u, "cache_read_input_tokens", 0) or 0) \
                + (getattr(u, "cache_creation_input_tokens", 0) or 0)
            summary.output_tokens += u.output_tokens
            summary.cost_usd += cost_usd(cfg.claude_model, u)
            if summary.cost_usd >= cfg.claude_spend_cap_usd:
                stop.set()
            if resp.stop_reason == "refusal" or resp.parsed_output is None:
                errors.append(f"job {job['id']}: no usable answer ({resp.stop_reason})")
                return
            result = verify(resp.parsed_output, profile_text, tax, job)
            with conn:
                summary.matched += save_match(conn, job, result, f"claude:{cfg.claude_model}", phash, cfg.min_score)
            summary.scored += 1

    await asyncio.gather(*(one(j) for j in rows))
    if stop.is_set():
        log(f"Stopped early: estimated spend reached the ${cfg.claude_spend_cap_usd:.2f} cap.")
    for e in errors[:5]:
        log(f"  ! {e}")
    return "; ".join(errors[:3])


def run_claude(conn: sqlite3.Connection, profile: Profile, tax: Taxonomy, cfg: MatchingSettings,
               rescore: bool = False, limit: int | None = None, client=None,
               log: Callable[[str], None] = print) -> MatchSummary:
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY in .env to use the Claude engine (or use engine: local).")
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError('Install the Claude extra first: pip install -e ".[claude]"') from exc
        client = anthropic.AsyncAnthropic()
    phash = profile_hash(profile)
    rows = jobs_to_score(conn, phash, rescore, min(limit or cfg.claude_max_jobs_per_run, cfg.claude_max_jobs_per_run))
    summary = MatchSummary(engine=f"claude:{cfg.claude_model}")
    started = now_iso()
    error = asyncio.run(_score_all(client, rows, profile, tax, cfg, conn, phash, summary, log))
    with conn:
        conn.execute(
            """INSERT INTO match_runs (engine, started_at, finished_at, jobs_scored, input_tokens, output_tokens,
                                       cost_usd, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (summary.engine, started, now_iso(), summary.scored, summary.input_tokens, summary.output_tokens,
             round(summary.cost_usd, 6), error or None),
        )
    return summary


def skills_from_json(raw: str) -> list[dict]:
    return json.loads(raw).get("skills", [])
