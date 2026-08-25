# CrytpTrde

CoinDCX paper-trading bot with an RSI swing strategy.

> Branch note: `arena/01a0392d-crytptrde` is an Arena session workspace branch opened against `main`.

## Scan more than BTC / ETH / SOL

`config.yaml` now supports automatic asset discovery:

```yaml
assets: auto

asset_discovery:
  min_volume_inr: 0      # set e.g. 100000 to skip illiquid pairs
  max_assets: null       # null/0 = all active INR spot markets
```

This discovers every active CoinDCX INR spot market (`ecode: I`) at startup,
sorted by approximate 24h INR turnover. Commands such as `check`, `run`,
`status`, `backtest`, `sweep`, and `sweep-live` all use the resolved list.

Useful commands:

```bash
python3 cryptobot.py assets              # list discovered assets
python3 cryptobot.py check               # run one signal check on all assets
python3 cryptobot.py run                 # run the hourly checker on all assets
python3 cryptobot.py --all-assets check  # force all assets even if config uses a list
```

If scanning all markets is too slow or hits public API limits, either set
`asset_discovery.max_assets` to a top-N value, raise
`public_request_interval_sec`, or set `min_volume_inr` to skip small pairs.
