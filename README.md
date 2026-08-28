# CrytpTrde

A **paper-trading bot for CoinDCX** (Indian INR spot markets) that trades an
RSI swing strategy, runs a **500-bot strategy tournament** against live prices,
and publishes every result to a **static analytics dashboard on GitHub Pages**.

> [Live dashboard → https://clickalex.github.io/CrytpTrde/](https://clickalex.github.io/CrytpTrde/)

| | |
|---|---|
| **Bot** | `cryptobot.py` — thin CLI launcher over the `src/cryptobot` package; no build step, no database |
| **Mode** | paper only. **No real orders are ever placed** — see [Going live](#going-live-read-before-touching-live) |
| **Runtime** | Python 3.10+ · `requests` + `PyYAML` |
| **Scheduling** | GitHub Actions cron (hourly) — free, no server |
| **Tests** | 15 offline tests, stubbed API, no network or secrets needed |

> Branch note: feature work happens on Arena session branches (`arena/*`) opened against `main` via pull requests.

---

## Contents

- [What the bot does](#what-the-bot-does)
- [Quick start](#quick-start)
- [Commands](#commands)
- [The strategy](#the-strategy-rsi-swing)
- [Configuration reference](#configuration-reference)
- [Scan more than BTC / ETH / SOL](#scan-more-than-btc--eth--sol)
- [Why a run is fast (and what made it slow)](#why-a-run-is-fast-and-what-made-it-slow)
- [Holding-period bots (week / month)](#holding-period-bots-week--month)
- [Backtesting & the 500-bot tournament](#backtesting--the-500-bot-tournament)
- [Web dashboard (GitHub Pages)](#web-dashboard-github-pages)
- [Running in the cloud (GitHub Actions)](#running-in-the-cloud-github-actions)
- [Tests & CI](#tests--ci)
- [Repo layout & data files](#repo-layout--data-files)
- [India tax notes](#india-tax-notes)
- [Going live (read before touching `live:`)](#going-live-read-before-touching-live)
- [Disclaimer](#disclaimer)

---

## What the bot does

1. **Discovers the universe** — pulls CoinDCX's active INR spot markets and
   keeps the most liquid ones (default: top 50 by 24h turnover), so you are not
   stuck watching three large caps.
2. **Fetches market data in parallel** — one candle request per asset per run,
   8 workers at a time, globally paced so the exchange is never hammered.
3. **Applies the RSI swing rules** to each asset and buys/sells in a simulated
   portfolio that accounts for taker fees, slippage and the 1% Indian TDS.
4. **Persists state to plain files** (`data/state/portfolio.json`,
   `data/state/trades.csv`) so the repo *is* the database — which is exactly
   how it runs for free on GitHub Actions.
5. **Competes 500 strategy variants against each other** on real prices, ranked
   hourly, with the leaderboard surfaced in the dashboard.

Everything is deliberately inspectable: CSVs and JSON, no hidden stores.

---

## Quick start

```bash
# 1. Dependencies (requests + PyYAML; matplotlib only for --chart)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Create the paper portfolio (₹50,000 starting cash by default)
python3 cryptobot.py init --yes

# 3. See what it would trade right now
python3 cryptobot.py assets        # which markets the config resolves to
python3 cryptobot.py check         # one signal-check cycle
python3 cryptobot.py status        # positions, P&L, tax estimate

# 4. Judge the strategy before trusting it
python3 cryptobot.py backtest              # RSI swing vs HODL on 30 days of 1h candles
python3 cryptobot.py backtest --chart eq.png
python3 cryptobot.py sweep                 # 500-bot tournament on the same history
python3 cryptobot.py sweep-live            # same 500 bots, trading live prices (paper)
python3 cryptobot.py sweep-status          # re-rank without trading
python3 cryptobot.py bot acc_001           # full buy/sell history of one tournament bot
python3 cryptobot.py coin BTCINR           # which tournament bots traded a given coin
```

No API key is needed in paper mode. To keep it running, either start the
daemon (`python3 cryptobot.py run`) or let GitHub Actions do it — see
[Running in the cloud](#running-in-the-cloud-github-actions).

---

## Commands

All commands accept `--config <path>` and `--all-assets` (ignore the configured
watchlist and scan every active INR market).

| Command | What it does |
|---|---|
| `init [--yes]` | Create / reset the paper portfolio at `initial_cash_inr` |
| `status` | Portfolio, per-asset P&L, and the paper tax estimate |
| `assets` | List the assets this config resolves to (discovery included) |
| `check` | One signal-check cycle: fetch, evaluate, place simulated fills |
| `run` | Daemon — repeats `check` every `check_interval_min` minutes |
| `reset [--yes]` | Delete `data/state/portfolio.json` + `data/state/trades.csv` |
| `backtest [--days N] [--chart FILE.png]` | Replay the strategy on real 1h history vs a HODL benchmark |
| `sweep [--days N] [--count N] [--chart FILE.png]` | Historical tournament across the strategy grid |
| `sweep-live [--top N] [--rank-only] [--reset]` | Run every demo account on live prices (paper); `--reset` restarts all at ₹10,000 |
| `sweep-status` | `sweep-live --rank-only`: rebuild the ranking without trading |
| `bot <account> [--json]` | Full trade history of one tournament bot (all fills + current holdings), read straight from `data/sweep/accounts/` — offline, no CoinDCX call |
| `coin <asset> [--json]` | Every tournament bot that bought/sold a given coin, with per-bot buy/sell counts, net qty, fees, TDS and realized P&L |

`backtest` writes its CSVs into `data/backtest/`; `sweep` and `sweep-live`
write into `data/sweep/` — see [data files](#repo-layout--data-files).

---

## The strategy: "RSI Swing"

Hourly, condition-based, one position per asset — no stacking, no panic-doubling.

| | Rule |
|---|---|
| **BUY** | 1h RSI meets the entry threshold — which way round depends on `entry_mode` (below) — **and** no open position on that asset |
| **SELL** | take-profit: price ≥ avg cost × (1 + `take_profit_pct`/100) |
| | stop-loss: price ≤ avg cost × (1 − `stop_loss_pct`/100) |
| | time exit: held ≥ `max_hold_hours` (when non-zero) |
| | overbought: 1h RSI ≥ `exit_rsi` |

Exits are evaluated in that priority order, and the reasons recorded in the trade
logs are exactly `take_profit`, `stop_loss`, `hold_timeout`, `rsi_overbought`.

It's a *level* test rather than a cross, so a coin that stays oversold keeps
qualifying until the buy actually fills. Buys are filled at the **ask** and sells
at the **bid** (both from one shared ticker call, not a mid-price fantasy), and
the RSI that decides an entry comes from the candle set fetched in the same cycle.

Two entry families share the same exit engine:

- `entry_mode: dip` (default) buys **weakness** — `RSI <= entry_rsi`
- `entry_mode: momentum` buys **strength** — `RSI >= entry_rsi`

`entry_signal()` / `exit_reason()` are used identically by the live loop, the
backtest and the tournament, so a backtest can't drift from live behaviour. A
typo'd `entry_mode` fails fast instead of silently trading as a dip bot.

Sizing guards: a buy is skipped when the amount it would deploy is under
`min_buy_inr`, the broker rejects anything below the market's own `min_notional`
(₹100 by default), and quantities are floored to the market's tick step /
precision — so simulated fills are sizes you could actually submit.
---

## Configuration reference

Everything tunable lives in `config/config.yaml` (the default `--config`
path). The knobs that change behaviour most:

| Key | Default | Notes |
|---|---|---|
| `mode` | `paper` | `live` only switches the API client to authenticated endpoints — it does **not** place orders (see [Going live](#going-live-read-before-touching-live)) |
| `initial_cash_inr` | `50000` | starting paper cash |
| `fee_rate` / `slippage_bps` | `0.001` / `5` | 0.10% taker fee, 5 bps simulated slippage |
| `simulate_tds` / `tds_rate` | `true` / `0.01` | the 1% TDS Indian exchanges deduct on sells |
| `check_interval_min` | `60` | cadence in `run` mode |
| `public_request_interval_sec` | `0.05` | global pace (~20 request **starts**/sec across all workers); `0` disables |
| `network.fetch_workers` | `8` | assets fetched concurrently; `1` = old serial behaviour |
| `assets` | `auto` | `auto` = discover; or an explicit `- name:/pair:` list |
| `asset_discovery.min_volume_inr` | `1000000` | ignore illiquid tails (≥ ₹10 lakh 24h turnover) |
| `asset_discovery.max_assets` | `50` | `null`/`0` = all ~500+ pairs (a run then takes minutes) |
| `asset_discovery.dipped_scan_pct` | `8` | safety net: also scan markets down ≥ 8% in 24h; `0` disables |
| `asset_discovery.dipped_scan_max` | `150` | cap on those extra markets per scan |
| `strategy.entry_mode` | `dip` | `dip` or `momentum` |
| `strategy.timeframe` / `rsi_period` | `1h` / `14` | signal candle size and RSI lookback |
| `strategy.entry_rsi` / `exit_rsi` | `30` / `72` | oversold entry, overbought early exit |
| `strategy.take_profit_pct` / `stop_loss_pct` | `4` / `2` | % exits vs average cost |
| `strategy.position_size_pct` | `40` | share of available cash per buy |
| `strategy.signal_lookback` | `200` | hourly candles fetched per asset per check |
| `strategy.max_hold_hours` | `0` | `168` = one-week bot, `720` = one-month bot |
| `backtest.days` / `start_cash_inr` | `30` / `100000` | history replayed, and capital for strategy **and** HODL benchmark |
| `sweep.accounts` | `500` | demo bots in the tournament |
| `sweep.start_cash_inr` | `10000` | starting balance per demo account |
| `sweep.*` lists | see file | the parameter grid mixed across the bots |
| `live.enabled` | `false` | plus `api_key_env` / `api_secret_env` names |

`acc_001` in the tournament is always the exact `strategy:` block above, so the
leaderboard's bottom line is "did my tuned config beat its own grid?".

---

## Scan more than BTC / ETH / SOL

`config/config.yaml` supports automatic asset discovery:

```yaml
assets: auto

asset_discovery:
  min_volume_inr: 1000000  # only pairs with >= Rs.10 lakh 24h turnover
  max_assets: 50           # top 50 by turnover; null/0 = ALL active markets
```

This discovers active CoinDCX INR spot markets (`ecode: I`) at startup,
sorted by approximate 24h INR turnover. Commands such as `check`, `run`,
`status`, `backtest`, `sweep`, and `sweep-live` all use the resolved list.

Useful commands:

```bash
python3 cryptobot.py assets              # list discovered assets
python3 cryptobot.py check               # run one signal check on all assets
python3 cryptobot.py run                 # run the hourly checker on all assets
python3 cryptobot.py --all-assets check  # force all assets even if config uses a list
```

To go back to the original small watchlist, replace `assets: auto` with:

```yaml
assets:
  - name: BTCINR
    pair: I-BTC_INR
  - name: ETHINR
    pair: I-ETH_INR
  - name: SOLINR
    pair: I-SOL_INR
```

---

## Why a run is fast (and what made it slow)

A run makes **1 candle request per asset**. Two knobs keep it quick:

- **Universe size** — `asset_discovery.max_assets: 50` + `min_volume_inr: 1000000`
  keep the scan to the liquid top-50. Setting `max_assets: null` scans ALL
  ~500+ pairs — that is 500+ API calls, which takes **minutes**, not seconds.
- **Parallel fetching** — `network.fetch_workers: 8` fetches 8 assets'
  candles concurrently (latency overlaps). Request STARTS are still paced
  globally by `public_request_interval_sec`, so CoinDCX is never hammered
  no matter how many workers you configure.

Typical `check` timing: top-50 universe ≈ **3–6 s**; all ~500 pairs ≈ **1–2 min**
(even parallel, the global pace of 20 req/s bounds it). `backtest`/`sweep`
fetch 30 days of candles per asset, so they take a bit longer either way.

### Trade-offs of the top-50 cap — and their safety nets

| Trade-off | Mitigation |
|---|---|
| An oversold coin outside the top-50 could be missed | **Dipped-market safety net** (`dipped_scan_pct: 8`): discovery already downloads 24h change/high/low for *every* market in one ticker call — any active INR market down >= 8% (or within 2% of its 24h low) is added to the scan at zero extra discovery cost. `0` disables; `dipped_scan_max: 150` bounds it. |
| Holding a pair that later falls out of the top-50 | Nothing to do — existing holdings are always merged into the scan list, so positions stay sellable. |
| Higher request pace (20/s) could annoy CoinDCX | **Adaptive backoff**: after any failed/429/5xx request the global pace auto-slows to 1/3 for ~10s (longer if the server sends `Retry-After`). Connections are pooled (keep-alive), not re-handshaked per request. |
| Long scans look frozen | Progress lines every 25 assets (`market data: 25/500 fetched ...`). |
| Dead/zero-liquidity books waste calls | Markets with no bid/ask/last price are skipped without orderbook/candle calls. |
| Two bot processes on the same `data/state/` can clobber each other's portfolio | Run ONE bot process per state dir (the GitHub Actions workflow already serialises runs via `concurrency:`). `save()` itself is atomic (temp file + rename), so a crash never corrupts the file. |

---

## Holding-period bots (week / month)

Every strategy has an optional time-based exit — `strategy.max_hold_hours`:
the bot sells a position once it has been held that many hours (`hold_timeout`),
whatever the price is doing. `0` (the default) keeps the old behaviour: hold
until take-profit / stop-loss / exit-RSI.

```yaml
strategy:
  max_hold_hours: 0     # 168 = hold-one-week bot, 720 = hold-one-month bot
```

The sweep tournament runs **500 bots** across **two strategy families** —
`dip` bots buy oversold dips (RSI <= entry_rsi), `momentum` bots buy
breakouts/strength (RSI >= entry_rsi) — with hold periods of hold-forever,
3 days, one week, two weeks and one month:

```yaml
sweep:
  accounts: 500
  entry_mode: ["dip", "momentum"]
  max_hold_hours: [0, 72, 168, 336, 720]
```

Account names carry the family and hold period (`dip_e30_x72_tp4_sl2.0_p40_h168`,
`mom_e55_x80_tp6_sl3.0_p60_h720`, ...); the leaderboard prints an `entry`
legend for the two families. Because the strategy grid changed, the first
`sweep-live` run after this change restarts all demo accounts at Rs.10,000 —
that is the normal grid-change wipe, not a bug. The wipe also triggers if
`sweep.accounts` changes; the grid fingerprint is stored in
`data/sweep/grid_signature.txt`.

`backtest` and `sweep` deliberately keep a **stable** universe (no dipped
extras, which flap hour to hour) so strategy comparisons stay comparable
across runs. Live checks (`check`/`run`/`sweep-live`/`status`) do include them,
so a crashing coin is never missed.

---

## Backtesting & the 500-bot tournament

**`backtest`** replays the current strategy bar-by-bar over `backtest.days` of
1h candles and prints it next to a **HODL benchmark** on the same universe and
the same starting cash. Signals come from bars *before* the fill bar, and fills
use the same fee/slippage/TDS math as the live paper broker — no lookahead.

**`sweep`** runs the whole grid over the same history in one shot (market data is
fetched once and shared by all 500 accounts, so the cost is the universe, not
the account count). It writes:

| File | Contents |
|---|---|
| `data/backtest/backtest_results.csv` | metric × strategy vs HODL |
| `data/backtest/backtest_equity.csv` | daily portfolio value of the strategy |
| `data/backtest/backtest_trades.csv` | every simulated entry/exit with RSI, price, exit reason, P&L |
| `data/sweep/results.csv` | 500-row leaderboard: parameters + win rate, profit factor, max drawdown, fees, TDS |
| `data/sweep/equity_top10.csv` | daily equity of the top 10 accounts vs HODL |
| `data/sweep/best_strategy.yaml` | a ready-to-use config from the winner |
| `data/sweep/accounts.csv` | the generated strategy grid (stable across runs) |
| `data/sweep/accounts/acc_XXX/` | per-account `portfolio.json` + `trades.csv` (live tournament) |
| `data/sweep/live_summary.csv` | the current live ranking, rewritten every `sweep-live` run |

`sweep-live` is the interesting one: the same 500 accounts, each with a unique
strategy, trading **live prices** in paper money on every cycle. `sweep-status`
just re-ranks them.

A caveat worth stating plainly: a 500-account grid is a machine for finding
overfitting. The winner of one 30-day window is a hypothesis, not an alpha —
`data/sweep/best_strategy.yaml` is a starting point for forward testing, not
something to size up on.

---

## Web dashboard (GitHub Pages)

The static site lives in **`web/`** and is deployed to GitHub Pages:

**→ https://clickalex.github.io/CrytpTrde/**

Two legacy workflows currently do the deploying (`.github/workflows/static.yml`
and `.github/workflows/jekyll-gh-pages.yml`). Both upload the **entire
repository** as the site, which publishes `src/`, `config/config.yaml` and
`data/` as public web content — and makes the two deployments race each other on
every push. `docs/ci/pages.yml` is a drop-in replacement that uploads `web/`
only; see [docs/ci/](docs/ci/README.md).

| Page (under `web/`) | What's on it |
|---|---|
| `index.html` | 🏆 Sweep tournament leaderboard — 500 strategies, P&L / win-rate / drawdown charts |
| `trades.html` | ⚡ Trades log: backtest trades, plus the **last trade** and **every fill** of the 500 live bots (with a streak analyzer, a 1% TDS calculator, and **🤖 Bot Details** / **🪙 Coin Details** drill-downs — pick a bot or a coin and see every buy/sell in detail) |
| `live.html` | 🟢 The 500 live demo accounts, ranked on current balances |
| `bot.html` | 🤖 **Bot Details** — a dedicated page for ONE bot (`bot.html?account=acc_001`): its strategy, rank, KPIs, open positions, full buy/sell history, and a cash & realized-P&L chart. Reachable from the nav, the Bot Details modal (↗ Full page), or any account row's inspector |
| `configs.html` | ⚙️ Every bot's parameter spec, with one-click `config.yaml` download |
| `compare.html` | ⚖️ Side-by-side strategy diffs + multi-line equity curves vs HODL |
| `analytics.html` | 📈 Monte Carlo risk & ruin runs, VaR, underwater drawdown curves, correlation heatmaps, strategy playground |
| `importer.html` | 📁 Drag-and-drop / paste any CSV or JSON and the same explorer runs on it |
| `health.html` | 🩺 **Data Health** — is the bot still running? Snapshot age, per-dataset row counts and payload sizes, staleness warnings, the next hourly run countdown, and the main paper portfolio |

`trades.html` has three switchable views — ⚡ **Backtest Trades**, 🟢 **Live
Trades** (every fill of the 500 live bots) and 🕐 **Last Trade / Bot** (the
single most recent fill per bot). The site navigation mirrors this: the ⚡
**Trades Log** entry carries the live-fill count and a dedicated 🕐 **Last
Trade** entry deep-links to `trades.html#view=last_trades`
(`#view=live_trades` and `#view=trades` work the same way).

Shared assets: `web/app.js` (filter/sort/paginate/export logic),
`web/styles.css` (dark + light theme, print styles), `web/data.js` (the
datasets). Chart.js loads from a CDN, so the site needs internet on first paint;
everything else is client-side with no server, no cookies, no account.

The grid views all support full-text search, column show/hide, row selection for
comparison, subtotals, CSV/JSON export, theme persistence, and a print-friendly
report.

**How the site gets its data:** `web/data.js` embeds snapshots of the CSVs and is
what every page renders. `scripts/build_data_js.py` regenerates ALL of
`web/data.js` from the bot's CSV outputs + heartbeat, and the hourly bot
workflow (`dca.yml`) re-runs it after every bot run and commits the result — so
every grid, the **"Last bot run" badge**, and the per-bot detail page refresh
automatically.

| Dataset in `data.js` | Source of truth |
|---|---|
| `sweep_results` | `data/sweep/results.csv` |
| `live_summary` | `data/sweep/live_summary.csv` |
| `accounts` | `data/sweep/accounts.csv` |
| `trades` | `data/backtest/backtest_trades.csv` |
| `backtest_results` | `data/backtest/backtest_results.csv` |
| `backtest_equity` | `data/backtest/backtest_equity.csv` |
| `top10_equity` | `data/sweep/equity_top10.csv` |
| `live_trades` | every `data/sweep/accounts/acc_XXX/trades.csv`, flattened |
| `last_trades` | same — the single latest fill per bot |
| `bot_status` | `data/state/last_run.json` — WHEN the bot last ran + how it went; shown as the header badge on every page |

All of the above are rebuilt by `scripts/build_data_js.py` (nothing is
hand-edited in `web/data.js` anymore). The **bot state** in the repo
(`data/state/portfolio.json`) is committed after every Actions run, and the
build script also copies it to `web/state/portfolio.json` so the deployed site
can poll it. `live.html`'s optional live polling (off / 5s / 10s / 30s)
refetches `state/portfolio.json` and never touches `web/data.js`. To refresh
everything locally:

```bash
python3 scripts/build_data_js.py            # rebuild ALL of web/data.js
python3 scripts/build_data_js.py --check    # dry-run: just print counts
```

To browse against your local run before pushing:

```bash
cd web && python3 -m http.server 8000   # then open http://localhost:8000
```

Note that `.github/workflows/jekyll-gh-pages.yml` is a second, redundant Pages
deployer (both trigger on pushes to `main`); `static.yml` is the one the site
actually needs, so the Jekyll workflow can be deleted.

---

## Running in the cloud (GitHub Actions)

`.github/workflows/dca.yml` runs the bot in GitHub's cloud on the hour — no
server, no cron, no secrets required in paper mode.

- **Schedule:** `30 * * * *` UTC = minute 00 IST (09:00, 10:00 … IST).
- **Default command:** `sweep-live` — the 500-bot tournament on real prices.
- **Manual runs:** Actions → *Run workflow* lets you pick `sweep-live`, `check`,
  `status`, `sweep` or `backtest`, and optionally tick **reset** to restart all
  demo accounts at ₹10,000. Handy from a phone.
- **State:** after each run it `git add -A`s and commits changed state back to the
  repo, which is also what redeploys the Pages site.
- **Quota:** private repos get 2,000 workflow minutes/month; this repo is
  **public**, so runs are unlimited. Otherwise drop the frequency or
  `sweep.accounts`.
- **Resilience:** when CoinDCX/Cloudflare blocks datacenter IPs, the run logs
  `CoinDCX unreachable this hour … will retry next run` and exits green rather
  than failing every hour.

Because it commits state back to the repo: **run exactly one bot process per
`data/state/` directory.** Don't leave a local `run` daemon going while Actions
is also writing (the workflow's `concurrency:` group serialises cloud runs
against each other, but not against your laptop).

---

## Tests & CI

```bash
python3 tests/test_speed_fix.py     # 15 offline tests, no network, no secrets
```

The suite stubs the CoinDCX client and covers the behaviour that is easy to
silently break: market-cache building, global rate limiting, parallel history
fetching, discovery caps, the dipped-market safety net, adaptive backoff,
dead-market skipping, progress lines, a full `run_cycle` smoke test (entry →
take-profit → hold-timeout exits), `PaperBroker` buy/partial-sell/exit
accounting + persistence, and the `bot`/`coin` drill-down reports (per-fill PnL
attribution, offline).

`.github/workflows/tests.yml` runs it on every push and pull request, so a
regression — a broken exit rule, a crashed simulator — fails before it reaches
`main`. It is independent of `dca.yml`, so a red test never stops the hourly bot.

Drop-in variants of both workflows live in `docs/ci/` (that directory exists
because workflow files once couldn't be written from the sandbox; they are already
in place under `.github/workflows/`).

---

## Repo layout & data files

```
cryptobot.py            thin CLI launcher — keeps the familiar command working
src/cryptobot/          the actual bot, split into focused modules
  __init__.py           package exports + convenience aliases
  paths.py              repo / config / data / web path constants
  indicators.py         RSI / SMA math
  strategy.py           entry/exit signal rules
  coindcx.py            CoinDCX REST client
  broker.py             PaperBroker: fills, fees, 1% TDS, persistence
  engine.py             load_cfg, market-data cache, run_cycle
  backtest.py           historical replay + HODL benchmark
  sweep.py              strategy grid, tournament sim, live tournament
  bot.py                CLI commands + main()

config/config.yaml      every knob; paper by default
requirements.txt        requests + PyYAML (matplotlib optional)
tests/test_speed_fix.py offline test suite (15 tests)
scripts/build_data_js.py regenerates ALL of web/data.js from CSVs + heartbeat

web/                    the static dashboard: deployed as the Pages site root
  index.html live.html trades.html configs.html
  compare.html analytics.html importer.html bot.html health.html
  app.js styles.css data.js     dashboard logic, styling, embedded datasets
  state/portfolio.json          live-poller copy (generated by build_data_js.py)

data/
  state/                the LIVE bot's state: portfolio.json + trades.csv
                          + last_run.json (heartbeat: when the bot last ran,
                            which command, ok/skipped/error — feeds the
                            "Last bot run" badge on the dashboard)
  backtest/             backtest_results.csv, backtest_equity.csv,
                          backtest_trades.csv   outputs of the last `backtest`
  sweep/                tournament outputs
    accounts.csv          the generated 500-strategy grid
    results.csv           leaderboard from the last offline `sweep`
    equity_top10.csv      top-10 equity curves vs HODL
    live_summary.csv      current live ranking (rewritten every `sweep-live`)
    best_strategy.yaml    the winner, as a ready-to-use --config file
    grid_signature.txt    grid fingerprint; a change wipes & rebuilds accounts
    accounts/acc_001…500/ each demo account's portfolio.json + trades.csv

.github/workflows/      dca.yml (hourly bot) · tests.yml · static.yml · jekyll…
docs/ci/                workflow drop-ins + notes
```

State is plain JSON/CSV on purpose: it is committed by the Actions workflow, it
survives any Python change, and you can audit or hand-edit it with any text
editor. `trades.csv` is the append-only audit log that `status` uses for its P&L
and tax rollup; `portfolio.json` is the current balances and open positions.

Start over anytime:

```bash
python3 cryptobot.py reset --yes      # live bot state
python3 cryptobot.py sweep-live --reset   # all 500 demo accounts
```

---

## India tax notes

The simulator is built for Indian crypto tax mechanics, because a paper P&L that
ignores them is a flattering fiction:

- **1% TDS** (`simulate_tds`, `tds_rate`) is deducted on every sell — including
  loss-making sells — so it is modelled in the fills and reported separately.
- **30% flat tax on gains**, with **no loss offsetting**: `status` sums gross
  gains, applies 30%, lists gross losses as non-offsettable, and shows TDS paid
  as a credit against that estimate.
- Amounts respect the exchange's tick step / precision and the ₹100 minimum
  notional, so simulated quantities are ones you could actually submit.

The numbers printed by `status` are an **estimate for paper trading, not tax
advice** — real reporting needs your complete exchange history (including coins
bought outside this bot) and a professional's read of the current rules.

---

## Going live (read before touching `live:`)

**Today this bot is paper-only.** Setting `live.enabled: true` (plus API-key
secrets) only switches the API client to authenticated endpoints — **no real
orders are placed by `check`/`run`**; the PaperBroker still simulates every
fill. The low-level `create_market_order` helper exists in
`src/cryptobot/coindcx.py` but is intentionally not wired into any command. Treat the `live:` config
block as scaffolding for a future, carefully-reviewed feature: if you want
real trading, verify order placement with a tiny manual order first and
never let a bot you haven't watched trade size.

---

## Disclaimer

Educational software. Crypto markets are volatile, exchanges can delist pairs or
halt withdrawals, and a strategy that won last month can lose next month.
Nothing in this repository is financial, investment, or tax advice, and no result
produced here is a promise of future returns. Trade only what you can afford to
lose — and if this bot ever does place real orders, that is on you.
