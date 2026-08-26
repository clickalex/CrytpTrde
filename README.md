# CrytpTrde

CoinDCX paper-trading bot with an RSI swing strategy.

> Branch note: `arena/01a0392d-crytptrde` is an Arena session workspace branch opened against `main`.

## Scan more than BTC / ETH / SOL

`config.yaml` now supports automatic asset discovery:

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
| Two bot processes on the same `state/` can clobber each other's portfolio | Run ONE bot process per state dir (the GitHub Actions workflow already serialises runs via `concurrency:`). `save()` itself is atomic (temp file + rename), so a crash never corrupts the file. |

## Holding-period bots (week / month)

Every strategy now has an optional time-based exit — `strategy.max_hold_hours`:
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
that is the normal grid-change wipe, not a bug.

`backtest` and `sweep` deliberately keep a **stable** universe (no dipped
extras, which flap hour to hour) so strategy comparisons stay comparable
across runs.

