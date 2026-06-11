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
   Standing judgment rules (owner-set, 2026-06):
   - **Expansion screen**: review every company whose `team_size` far exceeds
     `founder_count` (≥4× or ≥8 people) — payroll is the costliest signal a
     startup sends; a vague one-liner is not a reason to skip (Brickanta
     lesson: "Agentic AI for Society Builders" hid $8M raised, 2→11 people).
   - **Owner interest areas**: scan online education, e-commerce, and AI
     software each refresh; add picks only when they genuinely clear the bar —
     owner explicitly does not want force-fitted picks.
   - Don't quote $ figures without checking whose they are (see memory).
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

## Feedback loop (SPEC §7 — shipped)

- Reviews are human judgments appended to picks (pipeline never writes them):

  ```json
  "reviews": [{"date": "2026-12-11",
               "outcome": "thriving | growing | flat | pivoted | dead | unclear",
               "note": {"en": "…", "zh": "…"}}]
  ```

  Validated like verdicts (enum + ISO date, build fails loudly).
- Cadence: a pick is due when `today − max(picked_at, latest review date) ≥
  review_interval_days` (config.json, default 180). `status` prints the due
  count; the Updates tab shows due picks with dataset-only evidence —
  team_size at pick time (nearest `data/snapshots/` entry) vs now, one-liner
  changes since picked_at (Phase-2 records), and a delisted flag. Computed at
  build time in `sitebuild.compute_due_reviews()` (read-only).
- Report tab "calibration" block: latest outcome per pick × verdict action
  cross-tab plus coverage count; renders at zero coverage by design.
- When the owner reviews a due pick, append a `reviews[]` entry (bilingual
  note), do NOT touch `picked_at`; the new review date resets the clock.

## Frontier topics (owner-requested upgrade over YC categories)

- YC's own industry fields are too coarse to read trends from, so
  `analysis/topics.json` defines ~20 curated topics (agent-infra, ai-firms,
  defense, lab-data, …) as transparent regex keyword rules — deterministic,
  NO LLM calls (this supersedes SPEC §6b's "YC fields only" by owner request;
  the no-LLM-in-pipeline constraint stands).
- Matching runs at build time (`sitebuild.load_topic_rules` /
  `classify_topics`) over name + one-liner + description + tags + industry;
  multi-label; no match → "(unclassified)" (`__none`). Topics are derived
  into the payload only — never written back to `data/`. Bad regex → build
  fails loudly.
- UI: "Frontier topics" chart and the momentum table run on topics (both
  clickable → filtered Companies list), topic dropdown filter, accent topic
  chips on cards. The "YC industries" chart stays for raw provenance.
- Tuning loop when distribution drifts or unclassified grows: edit patterns
  in topics.json → `python3 scripts/yc.py build` → check the report. Keep
  unclassified under ~5%; current state ~2%. Labels are bilingual {en, zh}.

## Delta engine (SPEC §6 — shipped)

- Field-level diffing (`store.WATCHED_FIELDS`: one_liner, long_description,
  team_size, website, status): every `update` compares whitespace-normalized
  values for companies present in both states and appends
  `changed: [{slug, name, field, old, new}]` (raw values) to that run's
  changelog entry, per batch. Older entries without `changed` stay valid.
- UI: change lines in the Updates tab history (`team_size: 5 → 9`, text
  truncated to ~70 chars); companies changed in the LATEST run get a yellow
  CHANGED/有变化 badge whose tooltip lists the fields.
- Theme momentum: Report tab table, rows = top-10 `subindustry || industry`
  (YC's own categories) overall, columns = batches, cells = count · share of
  batch with a CSS bar scaled to the table max. Derived in app.js at render
  time from the dataset — nothing persisted (satisfies §6b "no new file").
  Missing cells render as dim "–"; young batches are small samples.
4. `python3 scripts/yc.py build`, then sanity-check `site/index.html`.

## Conventions

- Python 3.9 stdlib only — no third-party packages anywhere.
- Site is no-build vanilla HTML/CSS/JS; all data-derived text must go through
  `esc()` in app.js. Never hand-edit `site/data.js`, `dist/`, or `data/`.
- `yc_scraper.py` is the owner's standalone xlsx exporter — leave it alone.
- Keep watchlist picks editorial and grounded in the dataset (one_liner /
  long_description / team_size / founder_count / revenue_mention) — don't
  invent funding or traction facts.
