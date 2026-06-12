# YC Frontier Monitor

[English](#english) | [中文](#中文)

---

<a name="english"></a>

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

---

<a name="中文"></a>

# YC 前沿公司监测站

追踪 Y Combinator 最新批次（从 2025 年秋季起），持续更新数据集，并生成双语（中文/英文）报告：哪些公司值得关注、原因何在、以及自上次检查以来有哪些新动态。

本项目与 Y Combinator 无关。公司数据来自 YC 公开的公司目录；关注名单仅代表编辑观点，不构成投资建议。代码采用 MIT 许可证，详见 LICENSE。

## 快速开始

```bash
python3 scripts/yc.py update    # 拉取最新数据 → 差异对比 → 重建站点
open site/index.html            # 打开报告（支持直接从 file:// 浏览）
```

`dist/yc-monitor.html` 是整个站点打包成的单个独立文件，可随意分享、发邮件或放到任何地方。

## 命令说明

| 命令 | 功能 |
| --- | --- |
| `python3 scripts/yc.py update` | 拉取所有追踪批次，与上一次状态做差异对比，为新公司填充创始人数量，重建站点。 |
| `python3 scripts/yc.py update --no-founders` | 同上，但跳过创始人数量抓取（更快）。 |
| `python3 scripts/yc.py auto` | 仅在需要更新时执行（见下方节奏说明），调度器调用此命令——否则几乎是空操作。 |
| `python3 scripts/yc.py schedule install` | 安装 macOS launchd 定时任务，每天 10:00 运行 `auto`。`status` / `uninstall` 用于管理。 |
| `python3 scripts/yc.py build` | 用磁盘上已有数据重建站点——编辑 `analysis/watchlist.json` 后运行。 |
| `python3 scripts/yc.py status` | 显示各批次数量、待审公司数，以及下次自动拉取时间。 |

## 发布（GitHub Pages——无需域名）

仓库内置了一个工作流（`.github/workflows/monitor.yml`），让 GitHub 同时充当调度器和托管服务：

1. 在 github.com 创建公开仓库并推送本项目。
2. 仓库 **Settings → Pages → Source: 选择 "GitHub Actions"**（仅需一次）。
3. 在 Actions 标签页手动触发 **Monitor & deploy** 工作流（或直接推送代码）——站点将出现在 `https://<用户名>.github.io/<仓库名>/`。

之后它会自动维护：每日 Action 触发一次 `yc.py auto`（与本地定时任务相同的节奏策略——每月基线 + 每批次启动约一周后），将刷新后的 `data/*.json` 提交回仓库，重建并重新部署。

如果 Action 负责更新，请停用本地 launchd 定时任务（`python3 scripts/yc.py schedule uninstall`），并在本地编辑观察名单前先 `git pull`——两个写入者同时操作只会制造噪音。自定义域名可在 Pages 设置中后续添加；在此之前 github.io 地址是免费的。

## 自动更新节奏

`schedule install` 会设置一个 launchd 定时任务（`com.yc-monitor.auto`），每天触发一次，运行 `yc.py auto`。实际拉取数据仅在以下情况发生：

- **每月基线** —— 数据超过 31 天未更新时；
- **批次启动加速** —— 每个新批次开始约一周后触发一次（批次在每年 1/4/7/10 月第一周启动），此时新批次的公司名单刚开始填入目录。

启动日期是估算值，并非抓取自官方——YC 不提供机器可读的日历——每月基线会兜底处理任何时机偏差。节奏策略在 `scripts/automate.py` 中；launchd 定时任务只是一个简单的每日触发器，Mac 在 10:00 休眠时会在唤醒后补跑。活动记录在 `data/auto.log`；站点"Updates"标签页显示下次计划拉取时间。

移动项目文件夹后，需重新运行 `schedule install`（plist 文件中嵌入了绝对路径）。

## 数据如何保持最新

- **批次自动向前滚动。** 从 `start_batch`（config.json）到今天 + `lookahead_months` 的所有批次都被追踪。YC 会提前数月在目录中列出即将到来的批次（2026 年 6 月就已有 Fall 2026 的条目），因此新批次无需修改配置即可自动出现。
- **更新以差异形式呈现。** 新公司获得 `NEW` 徽章和更新日志条目；下架公司也会被记录。Updates 标签页展示完整历史。
- **拉取失败不会破坏现有数据。** 无法拉取的批次保留上次数据；所有写入均为原子操作。

## 数据来源

1. **YC 公司页面背后的 Algolia 实时索引。** 公开搜索密钥会轮换，因此 `scripts/sources.py` 每次运行时会从页面（`window.AlgoliaOpts`）重新发现密钥。
2. **备用：** [yc-oss 镜像](https://github.com/yc-oss/api)，每日从同一索引重建（最多滞后 24 小时）。

如果拉取失败，几乎都是密钥发现正则的问题：检查 YC 公司页面上 `window.AlgoliaOpts` 现在的格式。

## 分析层

`analysis/watchlist.json` 是报告的人工策划部分：执行摘要、主题和精选公司（`why` 关注理由 / `learn` 可借鉴之处）。数据管道不会写入此文件。每次更新后，当"Updates"标签页标记新公司为"待分析师审查"时，刷新此文件——通常是请 Claude 审查新来者——然后运行 `python3 scripts/yc.py build`。

站点右上角有中/英切换（浏览器记忆偏好）。观察名单中所有编辑字段均为双语 `{"en": ..., "zh": ...}` 对象——编辑时请同时维护两种语言。公司提供的原始文本（一句话介绍、详细描述）按设计保留英文原文。

## 目录结构

```
config.json              起始批次、前瞻月数、选项配置
scripts/
  yc.py                  CLI 入口（update / auto / schedule / build / status）
  batches.py             追踪的批次（自动向前滚动）
  automate.py            更新节奏策略 + launchd 每日触发
  sources.py             Algolia + 镜像拉取，统一规范化格式
  enrich.py              从公司页面获取创始人数量（增量式）
  store.py               标准数据集、快照、更新日志（原子写入）
  sitebuild.py           将数据与分析打包进站点
data/
  companies.json         标准数据集（请勿手动编辑）
  changelog.json         每次运行的新增/删除记录
  snapshots/<run>/       原始的分批次抓取数据，供溯源查阅
analysis/
  watchlist.json         精选公司与叙述（人工/Claude 层）
site/
  index.html             监测站（报告 / 公司 / 更新 三个标签页）
  styles.css, app.js     纯 CSS/JS，无需构建步骤
  data.js                自动生成——请勿手动编辑
dist/
  yc-monitor.html        整个站点打包为单个可移植文件（自动生成）
```

## 备注

- 数据来自 YC 公开列出的公司，即已亮相的公司——年轻批次会持续增长数月。这正是监测站存在的意义。
- 仅使用 Python 3.9+ 标准库；站点为无构建步骤的原生 HTML/CSS/JS。
- `yc_scraper.py` + `yc_Fall2025.xlsx` 早于本监测站（对单一批次的一次性 Excel 导出），仍可使用，但监测站已将其取代。
