import httpx
import pytest

from jobpilot.core.http import PoliteClient, RobotsDisallowed
from tests import fakes


def test_robots_disallow_blocks_request():
    calls = []
    http = fakes.make_client({"https://x.test/robots.txt": "User-agent: *\nDisallow: /private\n",
                              "https://x.test/": {"ok": 1}}, calls)
    assert http.get_json("https://x.test/public") == {"ok": 1}
    with pytest.raises(RobotsDisallowed):
        http.get("https://x.test/private/page")
    assert calls.count("https://x.test/robots.txt") == 1  # cached


def test_robots_404_means_allowed():
    http = fakes.make_client({"https://y.test/": "hi"})
    assert http.get("https://y.test/a").text == "hi"


def test_robots_500_means_disallowed():
    http = fakes.make_client({"https://z.test/robots.txt": 503, "https://z.test/": "hi"})
    with pytest.raises(RobotsDisallowed):
        http.get("https://z.test/a")


def test_rate_limit_and_crawl_delay():
    sleeps = []
    http = PoliteClient("t", min_interval=0.5, sleep=sleeps.append, transport=fakes.make_transport(
        {"https://r.test/robots.txt": "User-agent: *\nCrawl-delay: 2\n", "https://r.test/": "ok"}))
    http.get("https://r.test/1")
    http.get("https://r.test/2")
    assert sleeps and max(sleeps) > 1.5  # crawl-delay (2s) beats the 0.5s default


def test_retries_on_429_then_succeeds():
    state = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, text="ok")

    sleeps = []
    http = PoliteClient("t", min_interval=0, sleep=sleeps.append, transport=httpx.MockTransport(handler))
    assert http.get("https://q.test/x").text == "ok"
    assert state["n"] == 3 and 1.0 in sleeps


def test_gives_up_after_retries():
    http = PoliteClient("t", min_interval=0, sleep=lambda s: None, max_retries=1,
                        transport=fakes.make_transport({"https://e.test/x": 500}))
    with pytest.raises(httpx.HTTPStatusError):
        http.get("https://e.test/x")
