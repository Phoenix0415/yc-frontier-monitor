# YC Frontier Monitor — working notes

Long-term project: monitor new YC batches (Fall 2025 →) and maintain a curated
"companies to watch" report. The owner cares about clean, maintainable code,
spotting frontier-startup trends, and one-click access to each company's site.

## Commands

- `python3 scripts/yc.py update` — fetch → diff → founder + traction enrichment → site build
- `python3 scripts/yc.py enrich [--dry-run] [--limit N]` — LLM traction extraction
  (SPEC 002 §5); `--dry-run` prints quality + cost on a sample and writes nothing
- `python3 scripts/yc.py enrich --site [--dry-run] [--translate] [--limit N]` —
  LLM website enrichment (SPEC 002 §6): fetch each site → value prop / pricing /
  launch stage; `--translate` localizes the paraphrases to {en,zh} (no re-fetch)
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

## Website enrichment (SPEC 002 Phase 2 — shipped 2026-06-13)

- **What**: `scripts/enrich_site.py` fetches each company's own site (homepage +
  a linked `/pricing` and `/about`), reduces HTML to visible text with the stdlib
  `html.parser`, and an LLM pass fills `company.enrichment`:
  `{source:"website", source_url, value_prop, pain_point, target_customer,
  pricing:{has_pricing, model, entry_price, currency, notes}, launch_stage,
  named_customers[], fetched_at, content_hash}`.
- **Two-tier strictness**: `value_prop`/`pain_point`/`target_customer` are faithful
  paraphrases of the page; `pricing`/`launch_stage`/`named_customers` must be
  grounded on the page (never guessed). `launch_stage` ∈ launched | early-access |
  waitlist | building | unknown; `pricing.model` ∈ self-serve | sales-led |
  freemium | usage | tiered | unknown (hardware/one-time price = self-serve/tiered,
  not freemium).
- **Untrusted input**: page text is treated as data, never instructions — the
  prompt + a few-shot example make the extractor resist prompt injection.
- **stdlib only**: `urllib` (fetch + Anthropic call, reusing `enrich_text`'s
  retry/POST), `html.parser` (text), `urllib.robotparser` (robots). Polite: real
  UA, timeout, per-host delay, **robots.txt honored** (a Disallow → skip). Each
  page capped ~8K chars, combined ~16K, to bound tokens. JS-only/parked → low
  yield, fields left empty (graceful, never wrong).
- **Key-optional / hash-gated / failure-safe / off the daily tick** — same rules
  as Phase 1 (§4a). Hash is of the fetched page text; re-fetched unchanged text →
  zero LLM calls. Concurrent (`enrich_workers`), retry-on-429/5xx.
- **Carry-forward fix (store.apply_run)**: `update` now carries `traction` AND
  `enrichment` across runs (previously only `founder_count`), so a fetch never
  drops them and the hash gates decide refresh. `needs_enrichment` currently only
  enriches NEW companies (no periodic site re-fetch yet — that's the next lever).
- **Config**: `enrich_site_model` (optional; defaults to `enrich_model` = Haiku —
  dry run showed Haiku quality is sufficient), `enrich_workers`,
  `max_companies_per_run`. The one-time backfill (`enrich --site --limit 600`)
  cost $1.41 — 446/558 enriched (443 meaningful), 112 no-text (JS-only/parked),
  10 fetch errors. launch_stage: 289 launched, 40 early-access, 8 waitlist,
  2 building, 107 unknown; 228 show pricing.
- **Site**: value prop + pain point + pricing line on Companies cards AND on
  Report-tab watchlist picks, a launch-stage badge, a launch-stage filter + "has
  pricing" pill (Companies tab), all `esc()`-escaped. `value_prop`/`pain_point`/
  `target_customer` are stored bilingual `{en,zh}` — a `--translate` Haiku pass
  localizes the English paraphrases (and a normal `enrich --site` auto-translates
  freshly-extracted companies); verbatim facts (traction, prices, named_customers)
  stay English; `pricing.model` + `launch_stage` render via translated label maps.
  `status` prints enrichment coverage.
- **Contract**: `specs/phase2-website-prompt.md` (local-only) — the exact prompt,
  fetch policy, and field contract. Don't improvise a different one.
- **DEFERRED (the one remaining §6 item)**: surfacing a `waitlist → launched`
  move as a CHANGED event on the Updates tab. The UI already renders `changed`
  records generically, so it's backend-only — but it needs a post-enrichment diff
  (apply_run runs *before* enrichment, so it can't see the new launch_stage), and
  it produces nothing until a periodic re-fetch exists. Do it with the re-fetch
  cadence, not before.

## Conventions

- Python 3.9 stdlib only — no third-party packages anywhere.
- Site is no-build vanilla HTML/CSS/JS; all data-derived text must go through
  `esc()` in app.js. Never hand-edit `site/data.js`, `dist/`, or `data/`.
- Keep watchlist picks editorial and grounded in the dataset (one_liner /
  long_description / team_size / founder_count / revenue_mention) — don't
  invent funding or traction facts.
