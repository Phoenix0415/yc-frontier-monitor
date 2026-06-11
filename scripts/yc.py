#!/usr/bin/env python3
"""YC Frontier Monitor — fetch the YC directory, track changes, build the site.

Usage:
  python3 scripts/yc.py update                 fetch -> diff -> enrich -> build
  python3 scripts/yc.py update --no-founders   skip the founder-count page reads
  python3 scripts/yc.py auto                   update only if due (daily scheduler
                                               entry point: monthly baseline +
                                               ~1 week after each batch kickoff)
  python3 scripts/yc.py schedule install       create the daily launchd check
  python3 scripts/yc.py schedule status        is it loaded / when's the next pull
  python3 scripts/yc.py schedule uninstall     remove the launchd agent
  python3 scripts/yc.py build                  rebuild the site from data on disk
                                               (after editing analysis/watchlist.json)
  python3 scripts/yc.py status                 batch counts + what's awaiting review

Python 3.9+, standard library only.
"""

import argparse
import sys
from datetime import datetime, timezone

import automate
import batches as batchmod
import enrich
import sitebuild
import sources
import store


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def all_companies(state):
    return [c for b in state["batches"].values() for c in b["companies"]]


def pending_review(state, watchlist):
    """Companies that appeared after the watchlist was last curated."""
    if not watchlist or not watchlist.get("updated_at"):
        return []
    return [c for c in all_companies(state)
            if c["first_seen"] > watchlist["updated_at"]]


def cmd_update(args):
    cfg = store.load_json(store.CONFIG_PATH, {})
    slugs = batchmod.tracked_batches(cfg.get("start_batch", "fall-2025"),
                                     lookahead_months=cfg.get("lookahead_months", 6))
    print("Tracking: " + ", ".join(batchmod.display_name(s) for s in slugs))

    creds = None
    try:
        creds = sources.discover_algolia_creds()
        print("Algolia key discovered OK")
    except Exception as e:
        print("! %s — using the mirror for everything" % e)

    run_at = now_iso()
    run_id = run_at.replace(":", "-")
    results = {}
    for slug in slugs:
        display = batchmod.display_name(slug)
        try:
            companies, source = sources.fetch_batch(slug, display, creds)
        except Exception as e:
            print("  ! %s: fetch failed (%s) — keeping previous data" % (display, e))
            continue
        companies.sort(key=lambda c: c["name"].lower())
        results[slug] = {"display": display, "source": source, "companies": companies}
        store.save_snapshot(run_id, slug, companies)
        print("  %s: %d companies (%s)" % (display, len(companies), source))

    if not results:
        sys.exit("Every batch fetch failed; dataset left untouched.")

    state = store.load_state()
    entry = store.apply_run(state, results, run_at)

    if cfg.get("founder_enrichment", True) and not args.no_founders:
        enrich.enrich_founders(all_companies(state),
                               workers=cfg.get("founder_workers", 8))

    store.save_state(state)
    store.append_changelog(entry)
    sitebuild.build()

    if entry["initial"]:
        total = sum(b["total"] for b in entry["batches"].values())
        print("\nInitial import: %d companies." % total)
    else:
        added = sum(len(b["added"]) for b in entry["batches"].values())
        removed = sum(len(b["removed"]) for b in entry["batches"].values())
        print("\nDone: +%d new / -%d delisted since last update." % (added, removed))
        for b in entry["batches"].values():
            for c in b["added"]:
                print("  + [%s] %s — %s" % (b["display"], c["name"], c["one_liner"][:70]))
    print("Site rebuilt -> site/index.html (portable copy: dist/yc-monitor.html)")

    pending = pending_review(state, store.load_json(store.ANALYSIS_PATH, None))
    if pending:
        print("%d companies arrived after the last analyst review — see the "
              "Updates tab, or ask Claude to refresh analysis/watchlist.json." % len(pending))


def cmd_auto(args):
    """Daily scheduler entry point: cheap no-op unless an update is due."""
    state = store.load_state()
    due, reason = automate.update_due(state)
    stamp = now_iso()
    if not due:
        nxt = automate.next_due(state)
        print("[%s] auto: not due — %s; next pull %s (%s)" % (stamp, reason, nxt[0], nxt[1]))
        return
    print("[%s] auto: update due — %s" % (stamp, reason))
    cmd_update(args)


def cmd_schedule(args):
    if args.action == "install":
        path = automate.install()
        print("Installed launchd agent %s — daily check at %02d:00."
              % (automate.LABEL, automate.TICK_HOUR))
        print("  plist: %s" % path)
        print("  log:   %s" % automate.LOG_PATH)
        nxt = automate.next_due(store.load_state())
        print("  next real pull: %s (%s)" % nxt)
    elif args.action == "uninstall":
        automate.uninstall()
        print("Removed launchd agent %s." % automate.LABEL)
    else:  # status
        print("launchd agent: %s" % ("loaded" if automate.installed() else "NOT installed"))
        print("  plist: %s%s" % (automate.PLIST_PATH,
                                 "" if automate.PLIST_PATH.exists() else " (missing)"))
        state = store.load_state()
        due, reason = automate.update_due(state)
        if due:
            print("  update due now: %s" % reason)
        else:
            print("  next pull: %s (%s)" % automate.next_due(state))


def cmd_build(_args):
    payload = sitebuild.build()
    total = sum(len(b["companies"]) for b in payload["batches"])
    print("Site rebuilt: %d companies, %d watchlist picks." %
          (total, len(payload["watchlist"].get("picks", []))))


def cmd_status(_args):
    state = store.load_state()
    if not state["batches"]:
        sys.exit("No data yet — run: python3 scripts/yc.py update")
    print("Last update: %s" % state["updated_at"])
    for slug in sorted(state["batches"], key=batchmod.start_month):
        b = state["batches"][slug]
        missing = sum(1 for c in b["companies"] if c.get("founder_count") is None)
        print("  %-12s %4d companies  (%s%s)" %
              (b["display"], len(b["companies"]), b["source"],
               ", %d founder counts missing" % missing if missing else ""))
    wl = store.load_json(store.ANALYSIS_PATH, None)
    if wl and wl.get("updated_at"):
        pending = pending_review(state, wl)
        print("Watchlist: %d picks, reviewed %s — %d companies awaiting review."
              % (len(wl.get("picks", [])), wl["updated_at"][:10], len(pending)))
    else:
        print("Watchlist: not curated yet (analysis/watchlist.json).")
    due, reason = automate.update_due(state)
    auto_state = ("loaded" if automate.installed()
                  else "not installed — python3 scripts/yc.py schedule install")
    timing = ("due now: %s" % reason) if due else ("next pull %s (%s)" % automate.next_due(state))
    print("Auto-update: %s; %s" % (auto_state, timing))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_update = sub.add_parser("update", help="fetch latest data, diff, rebuild site")
    p_update.add_argument("--no-founders", action="store_true",
                          help="skip founder-count page fetches")
    p_update.set_defaults(fn=cmd_update)
    p_auto = sub.add_parser("auto", help="update only if due (daily scheduler hook)")
    p_auto.add_argument("--no-founders", action="store_true",
                        help="skip founder-count page fetches")
    p_auto.set_defaults(fn=cmd_auto)
    p_sched = sub.add_parser("schedule", help="manage the automatic update agent")
    p_sched.add_argument("action", choices=["install", "uninstall", "status"])
    p_sched.set_defaults(fn=cmd_schedule)
    sub.add_parser("build", help="rebuild the site from data on disk") \
       .set_defaults(fn=cmd_build)
    sub.add_parser("status", help="show batch counts and review backlog") \
       .set_defaults(fn=cmd_status)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
