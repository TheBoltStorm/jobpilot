"""Polite HTTP: robots.txt check, per-host rate limit, retries with backoff.

Every network request JobPilot makes goes through PoliteClient.
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx


class RobotsDisallowed(Exception):
    pass


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        max_retries: int = 3,
    ):
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._sleep = sleep
        self._client = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True, transport=transport
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._delay: dict[str, float] = {}
        self._lock = threading.Lock()
        self.requests_made = 0

    # ---- robots.txt ------------------------------------------------------

    def _robots_for(self, scheme: str, host: str) -> urllib.robotparser.RobotFileParser | None:
        key = f"{scheme}://{host}"
        if key in self._robots:
            return self._robots[key]
        rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        self._wait(host)
        try:
            resp = self._client.get(f"{key}/robots.txt")
            self.requests_made += 1
            if resp.status_code >= 500:
                rp.disallow_all = True  # RFC 9309: server error -> assume disallowed
            elif resp.status_code >= 400:
                rp = None  # RFC 9309: unavailable (4xx) -> no restrictions
            else:
                rp.parse(resp.text.splitlines())
                delay = rp.crawl_delay(self.user_agent)
                if delay:
                    self._delay[host] = float(delay)
        except httpx.HTTPError:
            rp = None
        self._robots[key] = rp
        return rp

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        rp = self._robots_for(parts.scheme, parts.netloc)
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # ---- rate limiting ---------------------------------------------------

    def _wait(self, host: str) -> None:
        with self._lock:
            interval = max(self.min_interval, self._delay.get(host, 0.0))
            last = self._last.get(host)
            now = time.monotonic()
            if last is not None and now - last < interval:
                self._sleep(interval - (now - last))
            self._last[host] = time.monotonic()

    # ---- requests --------------------------------------------------------

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        host = urlsplit(url).netloc
        backoff = 2.0
        for attempt in range(self.max_retries + 1):
            self._wait(host)
            try:
                resp = self._client.get(url, params=params, headers=headers)
                self.requests_made += 1
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                self._sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")  # pragma: no cover

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def close(self) -> None:
        self._client.close()
