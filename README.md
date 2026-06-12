# YC Frontier Monitor

Tracks Y Combinator's newest batches (Fall 2025 onward), keeps a dataset up to
date, and renders a bilingual (EN/中文) report + browser: which companies are
worth watching, why, and what's new since you last checked.

Not affiliated with Y Combinator. Company data comes from YC's public company
directory; the watchlist is editorial opinion, not investment advice. MIT
licensed (code) — see LICENSE.

## Quick start

```bash
python3 scripts/yc.py update    # fetch latest → diff → rebuild the site
open site/index.html            # browse the report (works straight off file://)
```

`dist/yc-monitor.html` is the same site as a single self-contained file —
share it, email it, drop it anywhere.

## Commands

| command | what it does |
| --- | --- |
| `python3 scripts/yc.py update` | Fetch all tracked batches, diff against the previous state, fill founder counts for new arrivals, rebuild the site. |
| `python3 scripts/yc.py update --no-founders` | Same, but skip the founder-count page reads (faster). |
| `python3 scripts/yc.py auto` | Update only if one is due (see cadence below). This is what the scheduler calls — cheap no-op otherwise. |
| `python3 scripts/yc.py schedule install` | Install the macOS launchd agent that runs `auto` daily at 10:00. `status` / `uninstall` manage it. |
| `python3 scripts/yc.py build` | Rebuild the site from data already on disk — run after editing `analysis/watchlist.json`. |
| `python3 scripts/yc.py status` | Batch counts, review backlog, and when the next automatic pull will happen. |

## Publishing (GitHub Pages — no domain needed)

The repo ships a workflow (`.github/workflows/monitor.yml`) that turns GitHub
into both the scheduler and the host:

1. Create a public repo on github.com and push this project to it.
2. Repo **Settings → Pages → Source: "GitHub Actions"** (one click, one time).
3. Run the **Monitor & deploy** workflow once from the Actions tab (or just
   push) — the site appears at `https://<username>.github.io/<repo>/`.

After that it maintains itself: a daily Action tick runs `yc.py auto` (same
cadence policy as the local agent — monthly baseline + ~1 week after each
batch kickoff), commits refreshed `data/*.json` back to the repo, rebuilds,
and redeploys. `data/companies.json` and `data/changelog.json` are tracked in
git precisely so the cloud runs can diff against the previous state.

If the Action is doing the updating, retire the local launchd agent
(`python3 scripts/yc.py schedule uninstall`) and `git pull` before editing the
watchlist locally — two writers just create noise. A custom domain can be
added later in the Pages settings; until then the github.io URL is free.

## Automatic updates

`schedule install` sets up a launchd agent (`com.yc-monitor.auto`) that ticks
daily and runs `yc.py auto`, which only really fetches when due:

- **monthly baseline** — whenever the data is older than 31 days;
- **batch-kickoff boost** — once about a week after each new batch starts
  (batches kick off the first week of Jan / Apr / Jul / Oct), right when the
  new batch's roster starts filling the directory.

The kickoff dates are nominal, not scraped — YC publishes no machine-readable
calendar — and the monthly baseline catches anything the boost mistimes.
Cadence policy lives in `scripts/automate.py`; the launchd agent is a dumb
daily tick, so a Mac asleep at 10:00 just runs it on wake. Activity is logged
to `data/auto.log`; the site's Updates tab shows the next scheduled pull.

If you move this project folder, re-run `schedule install` (the plist embeds
absolute paths).

## How it stays current

- **Batches roll forward automatically.** Everything from `start_batch`
  (config.json) through today + `lookahead_months` is tracked. YC lists
  companies for upcoming batches months early (Fall 2026 entries existed in
  June 2026), so new batches show up without config edits.
- **Updates are diffs.** New companies get a `NEW` badge and a changelog
  entry; delisted ones are recorded too. The Updates tab shows the history.
- **Fetch failures never destroy state.** A batch that can't be fetched keeps
  its previous data; all writes are atomic.

## Data sources

1. **Live Algolia index** behind ycombinator.com/companies. Its public search
   key rotates, so `scripts/sources.py` re-discovers it from the page
   (`window.AlgoliaOpts`) on every run.
2. **Fallback:** the [yc-oss mirror](https://github.com/yc-oss/api), rebuilt
   daily from the same index (≤24 h stale).

If a fetch breaks, it's almost always the key-discovery regex: check what
`window.AlgoliaOpts` looks like on the YC companies page now.

## The analyst layer

`analysis/watchlist.json` is the curated half of the report: executive
summary, themes, and picks (`why` watch / what's worth `learn`ing from each).
The pipeline never writes to it. After an update flags new arrivals
("awaiting analyst review" on the Updates tab), refresh it — typically by
asking Claude to review the newcomers — then run `python3 scripts/yc.py build`.

The site has an EN/中文 toggle (top right, remembered per browser). All
editorial fields in the watchlist are bilingual `{"en": ..., "zh": ...}`
objects — keep both languages when editing. Company-provided text
(one-liners, descriptions) stays in its original English by design.

## Layout

```
config.json              start batch, lookahead, options
scripts/
  yc.py                  CLI entry (update / auto / schedule / build / status)
  batches.py             which batches are tracked (auto rolls forward)
  automate.py            update cadence policy + the launchd daily tick
  sources.py             Algolia + mirror fetch, one normalized schema
  enrich.py              founder counts from company pages (incremental)
  store.py               canonical dataset, snapshots, changelog (atomic writes)
  sitebuild.py           bundles data + analysis into the site
data/
  companies.json         canonical dataset (never hand-edit)
  changelog.json         per-run adds/removals
  snapshots/<run>/       raw per-batch fetches, for archaeology
analysis/
  watchlist.json         curated picks + narrative (the human/Claude layer)
site/
  index.html             the monitor (Report / Companies / Updates tabs)
  styles.css, app.js     plain CSS/JS, no build step
  data.js                generated — never hand-edit
dist/
  yc-monitor.html        the whole site in one portable file (generated)
```

## Notes

- Data is whatever YC lists publicly, i.e. launched companies — young batches
  keep growing for months. That's the point of the monitor.
- Python 3.9+ standard library only; the site is no-build vanilla HTML/CSS/JS.
- `yc_scraper.py` + `yc_Fall2025.xlsx` predate the monitor (one-off Excel
  export of a single batch). Still works; the monitor supersedes it.
