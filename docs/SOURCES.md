# Job sources

Every source was checked on 2026-11-17. Run `jobpilot sources` to see what's enabled on your machine.

All requests go through one polite client ([`jobpilot/core/http.py`](../jobpilot/core/http.py)):
robots.txt is checked before each new host, there is at least 1 second between requests to the same
host (or the site's `Crawl-delay` if it's longer), and 429/5xx responses back off and retry.

## Included

| Source | How | Why it's OK |
|---|---|---|
| **Greenhouse** boards | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Greenhouse's public Job Board API, meant for showing a company's open jobs. robots.txt only disallows `/embed/`. |
| **Lever** boards | `GET api.lever.co/v0/postings/{token}?mode=json` | Lever's public Postings API. robots.txt allows all, `Crawl-delay: 1` (respected). |
| **Ashby** boards | `GET api.ashbyhq.com/posting-api/job-board/{token}` | Ashby's public Job Posting API for embedding job boards. robots.txt returns 401, which RFC 9309 treats as "no restrictions". Unlisted jobs (`isListed: false`) are skipped. |
| **GitHub lists**: SimplifyJobs Summer2027-Internships and New-Grad-Positions; vanshb03 Summer2027-Internships and New-Grad-2027 | The `listings.json` file each repo publishes (`raw.githubusercontent.com`). Falls back to parsing the README table if the JSON goes away. | Public, community-maintained data. The vanshb03 repos are MIT-licensed. The SimplifyJobs repos have no license file, so JobPilot only uses their data for your own search and always links back to the original posting. It never republishes it. |
| **USAJOBS** (off by default) | `GET data.usajobs.gov/api/search` with your free API key | Official API from the US Office of Personnel Management. Get a key at developer.usajobs.gov and put `USAJOBS_API_KEY` and `USAJOBS_EMAIL` in `.env` (USAJOBS requires your email as the User-Agent). Then set `sources.usajobs.enabled: true`. |
| **RSS/Atom feeds** (none by default) | Feeds you list under `sources.rss.feeds` in `settings.yaml` | Feeds exist to be syndicated. Each one is still checked against robots.txt. |
| **CSV import** | `jobpilot import-csv file.csv --name handshake` | Files you exported yourself, e.g. from Handshake or a career fair spreadsheet. JobPilot never logs in to Handshake for you. |

Season repos get renamed each year (Summer2027 → Summer2028). Update `sources.github_lists.lists`
in `data/settings.yaml` when a new season opens.

## Left out, and why

| Source | Reason |
|---|---|
| LinkedIn | Its User Agreement prohibits scraping and automated access, and there's no public job-search API. |
| Indeed | Its terms prohibit scraping, and the public job-search API was retired. |
| Glassdoor | Its terms prohibit scraping, and there's no public jobs API. |
| ZipRecruiter | Its terms prohibit scraping. The API is only for partners. |
| Handshake (direct) | Needs your school login, and there's no public API. Use CSV export instead. |
| Workday / iCIMS / Taleo / Oracle career sites (direct) | There's no public listing API, and applying needs an account. Jobs from these systems still show up through the GitHub lists. Later phases put them in the "Account wall – needs human" bucket. |
| SmartRecruiters, Workable | Both have public posting endpoints, but their terms haven't been reviewed yet. Candidates for a later phase. |

## Known gaps

- **Descriptions.** The GitHub lists don't include job descriptions. Jobs from Greenhouse, Lever and
  Ashby boards come with full descriptions. Phase 3 will fetch descriptions for listed jobs through
  the same public APIs when the posting is hosted on one of those three systems.
- **Short links.** Some vanshb03 entries use `go.vansh.nyc` short links. They're stored as-is and get
  replaced automatically when the same job shows up with a direct link from another source.
