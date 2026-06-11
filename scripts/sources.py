"""Where company data comes from.

Primary: the live Algolia index behind ycombinator.com/companies. The public
search-only key is shipped inside the page (window.AlgoliaOpts) and rotates,
so it is auto-discovered on every run — same technique as yc_scraper.py.

Fallback: the yc-oss GitHub Pages mirror (github.com/yc-oss/api), rebuilt
daily from the same index, so it can lag up to ~24h — fine for a monitor.

Every record is reduced to the schema in normalize() so the rest of the
pipeline never cares which source produced it.
"""

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode

TIMEOUT = 30
ALGOLIA_INDEX = "YCCompany_production"
MIRROR_BATCH_URL = "https://yc-oss.github.io/api/batches/{slug}.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}


class SourceError(RuntimeError):
    pass


def http_text(url, payload=None, headers=None):
    """GET (or POST when payload is given) and return the response body."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def discover_algolia_creds():
    """Pull the current public Algolia app id + search key off the YC site."""
    txt = http_text("https://www.ycombinator.com/companies")
    m = re.search(r'window\.AlgoliaOpts\s*=\s*(\{.*?\})\s*;', txt, re.S)
    if m:
        try:
            opts = json.loads(m.group(1))
            if opts.get("app") and opts.get("key"):
                return opts["app"], opts["key"]
        except ValueError:
            pass
    raise SourceError("Algolia creds not found on ycombinator.com/companies "
                      "(page layout changed?)")


def fetch_algolia(creds, batch_display):
    """All hits for one batch from the live index."""
    app_id, key = creds
    url = "https://%s-dsn.algolia.net/1/indexes/%s/query" % (app_id.lower(), ALGOLIA_INDEX)
    headers = {"X-Algolia-Application-Id": app_id, "X-Algolia-API-Key": key,
               "Content-Type": "application/json"}
    hits, page, pages = [], 0, 1
    while page < pages:
        params = urlencode({
            "hitsPerPage": 1000,
            "page": page,
            "facetFilters": json.dumps([["batch:%s" % batch_display]]),
        })
        data = json.loads(http_text(url, payload={"params": params}, headers=headers))
        hits += data.get("hits", [])
        pages = data.get("nbPages", 1)
        page += 1
    return hits


def fetch_mirror(slug):
    """One batch from the daily mirror; a 404 just means 'no companies yet'."""
    try:
        return json.loads(http_text(MIRROR_BATCH_URL.format(slug=slug)))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def fetch_batch(slug, display, creds):
    """Live index first, mirror as fallback. Returns (companies, source_name)."""
    if creds is not None:
        try:
            return [normalize(h) for h in fetch_algolia(creds, display)], "algolia"
        except Exception as e:  # any live-index failure means "use the mirror"
            print("  ! algolia failed for %s (%s); trying mirror" % (display, e))
    return [normalize(r) for r in fetch_mirror(slug)], "yc-oss mirror"


# --- revenue mentions --------------------------------------------------------
# Ported from yc_scraper.py. A sentence is any run of chars up to a sentence-
# ending period; a period inside a number ("6.5x", "$1.2M") is followed by a
# digit, so `\.(?=\d)` keeps it from ending the sentence early.
_NOTDOT = r'(?:[^.]|\.(?=\d))'
_REVENUE = re.compile(
    _NOTDOT + r'*\$\s?[\d.,]+\s?(?:[KMB]|thousand|million|billion)?\+?' + _NOTDOT + r'*?'
    r'(?:ARR|MRR|revenue|run[\s-]?rate|GMV|bookings)' + _NOTDOT + r'*\.',
    re.I)


def extract_revenue(text):
    """First '$ figure ... ARR/MRR/revenue' sentence in a description, if any."""
    m = _REVENUE.search(text or "")
    if not m:
        return ""
    snippet = re.sub(r'\s+', ' ', m.group(0)).strip()
    i = snippet.find("$")  # drop any lead-in clause before the figure
    return snippet[i:].strip(" .,") if i > 0 else snippet


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize(raw):
    """Reduce a raw Algolia/mirror record to the one schema the app uses."""
    slug = raw.get("slug") or ""
    desc = raw.get("long_description") or raw.get("description") or ""
    sub = raw.get("subindustry") or ""
    return {
        "slug": slug,
        "name": raw.get("name") or slug,
        "batch": raw.get("batch") or "",
        "one_liner": raw.get("one_liner") or "",
        "long_description": desc,
        "website": raw.get("website") or "",
        "yc_url": raw.get("url") or "https://www.ycombinator.com/companies/%s" % slug,
        "industry": raw.get("industry") or "",
        "subindustry": sub.split(" -> ")[-1] if sub else "",
        "industries": raw.get("industries") or [],
        "tags": raw.get("tags") or [],
        "team_size": _int_or_none(raw.get("team_size")),
        "founder_count": None,  # filled by enrich.py, preserved across runs
        "location": raw.get("all_locations") or "",
        "regions": raw.get("regions") or [],
        "status": raw.get("status") or "",
        "stage": raw.get("stage") or "",
        "is_hiring": bool(raw.get("isHiring")),
        "top_company": bool(raw.get("top_company")),
        "launched_at": _int_or_none(raw.get("launched_at")),
        "revenue_mention": extract_revenue(desc),
    }
