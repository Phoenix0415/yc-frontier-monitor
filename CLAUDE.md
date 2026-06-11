# YC Frontier Monitor — working notes

Long-term project: monitor new YC batches (Fall 2025 →) and maintain a curated
"companies to watch" report. The owner cares about clean, maintainable code,
spotting frontier-startup trends, and one-click access to each company's site.

## Commands

- `python3 scripts/yc.py update` — fetch → diff → founder enrichment → site build
- `python3 scripts/yc.py auto` — update only if due; called daily by launchd
  (cadence: monthly baseline + ~1 week after each batch kickoff; policy in
  `scripts/automate.py`, agent `com.yc-monitor.auto`, log `data/auto.log`)
- `python3 scripts/yc.py schedule install|status|uninstall` — manage the agent
- `python3 scripts/yc.py build` — rebuild site after editing `analysis/`
- `python3 scripts/yc.py status` — counts, review backlog, next scheduled pull

Data updates are automated; the watchlist refresh (step below) is not — after
an automatic pull lands new companies, the owner will ask for a review.

## The recurring task: refresh the analysis

1. Run `update`. `status` prints how many companies arrived since the last
   review (they're also on the site's Updates tab).
2. Read the newcomers in `data/companies.json` (filter
   `first_seen > watchlist.updated_at`); judge against the existing themes.
3. Edit `analysis/watchlist.json` — schema:
   `summary[]` (exec-summary paragraphs), `methodology` (one para),
   `themes[] {id, title, narrative}`,
   `picks[] {slug, theme, why, learn, signals, picked_at, verdict?}`.
   The site is bilingual (EN/中文 toggle): every editorial field is an
   `{"en": ..., "zh": ...}` object (`signals` is `{"en": [...], "zh": [...]}`).
   Always write both languages; a plain string is a legal fallback shown in
   both. Company-provided text (one-liners, descriptions) stays English.
   Every `pick.slug` must exist in the dataset; `theme` must match a theme id.
   New picks get `picked_at` (ISO date, the day they enter the watchlist).
   Refresh stats quoted in narratives if the cohort shifted.
   Set `updated_at` to now (UTC ISO) — it drives the "awaiting review" queue.

## Verdicts (decision layer, SPEC §5 — shipped)

Each pick may carry a decision; absent = rendered as "undecided" (grey badge):

```json
"verdict": {
  "action": "build | copy | partner | ignore",
  "note": {"en": "…", "zh": "…"},
  "decided_at": "2026-06-11"
}
```

- Semantics: `build` we build in this space · `copy` adapt for the China
  market · `partner` integration/channel candidate · `ignore` reviewed, not
  relevant. Verdicts are the OWNER's strategy calls — never invent one; only
  record what Phoenix decides. `note` is bilingual like other editorial text.
- `sitebuild.validate_watchlist()` enforces the action enum and ISO dates and
  fails the build loudly; it runs on every `build`/`update`.
- Badges render on Report pick cards and on picked companies in the Companies
  tab (zh labels: 自建/复制/合作/忽略/未定); the note shows on pick cards as
  "Decision:"/"决策：". `status` prints the verdict breakdown.
- Verdict-filter on the Companies tab was deliberately skipped (P1 in spec).
4. `python3 scripts/yc.py build`, then sanity-check `site/index.html`.

## Conventions

- Python 3.9 stdlib only — no third-party packages anywhere.
- Site is no-build vanilla HTML/CSS/JS; all data-derived text must go through
  `esc()` in app.js. Never hand-edit `site/data.js`, `dist/`, or `data/`.
- `yc_scraper.py` is the owner's standalone xlsx exporter — leave it alone.
- Keep watchlist picks editorial and grounded in the dataset (one_liner /
  long_description / team_size / founder_count / revenue_mention) — don't
  invent funding or traction facts.
