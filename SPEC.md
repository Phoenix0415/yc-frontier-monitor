# SPEC — YC Frontier Monitor: from reading to deciding

> Status: draft v1 · Owner: Phoenix · Implementer: Claude Code
> Read `CLAUDE.md` first. Three capabilities, three phases. Implement strictly one phase at a time; every phase ends with the checkpoint in §8.

## 1. Problem

The monitor answers "what's new in the YC directory." The owner's actual job is to spot where the market is heading and decide what to build or invest in. Today the pipeline only diffs adds/removals (the trend signal hidden in field changes is discarded), picks record *why watch* but never a decision, and nothing tracks whether past picks panned out. Result: reading, not deciding — and no calibration loop on the owner's own judgment.

## 2. Goals

1. Every pick can carry an explicit decision (build / copy / partner / ignore) visible in the report.
2. Field-level changes (pivot signals, team growth) are captured on every pull and visible on the Updates tab.
3. Per-batch theme distribution makes momentum across batches visible at a glance.
4. Picks resurface for review on a cadence with the evidence the dataset can supply; outcomes accumulate into a hit-rate view.

## 3. Non-goals (v1)

- **No external data sources** (LinkedIn, GitHub, funding APIs, news). That's a separate project — decide deliberately later.
- **No LLM calls inside the pipeline** (no auto-classifying companies into custom watchlist themes). Theme momentum uses YC's own `industry`/`tags` fields only.
- **No automated outcome detection.** Outcomes are human judgments; the monitor only supplies reminders and evidence.
- **No rewrite, no new project**, no touching `yc_scraper.py`, no third-party packages.
- **No invented facts** — funding/traction claims never go beyond what the dataset contains.

## 4. Inherited constraints (do not violate)

- Python 3.9 stdlib only. Site stays no-build vanilla HTML/CSS/JS; all data-derived text goes through `esc()`.
- The pipeline never writes `analysis/watchlist.json`; humans never hand-edit `data/`, `site/data.js`, `dist/`.
- All writes atomic; a failed fetch never destroys state.
- Editorial fields are bilingual `{"en": …, "zh": …}`; a plain string is a legal fallback.
- Backward compatibility: every old data file missing the new keys must keep working.

## 5. Phase 1 — Decision layer (verdicts)

**What**: each pick gets an explicit verdict, plus the metadata Phase 3 will need.

**Schema** — `analysis/watchlist.json`, per pick; all new keys optional:

```json
{
  "slug": "…",
  "theme": "…",
  "picked_at": "2026-06-11",
  "verdict": {
    "action": "build | copy | partner | ignore",
    "note": {"en": "…", "zh": "…"},
    "decided_at": "2026-06-11"
  }
}
```

- **Semantics**: `build` = we build in this space ourselves · `copy` = adapt the model for the China market · `partner` = integration / channel candidate · `ignore` = reviewed, not relevant (kept for the record). Absent verdict renders as **undecided**.
- **Migration**: one-time backfill of `picked_at` = current `watchlist.updated_at` for existing picks. Done by hand/Claude in the JSON — not by the pipeline.

**Build & site**:
- `sitebuild.py` validates: `action` in enum, dates parse as ISO; fail the build with a clear message on violation.
- Report tab: verdict badge on each pick card (one color per action; undecided = grey); note rendered with the pick, bilingual like other editorial text.
- Companies tab: same badge on picked companies. Filter-by-verdict is P1 — skip if it adds complexity.
- `status` prints pick counts by verdict, including undecided.

**Acceptance**:
- [ ] Build fails loudly on a bad `action` value; passes when `verdict` is absent.
- [ ] Badges and notes render correctly in both EN and 中文 toggle states, escaped.
- [ ] `status` shows the verdict breakdown.
- [ ] The pre-migration watchlist still builds unchanged.

## 6. Phase 2 — Delta engine

### 6a. Field-level diffing

**What**: on every `update`, for companies present in both the previous canonical dataset and the new fetch, detect changes in watched fields.

- **Watched fields** (a constant in `store.py`): `one_liner`, `long_description`, `team_size`, `website`, `status` — map to the normalized schema's actual field names.
- **Noise control**: normalize before comparing (trim, collapse internal whitespace); store the raw new value.
- **Storage**: extend `changelog.json` per-run entries with `"changed": [{"slug", "field", "old", "new"}]` alongside adds/removals. Older entries without `changed` stay valid.

**Site**: Updates tab gets a **Changed** section per run — `team_size` rendered as `5 → 9`; text fields as truncated old → new (expand-to-full is P1). A `CHANGED` badge on the company card pointing at its latest change.

### 6b. Theme momentum

**What**: per-batch distribution over YC-provided `industry` (and/or `tags`), so category momentum across batches is visible.

- Computed at build time from `companies.json`; no new persistent file.
- Report tab, one compact view: rows = top ~10 industries by overall count, columns = batches, cells = count + share-of-batch %. Pure CSS bars, no chart library. UI chrome bilingual; industry names stay as YC provides them.

**Acceptance**:
- [ ] Edit a fixture company's `one_liner` / `team_size` in the previous state → run update → change appears in changelog and on the Updates tab.
- [ ] Whitespace-only edits produce no change record.
- [ ] Runs with zero changes render fine; pre-existing changelog entries still render.
- [ ] Momentum view renders across all tracked batches and degrades gracefully where industry data is missing.

**Blocking check before 6b**: confirm `sources.py` keeps `industry`/`tags` in the normalized schema. If they're dropped today, add them to the schema first; a full refetch repopulates them.

## 7. Phase 3 — Feedback loop

**What**: picks resurface for review on a cadence, with evidence; outcomes accumulate into a calibration view.

**Schema** — per pick, appended by human/Claude during reviews; pipeline never writes it:

```json
"reviews": [
  {
    "date": "2026-12-11",
    "outcome": "thriving | growing | flat | pivoted | dead | unclear",
    "note": {"en": "…", "zh": "…"}
  }
]
```

**Cadence**: a pick is **due** when `today − max(picked_at, latest review date) ≥ review_interval_days` (new key in `config.json`, default `180`).

**Evidence (automatic, existing data only)** — shown next to each due pick:
- `team_size` at pick time vs now (nearest snapshot / changelog entry);
- one-liner changes since `picked_at` (from Phase 2 records);
- delisted flag if the company dropped out of the directory.

**Surfacing**:
- `status` prints "N picks due for review"; the Updates tab lists them with evidence.
- Report tab: a **hit-rate block** — latest-outcome counts, plus a verdict-action × outcome cross-tab (the calibration view: of my `build` calls, how many are growing?). Plain table.

**Acceptance**:
- [ ] A pick older than the interval with no review shows as due; adding a review clears it until the next interval.
- [ ] Evidence shows the correct team_size delta for a fixture with a known change.
- [ ] Hit-rate block renders with zero, partial, and full review coverage.
- [ ] The pipeline still never writes `analysis/watchlist.json`.

## 8. Process & checkpoints (every phase — handoff-drift protection)

1. Implement the phase.
2. Run `update` + `build`; sanity-check `site/index.html` in both languages.
3. Update **CLAUDE.md** to document the new schema/commands *as shipped* — reality, not plans.
4. Commit.
5. Stop. Owner reviews before the next phase starts.

Suggested Claude Code goals, in order:
`Phase 1 — add verdict layer per SPEC.md §5` → `Phase 2 — delta engine per SPEC.md §6` → `Phase 3 — feedback loop per SPEC.md §7`.

## 9. Open questions

- **Blocking (Phase 2b only)**: does the normalized schema currently retain `industry`/`tags`? Check `sources.py` before starting 6b.
- Non-blocking: how sparse is `team_size` across batches? If very sparse, demote it from headline evidence to "when available".
- Non-blocking: is 180 days the right default review interval?
