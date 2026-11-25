# HEOR Signal

A daily brief on health economics, real-world evidence, market access, HTA,
clinical research and the policy connecting them. Pulls from LinkedIn posts,
ISPOR, NICE, EMA, HAS, FDA, CMS, MHRA, Canada's Drug Agency, PBAC, EU HTA,
ICER and India policy, plus a daily watch on AI builders in the field, then delivers one edition to the web dashboard and
optionally to Telegram.

## What you get

- **One place to read everything.** A web dashboard (`http://localhost:8765`)
  with the current edition, an archive of every past edition, a saved reading
  list, source health and full-text search.
- **A Markdown edition** written to `data/digests/YYYY-MM-DD.md` every run.
- **Optional Telegram delivery** as a short, chunked digest message.
- **A saved reading list** that survives re-runs: bookmark any story and it
  stays put.

## Quick start

```powershell
cd heor-signal
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py update          # fetch, classify, store, select (~80s)
python run.py serve            # open http://127.0.0.1:8765
```

The first run takes roughly 80 seconds because Google News redirects are
decoded and up to 20 public LinkedIn posts are read.

## Daily use

```powershell
python run.py daily             # update + select + Markdown + Telegram (if configured)
python run.py daily --no-telegram   # everything except Telegram
python run.py update            # refresh the store without rebuilding the edition
python run.py select            # rebuild today's selection from the store
python run.py status            # last run, source health, recent editions
```

Add a single LinkedIn post or article by URL:

```powershell
python run.py add "https://www.linkedin.com/posts/..."
python run.py add "https://..." --category ai --unsaved
```

Seed the two example LinkedIn posts from the brief:

```powershell
python run.py seed
```

## Telegram

1. In Telegram, message `@BotFather`, run `/newbot`, copy the token.
2. Send any message to your new bot.
3. Run `python run.py telegram-discover` and copy the `chat_id`.
4. Put both in `.env`:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```

5. Test with `python run.py telegram-test`, then `python run.py daily`.

Messages are split automatically to stay under Telegram's 4096 character limit.

## Dashboard on your phone / LAN

The dashboard binds to `127.0.0.1` by default. To read it on another device
on the same network:

```powershell
python run.py serve --host 0.0.0.0 --port 8765
```

Then open `http://<your-pc-ip>:8765` (for example `http://192.168.1.5:8765`)
on your phone. Set `PUBLIC_BASE_URL` in `.env` so the Telegram digest links
back to the full edition.

For access from anywhere, deploy the app to a small host (a Raspberry Pi,
Fly.io, Render, or a VPS) and point `PUBLIC_BASE_URL` at that URL. The app
is a single FastAPI process with a SQLite file, so it moves easily.

## Scheduling

**GitHub Actions is the daily scheduler** (`.github/workflows/daily.yml`). It
runs whether or not your PC is on, commits the new edition and database to
`main`, and the Vercel deployment picks that up.

- Two slots: 05:47 IST (primary) and 09:11 IST (fallback). The fallback exits
  straight away if today's edition already exists. GitHub delays scheduled
  jobs at busy times, so expect the brief some time in the morning, not to
  the minute.
- **Run it now:** Actions tab → "Daily HEOR Signal update" → Run workflow, or
  `gh workflow run daily.yml`.
- **Failure alerts:** the job fails (and GitHub emails you) when nothing was
  fetched or at least half the sources failed. Partial problems, such as
  LinkedIn refusing guest access, show as a yellow warning on the run.
- To add Telegram delivery, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
  as repository secrets.

Your local copy does not update itself. Run `git pull` before
`python run.py serve` to read the latest edition locally.

### Windows Task Scheduler (only if you stop using GitHub Actions)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

Registers `HEOR Signal Daily` (07:30, runs `scripts\daily.ps1`) and
`HEOR Signal Dashboard` (at logon). Do not run this alongside the GitHub
workflow: both would write `data/heor_signal.db` and the next `git pull`
would conflict. Use `-NoDashboard` to register only the daily task, and
`.\scripts\uninstall_schedule.ps1` to remove both.

## AI builders section

A separate section tracks who is building AI tools for HEOR, RWE, SLR,
regulatory/HTA, CRO and health data work: product launches, funding rounds,
acquisitions and partnerships. Each story is tagged with its stage (for
example `Funding · $95M Series B`) and segment (`Regulatory & writing`,
`SLR & evidence`, `RWE & data`, `CRO & trials`, `HEOR & access`, `HTA & JCA`).

- Sources: short Google News searches (RWE, SLR, regulatory writing, trials,
  HEOR, CRO deals), a LinkedIn search for rounds and deals, two watchlist
  searches, and the tech.eu, Pulse 2.0 and PR Newswire health feeds.
- Rules live in `radar/builders.py`. A story qualifies when it pairs AI with
  one of those fields and reports a launch, round, deal or partnership, or
  when it names a watchlist company.
- The watchlist is `builders.watchlist` in `config.yaml`, seeded with the
  companies in `HEOR-AI-Builders-Landscape.pdf`. Add a company there and to
  one of the `bld_watch_*` queries to follow it.
- Up to `max_builders` (10) stories a day, on top of the main brief, so
  neither crowds the other out. Builder stories stay eligible for 14 days and
  are not repeated across editions.

## Run health

Every update reports `health ok | degraded | error`:

- `error`: nothing fetched or at least half the sources failed. `run.py
  daily` still writes the edition from the store, but exits with code 3.
- `degraded`: some sources failed, or LinkedIn search found posts but none
  could be read.
- Sources that returned no items are listed as a note.

Housekeeping: candidates that never made an edition and were not saved are
deleted after 30 days, and stories that ran in the last 7 days are not
repeated under a new headline.

## Configuration

`config.yaml` controls sources, limits and categories. The most useful knobs:

```yaml
app:
  max_items: 42              # stories per edition
  max_linkedin: 12           # LinkedIn posts per edition
  max_per_source: 5          # non-LinkedIn items per publisher
  max_per_category: 9        # items per category
  min_score: 5.0             # minimum relevance score
  decode_limit: 120          # Google News links to resolve per run
```

`.env` holds secrets and deployment settings (`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `PUBLIC_BASE_URL`, `HOST`, `PORT`, `TZ`).

## How it works

1. `radar/fetch.py` reads direct feeds (NICE, ISPOR, EMA, HAS, HIT Consultant) and targeted
   Google News RSS queries for everything else, including LinkedIn posts via
   `site:linkedin.com/posts`.
2. `radar/enrich.py` resolves Google News redirects and reads public LinkedIn
   post pages for full text and author.
3. `radar/classify.py` scores each item for relevance, assigns a category and
   applies hard filters (job ads, financial wires, unrelated industries) and
   soft penalties (conference promos, press releases).
4. `radar/pipeline.py` deduplicates, applies freshness and domain filters,
   selects the edition with per-source and per-category caps, and stores
   everything in SQLite.
5. `radar/digest.py` renders Markdown and Telegram messages.
6. `app.py` serves the dashboard.

## Troubleshooting

- **LinkedIn 429 / rate limit.** The run stops enriching LinkedIn posts and
  keeps the headlines. Re-run later; the store keeps what it already has.
- **PubMed 429.** Calls are serialised with a lock and retried with backoff.
- **Windows console crashes on special characters.** `run.py` reconfigures
  stdout/stderr to UTF-8 on import.
- **No edition appears.** Run `python run.py update` first, then
  `python run.py select`.
- **Stale dashboard.** Click Refresh in the UI or run `python run.py update`.

## Files

- `run.py` — CLI entry point.
- `app.py` — FastAPI dashboard.
- `radar/` — fetch, enrich, classify, pipeline, digest, telegram, db, util.
- `templates/`, `static/` — dashboard UI.
- `scripts/` — Windows launchers and scheduler.
- `data/heor_signal.db` — SQLite store.
- `data/digests/` — Markdown editions.
