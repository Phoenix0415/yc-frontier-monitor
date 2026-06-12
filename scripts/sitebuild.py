"""Assemble what the site renders.

site/data.js            window.YC_DATA = {...}; loaded by site/index.html
dist/yc-monitor.html    the whole site in one portable file (css/js/data inlined)

Data ships as a .js file (not fetched JSON) so the site works straight off
file:// with no server.
"""

import json
import re
from datetime import date, datetime, timezone

import automate
import batches as batchmod
from store import (ANALYSIS_PATH, CHANGELOG_PATH, CONFIG_PATH, DIST_DIR,
                   SITE_DIR, TOPICS_PATH, load_json, load_state)

EMPTY_WATCHLIST = {"updated_at": None, "summary": [], "methodology": "",
                   "themes": [], "picks": []}

def _bad_date(value):
    """True unless value parses as an ISO date or datetime."""
    for parse in (date.fromisoformat, datetime.fromisoformat):
        try:
            parse(value)
            return False
        except (TypeError, ValueError):
            pass
    return True


def validate_watchlist(wl):
    """Fail the build loudly on schema violations; absent keys are fine."""
    problems = []
    for p in wl.get("picks", []):
        where = "pick %r" % p.get("slug", "?")
        if p.get("picked_at") is not None and _bad_date(p["picked_at"]):
            problems.append("%s: picked_at %r is not an ISO date" % (where, p["picked_at"]))
    if problems:
        raise SystemExit("analysis/watchlist.json failed validation:\n  "
                         + "\n  ".join(problems))


# ------------------------------------------------------------ topic tagging
# YC's own categories are too coarse to read trends from ("B2B" covers half
# the batch), so analysis/topics.json defines frontier topics as transparent
# keyword rules — deterministic, no LLM calls (owner-approved deviation from
# SPEC §6b's "YC fields only"; the no-LLM constraint stands). A company can
# match several topics, or none.
def load_topic_rules():
    spec = load_json(TOPICS_PATH, {"topics": []})
    rules, problems = [], []
    for topic in spec.get("topics", []):
        if not topic.get("id") or not topic.get("label"):
            problems.append("topic with missing id/label: %r" % topic)
            continue
        compiled = []
        for pattern in topic.get("patterns", []):
            try:
                compiled.append(re.compile(pattern, re.I))
            except re.error as exc:
                problems.append("topic %s: bad pattern %r (%s)"
                                % (topic["id"], pattern, exc))
        rules.append({"id": topic["id"], "label": topic["label"],
                      "patterns": compiled})
    if problems:
        raise SystemExit("analysis/topics.json failed validation:\n  "
                         + "\n  ".join(problems))
    return rules


def classify_topics(company, rules):
    blob = " ".join([
        company.get("name") or "",
        company.get("one_liner") or "",
        company.get("long_description") or "",
        " ".join(company.get("tags") or []),
        company.get("industry") or "",
        company.get("subindustry") or "",
    ])
    return [r["id"] for r in rules
            if any(p.search(blob) for p in r["patterns"])]


def build_payload():
    cfg = load_json(CONFIG_PATH, {})
    state = load_state()
    ordered = sorted(state["batches"].items(),
                     key=lambda kv: batchmod.start_month(kv[0]))
    if state["batches"]:
        due, _ = automate.update_due(state)
        next_pull = date.today() if due else automate.next_due(state)[0]
    else:
        next_pull = None
    wl = load_json(ANALYSIS_PATH, EMPTY_WATCHLIST)
    rules = load_topic_rules()
    return {
        "site_title": cfg.get("site_title", "YC Monitor"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_at": state.get("updated_at"),
        "next_update": next_pull.isoformat() if next_pull else None,
        "topics": [{"id": r["id"], "label": r["label"]} for r in rules],
        # companies are shallow-copied so derived topics never leak back into
        # data/companies.json via the shared state dicts
        "batches": [dict(b, slug=slug,
                         companies=[dict(c, topics=classify_topics(c, rules))
                                    for c in b["companies"]])
                    for slug, b in ordered],
        "changelog": load_json(CHANGELOG_PATH, []),
        "watchlist": wl,
    }


def build():
    payload = build_payload()
    validate_watchlist(payload["watchlist"])
    known = {c["slug"] for b in payload["batches"] for c in b["companies"]}
    for pick in payload["watchlist"].get("picks", []):
        if pick["slug"] not in known:
            print("  ! watchlist pick %r is not in the dataset "
                  "(delisted or typo) — it won't render" % pick["slug"])
    # "</" must never appear verbatim inside a <script> block (a description
    # containing "</script>" would end the tag in the inlined dist build);
    # "<\/" is the same string to the JS parser.
    js = ("window.YC_DATA = "
          + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
          + ";\n")
    (SITE_DIR / "data.js").write_text(js, encoding="utf-8")

    app_js = (SITE_DIR / "app.js").read_text(encoding="utf-8")
    assert "</script" not in app_js.lower(), \
        "app.js must not contain a literal </script sequence (breaks dist inlining)"

    page = (SITE_DIR / "index.html").read_text(encoding="utf-8")
    for marker, inline in [
        ('<link rel="stylesheet" href="styles.css">',
         "<style>\n%s\n</style>" % (SITE_DIR / "styles.css").read_text(encoding="utf-8")),
        ('<script src="data.js"></script>', "<script>\n%s</script>" % js),
        ('<script src="app.js"></script>', "<script>\n%s\n</script>" % app_js),
    ]:
        assert marker in page, "marker missing from index.html: %s" % marker
        page = page.replace(marker, inline)
    DIST_DIR.mkdir(exist_ok=True)
    (DIST_DIR / "yc-monitor.html").write_text(page, encoding="utf-8")
    return payload
