#!/usr/bin/env python3
"""YC Frontier Monitor — fetch the YC directory, track changes, build the site.

Usage:
  python3 scripts/yc.py update                 fetch -> diff -> enrich -> build
  python3 scripts/yc.py update --no-founders   skip the founder-count page reads
  python3 scripts/yc.py enrich --dry-run       LLM traction extraction on a sample,
                                               printing quality + cost (writes nothing)
  python3 scripts/yc.py enrich                 run the hash-gated traction pass + build
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
import enrich_text
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

    # Traction extraction (SPEC 002 §5): key-gated, hash-gated, capped. Runs on
    # the manual `update` only — never on the daily `auto` tick (§4a sets
    # no_enrich there), and a clean no-op without a key.
    if cfg.get("text_enrichment", True) and not getattr(args, "no_enrich", False):
        api_key = enrich_text.load_api_key()
        if api_key:
            enrich_text.enrich_traction(
                all_companies(state), api_key,
                model=cfg.get("enrich_model", enrich_text.DEFAULT_MODEL),
                max_companies=cfg.get("max_companies_per_run", 40),
                workers=cfg.get("enrich_workers", 6))
        else:
            print("No ANTHROPIC_API_KEY — traction extraction skipped.")

    store.save_state(state)
    store.append_changelog(entry)
    sitebuild.build()

    if entry["initial"]:
        total = sum(b["total"] for b in entry["batches"].values())
        print("\nInitial import: %d companies." % total)
    else:
        added = sum(len(b["added"]) for b in entry["batches"].values())
        removed = sum(len(b["removed"]) for b in entry["batches"].values())
        changed = sum(len(b.get("changed", [])) for b in entry["batches"].values())
        print("\nDone: +%d new / -%d delisted / ~%d field changes since last update."
              % (added, removed, changed))
        for b in entry["batches"].values():
            for c in b["added"]:
                print("  + [%s] %s — %s" % (b["display"], c["name"], c["one_liner"][:70]))
            per_company = {}
            for r in b.get("changed", []):
                per_company.setdefault((r["slug"], r["name"]), []).append(r["field"])
            for (_slug, name), fields in per_company.items():
                print("  ~ [%s] %s — %s" % (b["display"], name, ", ".join(fields)))
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
    args.no_enrich = True  # SPEC 002 §4a: enrichment never runs on the daily tick
    cmd_update(args)


def _dry_run_sample(companies, n):
    """A representative ~n for cost/quality review: about a third drawn from
    companies the regex already flags (so real extractions show up), the rest an
    even spread across the dataset (so the no-invention case is exercised too).
    Deterministic."""
    desc = sorted((c for c in companies if (c.get("long_description") or "").strip()),
                  key=lambda c: c["slug"])
    flagged = [c for c in desc if c.get("revenue_mention") or c.get("funding_mention")]
    chosen, seen = [], set()
    for c in flagged[:max(1, n // 3)]:
        chosen.append(c)
        seen.add(c["slug"])
    rest = [c for c in desc if c["slug"] not in seen]
    need = n - len(chosen)
    if need > 0 and rest:
        for c in rest[:: max(1, len(rest) // need)]:
            if len(chosen) >= n:
                break
            chosen.append(c)
    return chosen[:n]


def cmd_enrich(args):
    """Traction extraction (SPEC 002 §5). --dry-run writes nothing and reports
    quality + cost on a sample; without it, runs the hash-gated pass and saves."""
    cfg = store.load_json(store.CONFIG_PATH, {})
    api_key = enrich_text.load_api_key()
    if not api_key:
        print("No ANTHROPIC_API_KEY (env or .env) — traction extraction is a no-op.")
        return
    model = cfg.get("enrich_model", enrich_text.DEFAULT_MODEL)
    state = store.load_state()
    if not state["batches"]:
        sys.exit("No data yet — run: python3 scripts/yc.py update")
    companies = all_companies(state)

    if args.dry_run:
        sample = _dry_run_sample(companies, args.limit or 20)
        print("DRY RUN — %d companies via %s. Nothing will be written.\n"
              % (len(sample), model))
        in_tok = out_tok = hits = errs = 0
        for i, c in enumerate(sample, 1):
            traction, usage, err = enrich_text.extract_one(api_key, model, c)
            in_tok += usage.get("input_tokens", 0)
            out_tok += usage.get("output_tokens", 0)
            print("[%2d] %s  (%s)" % (i, c["name"], c.get("batch", "")))
            if err:
                errs += 1
                print("     ! %s" % err)
                print()
                continue
            nonempty = [(k, traction[k]) for k in enrich_text.TRACTION_KEYS if traction[k]]
            if nonempty:
                hits += 1
                for k, v in nonempty:
                    print("     %-16s %s" % (k + ":", v))
            else:
                print("     (no traction stated)")
            if c.get("revenue_mention") or c.get("funding_mention"):
                print("     · regex previously caught: rev=%r fund=%r"
                      % (c.get("revenue_mention", ""), c.get("funding_mention", "")))
            print()
        cost = enrich_text.estimate_cost(model, in_tok, out_tok)
        eligible = sum(1 for c in companies if enrich_text.needs_extraction(c))
        print("-" * 64)
        print("Reviewed %d companies: %d with traction, %d empty, %d errors."
              % (len(sample), hits, len(sample) - hits - errs, errs))
        print("Tokens: %d in / %d out.  Estimated cost: $%.4f (%s)."
              % (in_tok, out_tok, cost, model))
        if in_tok:
            per = cost / len(sample)
            print("Per company ~$%.5f  ->  full run of %d eligible ~$%.2f."
                  % (per, eligible, per * eligible))
        print("\nNothing written. Review the above, then run the full pass when ready.")
        return

    stats = enrich_text.enrich_traction(
        companies, api_key, model=model,
        max_companies=args.limit or cfg.get("max_companies_per_run", 40),
        workers=cfg.get("enrich_workers", 6))
    store.save_state(state)
    sitebuild.build()
    print("Traction: %d extracted (%d disclosed), %d errors. Tokens %d/%d, est $%.4f."
          % (stats["extracted"], stats["with_traction"], stats["errors"],
             stats["in_tokens"], stats["out_tokens"], stats.get("cost", 0.0)))
    print("Site rebuilt -> site/index.html")


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
    comps = all_companies(state)
    extracted = [c for c in comps if c.get("traction")]
    disclosed = sum(1 for c in extracted
                    if any(c["traction"].get(k) for k in enrich_text.TRACTION_KEYS))
    print("Traction: %d/%d companies extracted, %d disclose traction."
          % (len(extracted), len(comps), disclosed))
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
    p_update.add_argument("--no-enrich", action="store_true",
                          help="skip LLM traction extraction")
    p_update.set_defaults(fn=cmd_update)
    p_auto = sub.add_parser("auto", help="update only if due (daily scheduler hook)")
    p_auto.add_argument("--no-founders", action="store_true",
                        help="skip founder-count page fetches")
    p_auto.set_defaults(fn=cmd_auto)
    p_enrich = sub.add_parser("enrich", help="LLM traction extraction (SPEC 002 Phase 1)")
    p_enrich.add_argument("--dry-run", action="store_true",
                          help="extract a sample, print quality + cost, write nothing")
    p_enrich.add_argument("--limit", type=int, default=None,
                          help="cap companies (dry-run sample size; default 20)")
    p_enrich.set_defaults(fn=cmd_enrich)
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
