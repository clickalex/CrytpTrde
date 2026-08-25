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

