"""Assemble what the site renders.

site/data.js            window.YC_DATA = {...}; loaded by site/index.html
dist/yc-monitor.html    the whole site in one portable file (css/js/data inlined)

Data ships as a .js file (not fetched JSON) so the site works straight off
file:// with no server.
"""

import json
from datetime import date, datetime, timezone

import automate
import batches as batchmod
from store import (ANALYSIS_PATH, CHANGELOG_PATH, CONFIG_PATH, DIST_DIR,
                   SITE_DIR, load_json, load_state)

EMPTY_WATCHLIST = {"updated_at": None, "summary": [], "methodology": "",
                   "themes": [], "picks": []}


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
    return {
        "site_title": cfg.get("site_title", "YC Monitor"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_at": state.get("updated_at"),
        "next_update": next_pull.isoformat() if next_pull else None,
        "batches": [dict(b, slug=slug) for slug, b in ordered],
        "changelog": load_json(CHANGELOG_PATH, []),
        "watchlist": load_json(ANALYSIS_PATH, EMPTY_WATCHLIST),
    }


def build():
    payload = build_payload()
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
