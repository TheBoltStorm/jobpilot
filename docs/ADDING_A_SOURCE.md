# Adding a job source

1. **Check that you're allowed.** Prefer an official or public API or feed. Read the site's terms and
   robots.txt. If the terms forbid automated access, don't add it. Note what you found in
   [SOURCES.md](SOURCES.md).

2. **Create `jobpilot/sources/<name>.py`** with a `JobSource` subclass:

```python
from typing import Iterator

from jobpilot.sources.base import JobSource, RawJob, ScopeResult
from jobpilot.sources.normalize import classify_remote, classify_role_type, html_to_text, to_date


class ExampleSource(JobSource):
    name = "example"                       # also its key under `sources:` in settings.yaml
    label = "Example job feed"
    terms_note = "Public API documented at example.com/developers; robots.txt allows /api."

    def fetch(self) -> Iterator[ScopeResult]:
        try:
            data = self.http.get_json("https://example.com/api/jobs")   # always use self.http
        except Exception as exc:
            yield ScopeResult("all", [], complete=False, error=str(exc))
            return
        jobs = []
        for j in data["jobs"]:
            jobs.append(RawJob(
                source_id=str(j["id"]),
                title=j["title"],
                company=j["company"],
                url=j["url"],
                locations=[j["location"]] if j.get("location") else [],
                description=html_to_text(j.get("description_html", "")),
                date_posted=to_date(j.get("posted_at")),
                role_type=classify_role_type(j["title"], j.get("employment_type", "")),
                remote=classify_remote([j.get("location", "")]),
            ))
        # complete=True means "this is the full listing", so jobs that disappear get marked closed.
        # Use complete=False for searches or partial pages.
        yield ScopeResult("all", jobs, complete=True)
```

3. **Register it** in `ALL_SOURCES` in [`jobpilot/sources/__init__.py`](../jobpilot/sources/__init__.py).

4. **Add a settings toggle.** Add a field named after `name` to `Sources` in
   [`jobpilot/settings.py`](../jobpilot/settings.py), e.g. `example: SourceToggle = Field(default_factory=SourceToggle)`.
   If it needs an API key, override `enabled()` to check the environment, like `usajobs.py` does.

5. **Test it** without the network: add a fake response to `tests/fakes.py` and a test in
   `tests/test_sources.py` (see the Greenhouse test).

Rules for `RawJob`: leave a field blank when the source doesn't say. Never guess. Discovery handles
filtering, dedupe and closing postings for you.

If the source is another ATS that's queried once per company (like Greenhouse), subclass
`BoardSource`, set `ats = "<name>"`, and implement `fetch_company()`. Companies then come from
`data/companies.yaml`.
