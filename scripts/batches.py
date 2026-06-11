"""Which batches the monitor tracks.

YC runs four batches a year (Winter, Spring, Summer, Fall — roughly Jan, Apr,
Jul, Oct starts). We track everything from `start_batch` in config.json up to
the batch starting within `lookahead_months` of today. YC lists companies for
upcoming batches months early (Fall 2026 entries existed in June 2026), so the
generous lookahead means new batches appear without anyone editing config.
"""

from datetime import date

# season -> nominal start month, in calendar order
SEASONS = [("winter", 1), ("spring", 4), ("summer", 7), ("fall", 10)]
_SEASON_MONTH = dict(SEASONS)


def display_name(slug):
    """'fall-2025' -> 'Fall 2025' (the name the YC directory uses)."""
    season, year = slug.split("-")
    return "%s %s" % (season.capitalize(), year)


def start_month(slug):
    """'fall-2025' -> (2025, 10); used for ordering and the lookahead cutoff."""
    season, year = slug.split("-")
    return int(year), _SEASON_MONTH[season]


def tracked_batches(start_slug, today=None, lookahead_months=6):
    """All batch slugs from start_slug through today + lookahead, in order."""
    today = today or date.today()
    months = today.year * 12 + (today.month - 1) + lookahead_months
    cutoff = (months // 12, months % 12 + 1)

    out = []
    year = start_month(start_slug)[0]
    started = False
    while year <= cutoff[0]:
        for season, month in SEASONS:
            slug = "%s-%d" % (season, year)
            if slug == start_slug:
                started = True
            if started and (year, month) <= cutoff:
                out.append(slug)
        year += 1
    return out
