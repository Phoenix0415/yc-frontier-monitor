"""Dataset state on disk.

data/companies.json   canonical dataset the site is built from
data/changelog.json   what each update run added/removed
data/snapshots/       normalized per-batch fetches, one directory per run

Failure-safe: a batch that can't be fetched keeps its previous data, and all
writes are atomic (tmp file + rename), so a crashed run can't corrupt state.
Fields that can't be refetched cheaply (first_seen, founder_count) are carried
over from the previous state on every merge.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
COMPANIES_PATH = DATA_DIR / "companies.json"
CHANGELOG_PATH = DATA_DIR / "changelog.json"
ANALYSIS_PATH = ROOT / "analysis" / "watchlist.json"
TOPICS_PATH = ROOT / "analysis" / "topics.json"
SITE_DIR = ROOT / "site"
DIST_DIR = ROOT / "dist"
CONFIG_PATH = ROOT / "config.json"


def load_json(path, default):
    if not Path(path).exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# fields whose changes are tracked between runs (SPEC §6a); these are the
# normalized schema's names (see sources.normalize)
WATCHED_FIELDS = ("one_liner", "long_description", "team_size", "website", "status")


def _comparable(value):
    """Whitespace-insensitive view of a value, so trivial edits don't count
    as changes. The changelog still stores the raw values."""
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def load_state():
    return load_json(COMPANIES_PATH, {"updated_at": None, "batches": {}})


def save_state(state):
    atomic_write_json(COMPANIES_PATH, state)


def save_snapshot(run_id, slug, companies):
    atomic_write_json(SNAPSHOT_DIR / run_id / ("%s.json" % slug), companies)


def apply_run(state, results, run_at):
    """Merge one update run into the dataset, in place.

    results: {batch_slug: {"display", "source", "companies"}} — successfully
    fetched batches only; anything absent keeps its previous data untouched.
    Returns the changelog entry describing the run.
    """
    initial = not state["batches"]
    entry = {"run_at": run_at, "initial": initial, "batches": {}}

    for slug, res in results.items():
        prev = state["batches"].get(slug, {})
        prev_by_slug = {c["slug"]: c for c in prev.get("companies", [])}

        changed = []
        for c in res["companies"]:
            old = prev_by_slug.get(c["slug"])
            c["first_seen"] = old["first_seen"] if old else run_at
            c["new_in_last_update"] = old is None and not initial
            if old and c.get("founder_count") is None:
                c["founder_count"] = old.get("founder_count")
            if old:
                for field in WATCHED_FIELDS:
                    if _comparable(old.get(field)) != _comparable(c.get(field)):
                        changed.append({"slug": c["slug"], "name": c["name"],
                                        "field": field,
                                        "old": old.get(field), "new": c.get(field)})

        added = [c for c in res["companies"] if c["slug"] not in prev_by_slug]
        fetched_slugs = {c["slug"] for c in res["companies"]}
        removed = [c for s, c in prev_by_slug.items() if s not in fetched_slugs]

        state["batches"][slug] = {
            "display": res["display"],
            "source": res["source"],
            "fetched_at": run_at,
            "companies": res["companies"],
        }
        entry["batches"][slug] = {
            "display": res["display"],
            "source": res["source"],
            "total": len(res["companies"]),
            # the initial import would list every company as "added" — the
            # totals carry that story, so keep the entry lean
            "added": [] if initial else [{"slug": c["slug"], "name": c["name"],
                                          "one_liner": c["one_liner"]} for c in added],
            "removed": [{"slug": c["slug"], "name": c["name"]} for c in removed],
            "changed": changed,
        }

    state["updated_at"] = run_at
    return entry


def append_changelog(entry):
    log = load_json(CHANGELOG_PATH, [])
    log.append(entry)
    atomic_write_json(CHANGELOG_PATH, log)
