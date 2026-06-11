"""Founder counts.

Not in the search index, so we read each company's YC page and pull the
founders array out of the Inertia `data-page` payload — same approach as
yc_scraper.py. Incremental by design: only companies whose founder_count is
still null get fetched, so routine updates only touch new arrivals.
"""

import html as htmllib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from sources import http_text


def _deep_find(obj, key):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out += _deep_find(v, key)
    elif isinstance(obj, list):
        for item in obj:
            out += _deep_find(item, key)
    return out


def founder_count(slug):
    """Founder count from the company page, or None if it can't be read.

    None (rather than 0) on failure, so the next update retries it."""
    try:
        page = http_text("https://www.ycombinator.com/companies/%s" % slug)
    except Exception:
        return None
    m = re.search(r'data-page="(.*?)"\s*>', page, re.S)
    if not m:
        return None
    try:
        data = json.loads(htmllib.unescape(m.group(1)))
    except ValueError:
        return None
    lists = [v for v in _deep_find(data, "founders")
             if isinstance(v, list) and v and isinstance(v[0], dict)]
    return len(max(lists, key=len)) if lists else None


def enrich_founders(companies, workers=8, log=print):
    """Fill founder_count in place for every company missing it."""
    todo = [c for c in companies if c.get("founder_count") is None and c.get("slug")]
    if not todo:
        log("Founder counts already complete.")
        return 0
    log("Reading founder counts for %d companies (%d workers)..." % (len(todo), workers))
    filled = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(founder_count, c["slug"]): c for c in todo}
        for done, fut in enumerate(as_completed(futures), 1):
            n = fut.result()
            if n is not None:
                futures[fut]["founder_count"] = n
                filled += 1
            if done % 50 == 0 or done == len(todo):
                log("  %d/%d" % (done, len(todo)))
    return filled
