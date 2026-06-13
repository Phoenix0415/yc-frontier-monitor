# YC Frontier Monitor — working notes

Long-term project: monitor new YC batches (Fall 2025 →) and maintain a curated
"companies to watch" report. The owner cares about clean, maintainable code,
spotting frontier-startup trends, and one-click access to each company's site.

## Commands

- `python3 scripts/yc.py update` — fetch → diff → founder + traction enrichment → site build
- `python3 scripts/yc.py enrich [--dry-run] [--limit N]` — LLM traction extraction
  (SPEC 002 §5); `--dry-run` prints quality + cost on a sample and writes nothing
- `python3 scripts/yc.py auto` — update only if due; called daily by launchd
  (cadence: monthly baseline + ~1 week after each batch kickoff; policy in
  `scripts/automate.py`, agent `com.yc-monitor.auto`, log `data/auto.log`)
- `python3 scripts/yc.py schedule install|status|uninstall` — manage the agent
- `python3 scripts/yc.py build` — rebuild site after editing `analysis/`
- `python3 scripts/yc.py status` — counts, watchlist backlog, next scheduled pull

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
   - **Funding screen**: review every company with a `funding_mention`
     (pipeline-extracted from descriptions: raises, named investors). A
     self-announced raise is a watch signal on its own. Caveats: "backed by
     Y Combinator" alone is true of everyone here; "investors behind X" means
     X's VCs, not X itself — read the quote before crediting it.
   - **Owner interest areas**: scan online education, e-commerce, and AI
     software each refresh; add picks only when they genuinely clear the bar —
     owner explicitly does not want force-fitted picks.
   - Don't quote $ figures without checking whose they are (see memory).
3. Edit `analysis/watchlist.json` — schema:
   `summary[]` (exec-summary paragraphs), `methodology` (one para),
   `themes[] {id, title, narrative}`,
   `picks[] {slug, theme, why, learn, signals, picked_at}`.
   The site is bilingual (EN/中文 toggle): every editorial field is an
   `{"en": ..., "zh": ...}` object (`signals` is `{"en": [...], "zh": [...]}`).
   Always write both languages; a plain string is a legal fallback shown in
   both. Company-provided text (one-liners, descriptions) stays English.
   Every `pick.slug` must exist in the dataset; `theme` must match a theme id.
   New picks get `picked_at` (ISO date, the day they enter the watchlist).
   Refresh stats quoted in narratives if the cohort shifted.
   Set `updated_at` to now (UTC ISO) — it drives the "awaiting review" queue.

## Removed features (owner decision, 2026-06-11 — pre-publication)

The verdict layer (SPEC §5: build/copy/partner/ignore tags + calibration
cross-tab) and the review/feedback loop (SPEC §7: reviews[], due-for-review
cadence, evidence) were REMOVED before making the site public — they were the
owner's private decision machinery. Do not re-add them unless Phoenix asks;
the full implementation lives in git history (commits d01057f/8b90715, removal
in the "Remove verdict & review tracking" commit). `picked_at` on picks stays
(provenance), and the "awaiting analyst review" queue (new companies since
watchlist.updated_at) is a different feature and stays.

## Frontier topics (owner-requested upgrade over YC categories)

- YC's own industry fields are too coarse to read trends from, so
  `analysis/topics.json` defines ~20 curated topics (agent-infra, ai-firms,
  defense, lab-data, …) as transparent regex keyword rules — deterministic,
  NO LLM calls (this supersedes SPEC §6b's "YC fields only" by owner request).
  Topic classification stays deterministic; SPEC 002 §3 separately reverses the
  no-LLM-in-pipeline rule for the enrichment passes (see "Traction extraction").
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

## Traction extraction (SPEC 002 Phase 1 — shipped 2026-06-13)

- **What**: an LLM pass over each company's existing `long_description` fills a
  structured `company.traction` in `data/companies.json`:
  `{source:"yc_description", revenue, arr, growth, customers_count, funding,
  named_customers[], extracted_at, content_hash}`. Verbatim strings as disclosed,
  empty when not stated — same "raw, not parsed" spirit as the `*_mention` fields.
- **This deliberately reverses SPEC 001's "no LLM in the pipeline" rule** (SPEC
  002 §3). Still Python 3.9 **stdlib-only**: the Anthropic call goes through
  `urllib` in `scripts/enrich_text.py` — no SDK, no new packages.
- **Key-optional**: reads `ANTHROPIC_API_KEY` from env or a gitignored `.env`.
  No key → the whole step is a clean no-op (build still succeeds); the free
  GitHub Pages flow never breaks for lack of a key. `revenue_mention` /
  `funding_mention` (regex) stay as the no-key fallback on cards.
- **Runs on `update` (manual) and standalone `enrich`; NEVER on the daily `auto`
  tick** (§4a) — keeps the daily deploy cheap. Hash-gated on the
  whitespace-normalized description: re-running with no text change makes zero
  LLM calls. Concurrent, retry-on-429/5xx, failure-safe (an error skips the
  company and leaves any prior `traction` intact; the fetch path never writes it).
- **Config** (`config.json`): `enrich_model` (`claude-haiku-4-5` — small/cheap,
  owner-chosen in the spec), `enrich_workers` (6), `max_companies_per_run` (40 =
  per-`update` cap; the one-time backfill used `enrich --limit 600`),
  `text_enrichment` (on/off). Full 534-company backfill cost ~$0.70.
- **Contract**: `specs/phase1-traction-prompt.md` (local-only) is the exact
  prompt + field contract — don't improvise a different one. Two owner rules are
  baked in: ignore "backed by Y Combinator" alone (true of everyone here — keep
  real investors), and never attribute a founder's PRIOR-company numbers to this
  company (the traction-claims gotcha; see memory).
- **Site**: structured traction block on company cards (green pull-quote;
  supersedes the regex `*_mention` quotes when present), a "discloses traction"
  filter + "Traction first" sort on the Companies tab, bilingual labels, all
  `esc()`-escaped. `status` prints traction coverage.
- State 2026-06-13: 534/558 extracted, 96 disclose traction (vs the regex's ~40
  mentions).

## Conventions

- Python 3.9 stdlib only — no third-party packages anywhere.
- Site is no-build vanilla HTML/CSS/JS; all data-derived text must go through
  `esc()` in app.js. Never hand-edit `site/data.js`, `dist/`, or `data/`.
- Keep watchlist picks editorial and grounded in the dataset (one_liner /
  long_description / team_size / founder_count / revenue_mention) — don't
  invent funding or traction facts.
