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
                   SITE_DIR, SNAPSHOT_DIR, TOPICS_PATH, load_json, load_state)

EMPTY_WATCHLIST = {"updated_at": None, "summary": [], "methodology": "",
                   "themes": [], "picks": []}

# verdict semantics (SPEC §5): build = we build in this space ourselves,
# copy = adapt the model for the China market, partner = integration/channel
# candidate, ignore = reviewed, not relevant. Absent verdict = undecided.
VERDICT_ACTIONS = ("build", "copy", "partner", "ignore")

# review outcomes (SPEC §7) — human judgments recorded in picks[].reviews
REVIEW_OUTCOMES = ("thriving", "growing", "flat", "pivoted", "dead", "unclear")


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
    """Fail the build loudly on schema violations; absent keys are fine.

    Checks the Phase-1 keys (SPEC §5): picked_at / verdict.decided_at must be
    ISO dates, verdict.action must be one of VERDICT_ACTIONS.
    """
    problems = []
    for p in wl.get("picks", []):
        where = "pick %r" % p.get("slug", "?")
        if p.get("picked_at") is not None and _bad_date(p["picked_at"]):
            problems.append("%s: picked_at %r is not an ISO date" % (where, p["picked_at"]))
        v = p.get("verdict")
        if v is None:
            continue
        if not isinstance(v, dict):
            problems.append("%s: verdict must be an object" % where)
            continue
        if v.get("action") not in VERDICT_ACTIONS:
            problems.append("%s: verdict.action %r must be one of %s"
                            % (where, v.get("action"), "/".join(VERDICT_ACTIONS)))
        if v.get("decided_at") is not None and _bad_date(v["decided_at"]):
            problems.append("%s: verdict.decided_at %r is not an ISO date"
                            % (where, v["decided_at"]))
    for p in wl.get("picks", []):
        where = "pick %r" % p.get("slug", "?")
        for i, r in enumerate(p.get("reviews") or []):
            at = "%s.reviews[%d]" % (where, i)
            if not isinstance(r, dict):
                problems.append("%s must be an object" % at)
                continue
            if r.get("outcome") not in REVIEW_OUTCOMES:
                problems.append("%s: outcome %r must be one of %s"
                                % (at, r.get("outcome"), "/".join(REVIEW_OUTCOMES)))
            if not r.get("date") or _bad_date(r["date"]):
                problems.append("%s: date %r is not an ISO date" % (at, r.get("date")))
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


# ----------------------------------------------------------- review cadence
def _review_anchor(pick):
    """The date the review clock runs from: max(picked_at, latest review)."""
    dates = [pick.get("picked_at")] + [r.get("date") for r in pick.get("reviews") or []]
    dates = [d[:10] for d in dates if d]
    return max(dates) if dates else None  # ISO date strings compare correctly


def _snapshot_team_size(slug, batch_slug, picked_at):
    """team_size from the snapshot nearest to picked_at -> (value, snap_date).

    Snapshots are per-run directories named by run timestamp; we walk them in
    order of date distance and return the first one containing the company.
    (None, None) when nothing matches — evidence is best-effort by design.
    """
    if not SNAPSHOT_DIR.exists():
        return None, None
    target = date.fromisoformat(picked_at[:10])
    dirs = []
    for d in SNAPSHOT_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            dirs.append((abs((date.fromisoformat(d.name[:10]) - target).days), d))
        except ValueError:
            continue
    for _, d in sorted(dirs, key=lambda x: x[0]):
        names = [d / ("%s.json" % batch_slug)] if batch_slug else []
        names += sorted(p for p in d.glob("*.json") if p not in names)
        for path in names:
            if not path.exists():
                continue
            try:
                companies = load_json(path, [])
            except ValueError:
                continue
            for c in companies:
                if c.get("slug") == slug:
                    return c.get("team_size"), d.name[:10]
    return None, None


def compute_due_reviews(wl, state, interval_days, today=None):
    """Picks whose review is overdue, each with dataset-only evidence
    (SPEC §7): team_size then vs now, one-liner changes since picked_at,
    delisted flag. Read-only — never touches the watchlist."""
    today = today or date.today()
    current = {c["slug"]: (bslug, c)
               for bslug, b in state.get("batches", {}).items()
               for c in b["companies"]}
    changelog = load_json(CHANGELOG_PATH, [])
    due = []
    for p in wl.get("picks", []):
        anchor = _review_anchor(p)
        if not anchor:
            continue
        days = (today - date.fromisoformat(anchor)).days
        if days < interval_days:
            continue
        slug = p["slug"]
        batch_slug, company = current.get(slug, (None, None))
        picked_at = (p.get("picked_at") or anchor)[:10]
        one_liner_changes = [
            {"date": entry["run_at"][:10], "old": r["old"], "new": r["new"]}
            for entry in changelog if entry["run_at"][:10] >= picked_at
            for b in entry.get("batches", {}).values()
            for r in b.get("changed", [])
            if r["slug"] == slug and r["field"] == "one_liner"
        ]
        team_then, team_then_at = _snapshot_team_size(slug, batch_slug, picked_at)
        due.append({
            "slug": slug,
            "anchor": anchor,
            "days": days,
            "evidence": {
                "team_then": team_then,
                "team_then_at": team_then_at,
                "team_now": company.get("team_size") if company else None,
                "one_liner_changes": one_liner_changes,
                "delisted": company is None,
            },
        })
    due.sort(key=lambda d: -d["days"])
    return due


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
    interval = cfg.get("review_interval_days", 180)
    rules = load_topic_rules()
    return {
        "site_title": cfg.get("site_title", "YC Monitor"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_at": state.get("updated_at"),
        "next_update": next_pull.isoformat() if next_pull else None,
        "review_interval_days": interval,
        "due_reviews": compute_due_reviews(wl, state, interval),
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
