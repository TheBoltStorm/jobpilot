"""Sort job locations into regions and areas, so you can browse and search by place.

A posting's location text ("San Jose, CA", "NYC", "Toronto, ON, Canada", "Chicago, IL; NYC",
"Remote in USA") becomes one or more places, each with:
- region: Remote, US Northeast / Midwest / South / West, Canada, Europe, Asia-Pacific,
  Latin America, Other or Unknown;
- area: a metro area for big job markets ("SF Bay Area", "New York City area"), otherwise "City, ST".
Only the posting's own text is used. Nothing is looked up online.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
_STATE_BY_NAME = {v.lower(): k for k, v in US_STATES.items()}
_US_REGION = {
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX PR",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA",
}
STATE_REGION = {st: f"US {r}" for r, sts in _US_REGION.items() for st in sts.split()}

CA_PROVINCES = {"AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
                "NL": "Newfoundland", "NS": "Nova Scotia", "ON": "Ontario", "PE": "PEI", "QC": "Quebec",
                "SK": "Saskatchewan"}
_PROV_BY_NAME = {v.lower(): k for k, v in CA_PROVINCES.items()}

EUROPE = {"united kingdom", "uk", "england", "scotland", "wales", "ireland", "germany", "france", "netherlands",
          "spain", "portugal", "italy", "switzerland", "austria", "belgium", "denmark", "sweden", "norway",
          "finland", "poland", "czech republic", "czechia", "romania", "greece", "hungary", "estonia", "lithuania",
          "latvia", "luxembourg", "serbia", "croatia", "bulgaria", "ukraine", "europe", "emea"}
ASIA_PACIFIC = {"india", "china", "japan", "singapore", "south korea", "korea", "taiwan", "hong kong", "australia",
                "new zealand", "vietnam", "philippines", "indonesia", "malaysia", "thailand", "apac"}
LATAM = {"mexico", "brazil", "argentina", "chile", "colombia", "peru", "costa rica", "uruguay", "latam"}
COUNTRY_NAMES = {"uk": "United Kingdom", "england": "United Kingdom", "scotland": "United Kingdom",
                 "wales": "United Kingdom", "usa": "United States", "us": "United States", "u.s.": "United States",
                 "united states of america": "United States", "czechia": "Czech Republic"}

# Single words that are enough on their own: city -> (city, state-or-country)
KNOWN_CITIES = {
    "nyc": ("New York", "NY"), "new york city": ("New York", "NY"), "sf": ("San Francisco", "CA"),
    "san francisco": ("San Francisco", "CA"), "bay area": ("San Francisco", "CA"), "la": ("Los Angeles", "CA"),
    "los angeles": ("Los Angeles", "CA"), "seattle": ("Seattle", "WA"), "boston": ("Boston", "MA"),
    "chicago": ("Chicago", "IL"), "austin": ("Austin", "TX"), "atlanta": ("Atlanta", "GA"),
    "denver": ("Denver", "CO"), "pittsburgh": ("Pittsburgh", "PA"), "washington dc": ("Washington", "DC"),
    "dc": ("Washington", "DC"), "london": ("London", "United Kingdom"), "toronto": ("Toronto", "Canada"),
    "montreal": ("Montreal", "Canada"), "vancouver": ("Vancouver", "Canada"), "dublin": ("Dublin", "Ireland"),
    "berlin": ("Berlin", "Germany"), "paris": ("Paris", "France"), "amsterdam": ("Amsterdam", "Netherlands"),
    "bangalore": ("Bangalore", "India"), "bengaluru": ("Bangalore", "India"), "singapore": ("Singapore", "Singapore"),
    "tokyo": ("Tokyo", "Japan"), "sydney": ("Sydney", "Australia"), "new york": ("New York", "NY"),
    "south sf": ("South San Francisco", "CA"), "south san francisco": ("South San Francisco", "CA"),
    "zurich": ("Zurich", "Switzerland"), "munich": ("Munich", "Germany"),
}
# Two-letter country codes that don't clash with a US state code.
COUNTRY_CODES = {"UK": "United Kingdom", "GB": "United Kingdom", "IE": "Ireland", "CH": "Switzerland",
                 "NL": "Netherlands", "FR": "France", "ES": "Spain", "SE": "Sweden", "PL": "Poland", "JP": "Japan",
                 "SG": "Singapore", "AU": "Australia", "CN": "China", "TW": "Taiwan", "KR": "South Korea",
                 "MX": "Mexico", "BR": "Brazil"}

# Metro areas for the biggest job markets. Key: "city|ST" (state matters: Cambridge MA vs UK).
_METROS = {
    "SF Bay Area": ("CA", "san francisco, san jose, palo alto, santa clara, sunnyvale, mountain view, san mateo, "
                          "menlo park, los altos, milpitas, fremont, burlingame, oakland, redwood city, cupertino, "
                          "berkeley, emeryville, foster city, south san francisco, san carlos, campbell, "
                          "los gatos, pleasanton, livermore, hayward, san bruno, belmont, newark, union city, "
                          "south san francisco, san ramon, walnut creek"),
    "New York City area": ("NY", "new york, brooklyn, manhattan, queens, bronx, long island city, white plains"),
    "Seattle area": ("WA", "seattle, redmond, bellevue, kirkland, bothell, everett, tacoma, renton"),
    "Boston area": ("MA", "boston, cambridge, somerville, waltham, burlington, lexington, woburn, quincy, "
                          "bedford, needham, newton, marlborough, andover"),
    "Los Angeles area": ("CA", "los angeles, santa monica, el segundo, culver city, pasadena, burbank, "
                               "long beach, torrance, irvine, hawthorne, glendale, playa vista, costa mesa"),
    "San Diego area": ("CA", "san diego, la jolla, carlsbad"),
    "Dallas-Fort Worth": ("TX", "dallas, fort worth, plano, irving, richardson, frisco, addison, arlington"),
    "Houston area": ("TX", "houston, the woodlands, sugar land"),
    "Austin area": ("TX", "austin, round rock"),
    "Washington DC area": ("", "washington|DC, mclean|VA, arlington|VA, reston|VA, herndon|VA, alexandria|VA, "
                               "chantilly|VA, fairfax|VA, tysons|VA, laurel|MD, bethesda|MD, rockville|MD, "
                               "columbia|MD, annapolis junction|MD, gaithersburg|MD, silver spring|MD"),
    "Chicago area": ("IL", "chicago, evanston, naperville, schaumburg, oak brook, deerfield, northbrook"),
    "Raleigh-Durham": ("NC", "raleigh, durham, cary, morrisville, research triangle park, chapel hill"),
    "Denver-Boulder": ("CO", "denver, boulder, golden, broomfield, englewood, louisville, lakewood"),
    "Atlanta area": ("GA", "atlanta, alpharetta, marietta, duluth"),
    "Philadelphia area": ("PA", "philadelphia, king of prussia, conshohocken, malvern"),
    "Twin Cities": ("MN", "minneapolis, st. paul, saint paul, bloomington, eden prairie, plymouth"),
    "Phoenix area": ("AZ", "phoenix, scottsdale, tempe, chandler, mesa"),
    "Toronto area": ("ON", "toronto, markham, mississauga, waterloo, kitchener, vaughan"),
}
METRO_OF: dict[str, str] = {}
for _metro, (_st, _cities) in _METROS.items():
    for _c in _cities.split(","):
        _c = _c.strip()
        if "|" in _c:
            METRO_OF[_c.lower()] = _metro
        else:
            METRO_OF[f"{_c}|{_st.lower()}"] = _metro
METRO_OF.update({f"{c}|nj": "New York City area" for c in ("jersey city", "hoboken", "newark")})

REGION_ORDER = ["Remote", "US Northeast", "US Midwest", "US South", "US West", "US (state unknown)", "Canada",
                "Europe", "Asia-Pacific", "Latin America", "Other", "Unknown"]

# " or " only in lower case: "OR" is Oregon.
_SPLIT = re.compile(r"\s*(?:;|\||/|\n|•)\s*|\s+or\s+")
_REMOTE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed)\b", re.IGNORECASE)
_NOISE = re.compile(r"\b(hybrid|on-?site|in[- ]office|hq|headquarters|office|locations?|multiple|and \d+ more)\b",
                    re.IGNORECASE)


@dataclass(frozen=True)
class Place:
    region: str
    area: str
    country: str = ""
    remote: bool = False


def _country_region(country: str) -> str:
    c = country.lower()
    if c == "united states":
        return "US (state unknown)"
    if c == "canada":
        return "Canada"
    if c in EUROPE or country == "United Kingdom":
        return "Europe"
    if c in ASIA_PACIFIC:
        return "Asia-Pacific"
    if c in LATAM:
        return "Latin America"
    return "Other"


def _us_place(city: str, st: str) -> Place:
    metro = METRO_OF.get(f"{city.lower()}|{st.lower()}")
    return Place(STATE_REGION.get(st, "US (state unknown)"), metro or (f"{city}, {st}" if city else US_STATES[st]),
                 "United States")


def _one(piece: str) -> Place | None:
    raw = piece.strip(" -,.()")
    if not raw:
        return None
    remote = bool(_REMOTE.search(raw))
    text = _NOISE.sub(" ", _REMOTE.sub(" ", raw))
    if remote:  # "Remote in USA" (elsewhere "IN" is Indiana)
        text = re.sub(r"\b(in|from|within|based)\b", " ", text, flags=re.IGNORECASE)
    if len(re.sub(r"[^A-Za-z]", "", text)) < 2:
        return Place("Remote", "Remote", "", True) if remote else None
    parts = [p.strip(" -.()") for p in text.split(",") if p.strip(" -.()")]
    parts = [" ".join(p.split()) for p in parts]
    if remote:
        country = ""
        for p in parts:
            name = COUNTRY_NAMES.get(p.lower(), p.title() if p.lower() in EUROPE | ASIA_PACIFIC | LATAM else "")
            if p.lower() in ("united states", "usa", "us", "u.s."):
                name = "United States"
            if p.lower() == "canada":
                name = "Canada"
            if name:
                country = name
        return Place("Remote", f"Remote ({country})" if country else "Remote", country, True)
    if not parts:
        return None
    low = [p.lower() for p in parts]
    # "City, ST" / "City, State" / "City, ST, USA"
    for i, p in enumerate(parts[1:], 1):
        up = p.upper()
        st = up if up in US_STATES else _STATE_BY_NAME.get(p.lower())
        if st and (len(parts) == 2 or low[-1] in ("usa", "us", "united states", "u.s.") or i == len(parts) - 1):
            return _us_place(parts[0], st)
        if up in COUNTRY_CODES and up not in US_STATES and i == len(parts) - 1:
            country = COUNTRY_CODES[up]
            return Place(_country_region(country), f"{parts[0]}, {country}", country)
        prov = up if up in CA_PROVINCES else _PROV_BY_NAME.get(p.lower())
        if prov and (len(parts) == 2 or low[-1] == "canada"):
            metro = METRO_OF.get(f"{parts[0].lower()}|{prov.lower()}")
            return Place("Canada", metro or f"{parts[0]}, {prov}", "Canada")
    last = low[-1]
    country = COUNTRY_NAMES.get(last) or ("United States" if last in ("united states", "usa") else
                                          parts[-1].title() if last in EUROPE | ASIA_PACIFIC | LATAM | {"canada"} else "")
    if country:
        if len(parts) == 1:
            return Place(_country_region(country), country, country)
        city = parts[0]
        if country == "United States":
            known = KNOWN_CITIES.get(city.lower())
            return _us_place(*known) if known and known[1] in US_STATES else Place(
                "US (state unknown)", f"{city}, US", country)
        if country == "Canada":
            metro = next((METRO_OF[f"{city.lower()}|{pv.lower()}"] for pv in CA_PROVINCES
                          if f"{city.lower()}|{pv.lower()}" in METRO_OF), None)
            return Place("Canada", metro or f"{city}, Canada", "Canada")
        return Place(_country_region(country), f"{city}, {country}", country)
    if len(parts) > 1 and all(x in KNOWN_CITIES for x in low):
        return None  # "New York, Seattle": a list of cities; the caller splits it
    if last in KNOWN_CITIES or (len(parts) == 1 and parts[0].lower() in KNOWN_CITIES):
        city, where = KNOWN_CITIES[low[0] if low[0] in KNOWN_CITIES else last]
        if where in US_STATES:
            return _us_place(city, where)
        return _one(f"{city}, {where}")
    if len(parts) == 1:
        p = parts[0]
        if p.upper() in US_STATES and len(p) == 2:
            return _us_place("", p.upper())
        if p.lower() in _STATE_BY_NAME:
            return _us_place("", _STATE_BY_NAME[p.lower()])
    return None


def _resolve(piece: str) -> list[Place]:
    p = _one(piece)
    if p is not None:
        return [p]
    # "San Francisco - SF9", "New York, Seattle, South San Francisco HQ": try the pieces.
    for sep in (" - ", ","):
        if sep in piece:
            found = [q for q in (_one(x) for x in piece.split(sep)) if q]
            if found:
                return found
    raw = piece.strip(" -,.()")
    meaningful = re.sub(r"[^A-Za-z]", "", _NOISE.sub(" ", raw))
    return [Place("Unknown", raw[:60])] if len(meaningful) >= 2 else []


def classify(location: str, remote: str = "") -> list[Place]:
    """All the places a posting lists. Always at least one (region Unknown if the text says nothing)."""
    out: list[Place] = []
    for piece in _SPLIT.split(location or ""):
        for p in _resolve(piece or ""):
            if p not in out:
                out.append(p)
    # A bare country next to specific places in it adds nothing ("San Jose, CA; United States").
    specific = {p.country for p in out if p.area != p.country and not p.remote}
    out = [p for p in out if not (p.area == p.country and p.country in specific)]
    # "Remote; United States" is one place: remote, in the US.
    bare = [p for p in out if not p.remote and p.area == p.country and p.country]
    if bare and any(p.remote and not p.country for p in out):
        c = bare[0].country
        out = [Place("Remote", f"Remote ({c})", c, True) if p.remote and not p.country else p
               for p in out if p is not bare[0]]
    if remote == "remote" and not any(p.remote for p in out):
        out.insert(0, Place("Remote", "Remote", next((p.country for p in out if p.country), ""), True))
    return out or [Place("Unknown", "Location not listed")]


# ---------------------------------------------------------------- stored index

def refresh(conn: sqlite3.Connection) -> int:
    """(Re)classify jobs whose location changed since last time. Cheap when nothing changed."""
    rows = conn.execute(
        """SELECT j.id, j.location, j.remote FROM jobs j
           LEFT JOIN (SELECT job_id, MIN(src) src FROM job_places GROUP BY job_id) p ON p.job_id = j.id
           WHERE p.src IS NULL OR p.src != COALESCE(j.location, '') || '|' || COALESCE(j.remote, '')""").fetchall()
    with conn:
        for r in rows:
            src = f"{r['location'] or ''}|{r['remote'] or ''}"
            conn.execute("DELETE FROM job_places WHERE job_id = ?", (r["id"],))
            conn.executemany(
                "INSERT INTO job_places (job_id, region, area, country, remote, src) VALUES (?, ?, ?, ?, ?, ?)",
                [(r["id"], p.region, p.area, p.country, int(p.remote), src) for p in classify(r["location"], r["remote"])])
    return len(rows)


def facets(conn: sqlite3.Connection, where: str = "j.status NOT IN ('posting_closed', 'rejected')",
           params: tuple | list = ()) -> list[tuple[str, int, list[tuple[str, int]]]]:
    """[(region, jobs, [(area, jobs), ...]), ...] for the jobs matching `where` (on jobs j, matches m,
    qualifications q), in a fixed region order, areas busiest first."""
    base = f"""FROM job_places p JOIN jobs j ON j.id = p.job_id LEFT JOIN matches m ON m.job_id = j.id
               LEFT JOIN qualifications q ON q.job_id = j.id WHERE {where}"""
    rows = conn.execute(f"SELECT p.region, p.area, COUNT(DISTINCT p.job_id) n {base} GROUP BY p.region, p.area",
                        params).fetchall()
    totals = dict(conn.execute(f"SELECT p.region, COUNT(DISTINCT p.job_id) {base} GROUP BY p.region", params).fetchall())
    by: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        by.setdefault(r["region"], []).append((r["area"], r["n"]))
    order = {r: i for i, r in enumerate(REGION_ORDER)}
    return [(reg, totals.get(reg, 0), sorted(areas, key=lambda a: (-a[1], a[0])))
            for reg, areas in sorted(by.items(), key=lambda kv: order.get(kv[0], 99))]


def sql_filter(region: str, area: str, near: str = "") -> tuple[str, list]:
    """A WHERE fragment (on jobs aliased j) for a region/area/free-text place filter."""
    conds, params = [], []
    if region:
        conds.append("p.region = ?")
        params.append(region)
    if area:
        conds.append("p.area = ?")
        params.append(area)
    if near:
        conds.append("(p.area LIKE ? OR p.country LIKE ? OR p.src LIKE ?)")
        params += [f"%{near}%"] * 3
    if not conds:
        return "", []
    return f"EXISTS (SELECT 1 FROM job_places p WHERE p.job_id = j.id AND {' AND '.join(conds)})", params
