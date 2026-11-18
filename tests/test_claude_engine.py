"""Claude engine tests with a fake client: no network, no API key, no cost."""

import types

import pytest

from jobpilot.core import db
from jobpilot.matching.claude import LLMMatch, LLMSkill, cost_usd, run_claude
from jobpilot.matching.skills import load_taxonomy
from jobpilot.settings import MatchingSettings
from tests.test_matching import JOB_DESC, _insert, profile  # noqa: F401  (fixture)

TAX = load_taxonomy()


class FakeMessages:
    def __init__(self, answer: LLMMatch, usage=(1000, 200, 0, 0), stop="end_turn"):
        self.answer, self.usage, self.stop = answer, usage, stop
        self.calls = []

    async def parse(self, **kw):
        self.calls.append(kw)
        i, o, cr, cw = self.usage
        return types.SimpleNamespace(
            parsed_output=self.answer, stop_reason=self.stop,
            usage=types.SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr,
                                        cache_creation_input_tokens=cw),
        )


def fake_client(answer, **kw):
    return types.SimpleNamespace(messages=FakeMessages(answer, **kw))


ANSWER = LLMMatch(score=77, reason="Strong Python backend fit; no Go.", skills=[
    LLMSkill(name="Python", importance="required", status="have",
             evidence="Built a Python service that parsed 40k daily shipment events"),
    LLMSkill(name="Go", importance="required", status="missing", evidence=""),
    LLMSkill(name="Kubernetes", importance="preferred", status="have", evidence="Ran Kubernetes clusters at scale"),
])


@pytest.fixture
def conn(paths):
    with db.connect(paths.db) as c:
        _insert(c, 1, "Software Engineer Intern", JOB_DESC)
        _insert(c, 2, "Backend Intern", JOB_DESC)
        c.commit()
        yield c


def test_claude_scores_and_downgrades_unsupported_claims(conn, profile):
    client = fake_client(ANSWER)
    s = run_claude(conn, profile, TAX, MatchingSettings(), client=client, log=lambda m: None)
    assert s.scored == 2 and s.engine == "claude:claude-haiku-5-5"
    import json
    row = conn.execute("SELECT score, reason, skills_json, engine FROM matches WHERE job_id = 1").fetchone()
    skills = {x["key"]: x for x in json.loads(row["skills_json"])["skills"]}
    assert skills["python"]["status"] == "have"
    assert skills["kubernetes"]["status"] == "missing"  # quote isn't in the profile -> not trusted
    assert "had no matching quote" in row["reason"]
    # Profile goes in a cached system block; job goes in the user message.
    call = client.messages.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Northwind Logistics" in call["system"][0]["text"]
    assert call["output_format"] is LLMMatch and call["model"] == "claude-haiku-5-5"


def test_cost_tracking_and_run_log(conn, profile):
    run_claude(conn, profile, TAX, MatchingSettings(), client=fake_client(ANSWER), log=lambda m: None)
    r = conn.execute("SELECT * FROM match_runs ORDER BY id DESC").fetchone()
    assert r["jobs_scored"] == 2 and r["input_tokens"] == 2000 and r["output_tokens"] == 400
    assert r["cost_usd"] == pytest.approx(2 * (1000 * 0.10 + 200 * 0.50) / 1e6)


def test_cost_formula_counts_cache():
    u = types.SimpleNamespace(input_tokens=100, output_tokens=0, cache_read_input_tokens=1000,
                              cache_creation_input_tokens=0)
    assert cost_usd("claude-sonnet-5-5", u) == pytest.approx((100 * 2 + 1000 * 0.2) / 1e6)


def test_spend_cap_stops_run(conn, profile):
    cfg = MatchingSettings(claude_spend_cap_usd=0.0001, claude_concurrency=1)
    logs = []
    s = run_claude(conn, profile, TAX, cfg, client=fake_client(ANSWER, usage=(1000, 1000, 0, 0)), log=logs.append)
    assert s.scored == 1 and any("spend reached" in m for m in logs)


def test_refusal_is_skipped(conn, profile):
    s = run_claude(conn, profile, TAX, MatchingSettings(), client=fake_client(None, stop="refusal"), log=lambda m: None)
    assert s.scored == 0


def test_needs_api_key(conn, profile, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        run_claude(conn, profile, TAX, MatchingSettings())
