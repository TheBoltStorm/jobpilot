"""Fake API responses shaped like the real ones (fields trimmed). All companies/jobs are fictional."""

from __future__ import annotations

import json

import httpx

from jobpilot.core.http import PoliteClient

GREENHOUSE = {
    "jobs": [
        {
            "id": 111, "title": "Software Engineer Intern, Summer 2027", "company_name": "Acme Robotics",
            "absolute_url": "https://job-boards.greenhouse.io/acmerobotics/jobs/111?utm_source=x",
            "location": {"name": "Chicago, IL"}, "first_published": "2026-11-01T10:00:00-05:00",
            "updated_at": "2026-11-10T10:00:00-05:00",
            "content": "&lt;p&gt;Build &lt;strong&gt;Python&lt;/strong&gt; services.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;",
            "departments": [{"name": "Engineering"}], "metadata": [],
        },
        {
            "id": 112, "title": "Senior Account Executive", "company_name": "Acme Robotics",
            "absolute_url": "https://job-boards.greenhouse.io/acmerobotics/jobs/112",
            "location": {"name": "Remote - US"}, "first_published": "2026-11-02T10:00:00-05:00",
            "content": "&lt;p&gt;Sell things.&lt;/p&gt;", "departments": [{"name": "Sales"}], "metadata": [],
        },
        {
            "id": 113, "title": "Data Science Intern", "company_name": "Acme Robotics",
            "absolute_url": "https://job-boards.greenhouse.io/acmerobotics/jobs/113",
            "location": {"name": "New York, NY"}, "first_published": "2025-01-01T10:00:00-05:00",
            "content": "", "departments": [], "metadata": [],
        },
    ]
}

LEVER = [
    {
        "id": "abc-1", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/blueowl/abc-1",
        "applyUrl": "https://jobs.lever.co/blueowl/abc-1/apply",
        "categories": {"commitment": "Full-time", "location": "Austin, TX", "team": "Platform",
                       "allLocations": ["Austin, TX"]},
        "createdAt": 1793500000000, "workplaceType": "hybrid",
        "openingPlain": "Join us.", "descriptionBodyPlain": "We use Go and Postgres.",
        "lists": [{"text": "Requirements", "content": "<li>Go</li><li>Kubernetes</li>"}], "additionalPlain": "",
    },
    {
        "id": "abc-2", "text": "Software Engineering Co-op", "hostedUrl": "https://jobs.lever.co/blueowl/abc-2",
        "categories": {"commitment": "Intern", "location": "Boston, MA"}, "createdAt": 1793500000000,
        "workplaceType": "onsite", "descriptionPlain": "Six month co-op.",
    },
]

ASHBY = {
    "jobs": [
        {
            "id": "z-1", "title": "Machine Learning Intern", "employmentType": "Intern",
            "location": "San Francisco, CA", "secondaryLocations": [{"location": "Remote"}],
            "jobUrl": "https://jobs.ashbyhq.com/zebra/z-1", "applyUrl": "https://jobs.ashbyhq.com/zebra/z-1/application",
            "publishedAt": "2026-11-05T00:00:00.000+00:00", "isListed": True, "isRemote": False,
            "workplaceType": "Hybrid", "department": "Research", "descriptionPlain": "PyTorch, statistics.",
        },
        {"id": "z-2", "title": "Hidden Role", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/zebra/z-2"},
    ]
}

LISTINGS = [
    {   # same job as Greenhouse 111, different URL form and title wording
        "id": "s1", "company_name": "Acme Robotics Inc.", "title": "Software Engineering Intern",
        "locations": ["Chicago, IL"], "url": "https://boards.greenhouse.io/acmerobotics/jobs/111?gh_src=simplify",
        "date_posted": 1793600000, "active": True, "is_visible": True, "category": "Software", "terms": ["Summer 2027"],
    },
    {
        "id": "s2", "company_name": "Pine Labs", "title": "Hardware Intern", "locations": ["Denver, CO"],
        "url": "https://pine.example.com/careers/9", "date_posted": 1793600000, "active": True,
        "is_visible": True, "category": "Hardware", "terms": ["Summer 2027"],
    },
    {
        "id": "s3", "company_name": "Old Co", "title": "Software Intern", "locations": ["Remote"],
        "url": "https://oldco.wd5.myworkdayjobs.com/jobs/3", "date_posted": 1793600000, "active": False,
        "is_visible": True, "category": "Software",
    },
    {
        "id": "s4", "company_name": "Maple AI", "title": "Software Engineer Intern", "locations": ["Seattle, WA"],
        "url": "https://maple.wd1.myworkdayjobs.com/en-US/careers/job/Seattle/SWE-Intern_R1",
        "date_posted": 1793600000, "active": True, "is_visible": True, "category": "Software",
    },
]

README_MD = """
| Company | Role | Location | Application | Age |
| ------- | ---- | -------- | ----------- | --- |
| **[Fern Co](https://fern.example.com)** | Software Intern | Austin, TX | [Apply](https://jobs.lever.co/fern/1) | 2d |
| ↳ | Data Intern | Remote | [Apply](https://jobs.lever.co/fern/2) | 2d |
| Gone Inc | Closed Intern | NYC | 🔒 | 9d |
"""

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Birch Careers</title>
<item><title>Frontend Intern</title><link>https://birch.example.com/jobs/1</link>
<guid>birch-1</guid><description>&lt;p&gt;React&lt;/p&gt;</description><pubDate>Mon, 10 Nov 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Cedar Jobs</title>
<entry><title>Data Analyst Intern</title><link href="https://cedar.example.com/j/7"/><id>cedar-7</id>
<published>2026-11-09T00:00:00Z</published><summary>SQL and Tableau</summary></entry>
</feed>"""

USAJOBS = {
    "SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {
        "PositionID": "DOE-1", "PositionTitle": "Student Trainee (Information Technology)",
        "PositionURI": "https://www.usajobs.gov/job/123", "ApplyURI": ["https://www.usajobs.gov/job/123/apply"],
        "PositionLocation": [{"LocationName": "Washington, District of Columbia"}],
        "OrganizationName": "Office of Science", "DepartmentName": "Department of Energy",
        "PositionSchedule": [{"Name": "Part-time"}], "PublicationStartDate": "2026-11-03T00:00:00.0000",
        "JobCategory": [{"Name": "Information Technology Management"}],
        "QualificationSummary": "Enrolled in a degree program.", "UserArea": {"Details": {"JobSummary": "IT work."}},
    }}]}
}


def make_transport(routes: dict[str, object], calls: list[str] | None = None) -> httpx.MockTransport:
    """routes: url prefix -> JSON-able object, str body, or int status."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        if url.endswith("/robots.txt") and not any(url.startswith(k) for k in routes if k.endswith("robots.txt")):
            return httpx.Response(404)
        for prefix, body in sorted(routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                if isinstance(body, int):
                    return httpx.Response(body)
                if isinstance(body, str):
                    return httpx.Response(200, text=body)
                return httpx.Response(200, text=json.dumps(body), headers={"content-type": "application/json"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def make_client(routes: dict[str, object], calls: list[str] | None = None, sleeps: list[float] | None = None) -> PoliteClient:
    return PoliteClient(
        "JobPilot-test", min_interval=0.0, transport=make_transport(routes, calls),
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


ROUTES = {
    "https://boards-api.greenhouse.io/v1/boards/acmerobotics/jobs": GREENHOUSE,
    "https://api.lever.co/v0/postings/blueowl": LEVER,
    "https://api.ashbyhq.com/posting-api/job-board/zebra": ASHBY,
    "https://raw.githubusercontent.com/fake/Interns/dev/.github/scripts/listings.json": LISTINGS,
}
