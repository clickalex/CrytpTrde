"""Engine: config loading, market-data cache, and the live decision loop."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import paths
from .broker import PaperBroker
from .coindcx import (CoinDCX, CoinDCXError, env_keys, qty_from_inr)
from .strategy import (entry_signal, exit_reason, position_amount,
                         position_hold_hours, rsi_value)

IST = ZoneInfo("Asia/Kolkata")


# ------------------------------------------------------------------ construction
def load_cfg(path: Path) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def make_broker(cfg: dict, state_dir: Path | None = None) -> PaperBroker:
    max_trades = cfg.get("max_trades", 100)
    target_dir = Path(state_dir) if state_dir else paths.STATE_DIR
    if paths.ACCOUNTS_DIR in target_dir.parents or target_dir == paths.ACCOUNTS_DIR:
        sweep_cfg = cfg.get("sweep") or {}
        if "max_trades" in sweep_cfg:
            max_trades = sweep_cfg.get("max_trades")
    elif "sweep" in cfg and isinstance(cfg["sweep"], dict) and "max_trades" in cfg["sweep"] and "max_trades" not in cfg:
        max_trades = cfg["sweep"]["max_trades"]

    try:
        max_trades = int(max_trades) if max_trades is not None else 100
    except (TypeError, ValueError):
        max_trades = 100

    return PaperBroker(
        state_dir=target_dir,
        cash=float(cfg.get("initial_cash_inr", 50000)),
        fee_rate=float(cfg.get("fee_rate", 0.001)),
        slippage_bps=float(cfg.get("slippage_bps", 5)),
        tds_rate=float(cfg.get("tds_rate", 0.01)),
        simulate_tds=bool(cfg.get("simulate_tds", True)),
        max_trades=max_trades,
    )


def public_request_interval(cfg: dict) -> float:
    return max(0.0, float(cfg.get("public_request_interval_sec", 0.0) or 0.0))


def fetch_workers(cfg: dict) -> int:
    """How many assets to fetch market data for in parallel (network.fetch_workers)."""
    try:
        w = int((cfg.get("network") or {}).get("fetch_workers", 8) or 8)
    except (TypeError, ValueError):
        w = 8
    return max(1, min(w, 32))


def _run_parallel(fn, items: list, workers: int,
                  progress_label: str | None = None,
                  progress_every: int = 25) -> list:
    """Map `fn` over `items`, in parallel when useful, preserving order.

    With `progress_label`, prints e.g. "  market data: 40/500 fetched ..."
    every `progress_every` completions so long scans show life."""
    def run_one(i):
        return i, fn(items[i])

    results: list = [None] * len(items)
    done = 0

    def tick():
        nonlocal done
        done += 1
        if progress_label and done % progress_every == 0 and done < len(items):
            print(f"  {progress_label}: {done}/{len(items)} fetched ...")

    if workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
            for i, res in pool.map(run_one, range(len(items))):
                results[i] = res
                tick()
    else:
        for i, item in enumerate(items):
            results[i] = fn(item)
            tick()
    return results


def make_public_coin(cfg: dict) -> CoinDCX:
    """Public-market-data client. Never requires live API keys."""
    return CoinDCX(timeout=30, request_interval=public_request_interval(cfg))


def make_coin(cfg: dict) -> CoinDCX:
    interval = public_request_interval(cfg)
    if cfg.get("live", {}).get("enabled"):
        k, s = env_keys(cfg["live"]["api_key_env"], cfg["live"]["api_secret_env"])
        return CoinDCX(api_key=k, api_secret=s, timeout=20,
                       request_interval=interval)
    return CoinDCX(timeout=20, request_interval=interval)


def ensure_initialised(cfg: dict, broker: PaperBroker) -> None:
    """If no paper state exists yet (e.g. first run in the cloud), create it."""
    if broker.file.exists():
        return
    print(f"No paper state found ({broker.file.parent.name}) â initialising a "
          f"fresh paper portfolio first.")
    broker.cash = float(cfg["initial_cash_inr"])
    broker.positions = {}
    broker.save()


def resolve_assets(cfg: dict, force_all: bool = False,
                   extra_state_dirs: list[Path] | None = None,
                   include_dipped: bool = True) -> list[dict]:
    """Resolve config assets into the explicit ``[{name, pair}]`` list.

    ``assets: auto`` (or ``--all-assets``) discovers every active INR spot
    market rather than only BTC/ETH/SOL. Existing holdings are always merged in
    so a discovered universe change never leaves an open position unsellable.
    With ``include_dipped`` (default), markets outside a top-N cap that fell
    hard in the last 24h are also added - see CoinDCX.dipped_inr_markets.
    """
    configured = cfg.get("assets")
    assets_cfg = cfg.get("asset_discovery") or {}
    explicit_assets: list[dict] = []
    names: set[str] = set()

    def add_asset(a: dict, allow_missing_pair: bool = False) -> None:
        if not isinstance(a, dict):
            return
        name = str(a.get("name") or "").upper()
        pair = str(a.get("pair") or "")
        if not name or (not pair and not allow_missing_pair):
            return
        if name not in names:
            names.add(name)
            explicit_assets.append({"name": name, "pair": pair})

    if configured == "auto" or force_all or assets_cfg.get("enabled") is True:
        coin = make_public_coin(cfg)
        min_volume = float(assets_cfg.get("min_volume_inr", 0.0) or 0.0)
        limit = assets_cfg.get("max_assets")
        limit = int(limit) if limit not in (None, "", 0, "0") else None
        discovered = coin.discover_inr_markets(min_volume_inr=min_volume, limit=limit)
        for a in discovered:
            add_asset(a)
        # Safety net for the liquidity cap: also catch sharply-dipped markets
        # outside it (cheap - reuses the discovery ticker call). Skipped for
        # --all-assets (everything already scanned) and for explicit lists.
        if include_dipped and not force_all:
            dip_pct = float(assets_cfg.get("dipped_scan_pct", 8) or 0)
            if dip_pct > 0:
                try:
                    dip_max = int(assets_cfg.get("dipped_scan_max", 150) or 0)
                except (TypeError, ValueError):
                    dip_max = 150
                dipped = coin.dipped_inr_markets(dip_pct=dip_pct, limit=dip_max,
                                                 exclude=set(names))
                for a in dipped:
                    add_asset(a)
                if dipped:
                    print(f"+{len(dipped)} sharply-dipped market(s) added to the scan "
                          f"(24h drop <= -{dip_pct:g}% or near the 24h low).")
    elif isinstance(configured, list):
        for a in configured:
            add_asset(a)
    else:
        raise ValueError("config 'assets' must be a list of assets or 'auto'")

    state_dirs = [paths.STATE_DIR]
    if extra_state_dirs:
        state_dirs.extend(Path(p) for p in extra_state_dirs)
    for state_dir in state_dirs:
        portfolio = state_dir / "portfolio.json"
        if not portfolio.exists():
            continue
        try:
            data = json.loads(portfolio.read_text())
            for name in (data.get("holdings") or {}):
                add_asset({"name": name, "pair": ""}, allow_missing_pair=True)
        except (OSError, ValueError, TypeError):
            continue

    if not explicit_assets:
        raise ValueError("No assets resolved. Check CoinDCX connectivity or config.")
    return explicit_assets


def runtime_assets(cfg: dict, broker: PaperBroker) -> list[dict]:
    """Configured assets plus any asset currently held by this broker."""
    assets = list(cfg.get("assets") or [])
    names = {a["name"] for a in assets if isinstance(a, dict)}
    for name, pos in broker.positions.items():
        if pos.qty > 0 and name not in names:
            pair = next((a.get("pair", "") for a in assets
                         if isinstance(a, dict) and a.get("name") == name), "")
            assets.append({"name": name, "pair": pair})
    return assets


# ----------------------------------------------------------------- market data
def build_market_cache(cfg: dict, coin: CoinDCX,
                       assets: list[dict] | None = None) -> dict[str, tuple]:
    """Fetch candles + bid/ask ONCE per asset so a sweep of many accounts uses
    identical market data (fair comparison) with minimal API calls.

    Assets are fetched in PARALLEL (see network.fetch_workers). The CoinDCX
    client still paces request starts to `public_request_interval_sec`
    globally across all workers, so the exchange is never hammered.

    Returns {market_name: (candles, bid, ask)} for every healthy asset.
    Assets with no live quote ("dead market") or failing candle fetches are
    reported on stdout and left OUT of the dict - callers treat a missing
    name as "retry next check", never as an error.
    """
    s = cfg["strategy"]
    assets = assets or cfg["assets"]

    # Tickers give bid/ask for every market in one request. This is important
    # when scanning all INR markets: one ticker call is much safer than one
    # order-book request per asset.
    tickers = coin.tickers(refresh=True)

    def fetch_one(a: dict):
        name = a["name"]
        pair = a.get("pair")
        if not pair:
            return None
        try:
            t = tickers.get(name, {})
            try:
                bid = float(t.get("bid") or 0.0)
                ask = float(t.get("ask") or 0.0)
                last = float(t.get("last_price") or 0.0)
            except (TypeError, ValueError):
                bid = ask = last = 0.0
            if bid <= 0 or ask <= 0:
                if last <= 0:
                    # dead book: avoid the orderbook+ticker-refetch fallback
                    return name, None, "no live quote (dead market) - skipped"
                bid, ask = coin.best_bid_ask(pair)
        except (CoinDCXError, KeyError, ValueError, IndexError) as exc:
            return name, None, f"quote data failed ({exc})"
        try:
            candles = coin.candles(pair,
                                   interval=s.get("timeframe", "1h"),
                                   limit=int(s.get("signal_lookback", 200)))
            return name, (candles, bid, ask), None
        except (CoinDCXError, KeyError, ValueError, IndexError) as exc:
            return name, None, f"candle data failed ({exc})"

    label = "market data" if len(assets) > 25 else None
    cache: dict[str, tuple] = {}
    for res in _run_parallel(fetch_one, assets, fetch_workers(cfg),
                             progress_label=label):
        if res is None:
            continue
        name, payload, err = res
        if err is not None:
            print(f"  {name}: {err}")
            continue
        cache[name] = payload
    return cache


# --------------------------------------------------------------- trading cycle
def run_cycle(cfg: dict, broker: PaperBroker, coin: CoinDCX,
              market_cache: dict | None = None, verbose: bool = True) -> None:
    """One signal-check cycle. Trades ONLY when a condition is met:
       - BUY  : 1h RSI <= entry_rsi and no open position
       - SELL : take-profit / stop-loss / RSI >= exit_rsi

    Timing rules (identical to the backtest/sweep sims): the signal comes
    from the LAST CLOSED candles, fills happen at the CURRENT bid/ask.
    Buys deploy position_size_pct of available cash (fee-inclusive, same
    formula as PaperBroker.buy); sells flatten the whole position. Every
    asset ends up in exactly one of: bought / sold / held / no_entry /
    low_cash / errors - printed as a one-line summary for large universes.
    """
    ensure_initialised(cfg, broker)
    s = cfg["strategy"]
    assets = runtime_assets(cfg, broker)
    if market_cache is None:
        market_cache = build_market_cache(cfg, coin, assets)

    show_all = verbose and len(assets) <= 20
    counts = {"bought": 0, "sold": 0, "held": 0, "no_entry": 0,
              "errors": 0, "low_cash": 0}
    if verbose:
        print(f"[{datetime.now(IST):%Y-%m-%d %H:%M IST}] Signal check "
              f"({len(assets)} assets; entry RSI <= {s.get('entry_rsi', 30)}, "
              f"exit RSI >= {s.get('exit_rsi', 999)})")

    for a in assets:
        name = a["name"]
        pair = a.get("pair") or coin.market(name).get("pair", "")
        if not pair and name not in market_cache:
            if show_all:
                print(f"  {name}: market metadata unavailable - will retry next check")
            counts["errors"] += 1
            continue

        md = coin.market(name)
        step = md.get("step", 1e-6) or 1e-6
        prec = md.get("target_currency_precision", 6)
        min_notional = float(md.get("min_notional", 100))

        if name in market_cache:
            candles, bid, ask = market_cache[name]
        else:
            try:
                candles = coin.candles(pair, interval=s.get("timeframe", "1h"),
                                       limit=int(s.get("signal_lookback", 200)))
                t = coin.tickers().get(name, {})
                bid = float(t.get("bid") or 0.0)
                ask = float(t.get("ask") or 0.0)
                if bid <= 0 or ask <= 0:
                    bid, ask = coin.best_bid_ask(pair)
            except (CoinDCXError, KeyError, ValueError, IndexError) as exc:
                if show_all:
                    print(f"  {name}: market data failed ({exc}) - will retry next check")
                counts["errors"] += 1
                continue

        rsi_val = rsi_value(candles, int(s.get("rsi_period", 14)))
        rsi_text = f"{rsi_val:.1f}" if rsi_val is not None else "n/a"
        pos = broker.positions.get(name)

        if pos and pos.qty > 0:
            reason = exit_reason(rsi_val, bid, pos.avg_cost, s,
                                 position_hold_hours(pos))
            if reason:
                res = broker.sell(name, pos.qty, bid, step=step, precision=prec)
                if res["ok"]:
                    counts["sold"] += 1
                    if verbose:
                        print(f"  SELL ALL {name} ({reason}) @ Rs.{res['price']:,.2f}"
                              f" x {res['qty']:.8f} -> Rs.{res['notional']:,.2f}"
                              f" (fee Rs.{res['fee']:.2f}, TDS Rs.{res['tds']:.2f})")
                else:
                    counts["errors"] += 1
                    if verbose:
                        print(f"  {name}: sell failed - {res['reason']}")
            else:
                counts["held"] += 1
                if show_all:
                    pnl_pct = (bid / pos.avg_cost - 1) * 100 if pos.avg_cost else 0.0
                    print(f"  {name}: HOLD {pos.qty:.8f} @ Rs.{pos.avg_cost:,.2f} "
                          f"(now Rs.{bid:,.2f}, {pnl_pct:+.1f}%, RSI {rsi_text})")
            continue

        if entry_signal(rsi_val, s):
            amount = position_amount(broker.cash, s)
            if amount < float(s.get("min_buy_inr", 500)):
                counts["low_cash"] += 1
                if show_all:
                    print(f"  {name}: BUY condition MET (RSI {rsi_text} <= "
                          f"{s.get('entry_rsi')}) but Rs.{amount:,.0f} < min_buy_inr - "
                          f"top up paper cash or lower min_buy_inr.")
                continue
            res = broker.buy(name, amount, ask, step=step, precision=prec,
                             min_notional=min_notional)
            if res["ok"]:
                counts["bought"] += 1
                if verbose:
                    print(f"  BUY {name} Rs.{res['notional']:,.2f} @ Rs.{res['price']:,.2f}"
                          f" x {res['qty']:.8f} (fee Rs.{res['fee']:.2f}) | "
                          f"RSI {rsi_text} <= {s.get('entry_rsi')}")
            else:
                counts["errors"] += 1
                if show_all:
                    print(f"  {name}: BUY skipped - {res['reason']}")
        else:
            counts["no_entry"] += 1
            if show_all:
                print(f"  {name}: no position, RSI {rsi_text} - no entry "
                      f"(need <= {s.get('entry_rsi')} to buy)")

    if verbose and not show_all:
        print(f"  summary: bought {counts['bought']}, sold {counts['sold']}, "
              f"held {counts['held']}, no-entry {counts['no_entry']}, "
              f"low-cash {counts['low_cash']}, errors {counts['errors']}")
    if verbose:
        print("Check done.")


# -------------------------------------------------------------- HODL benchmark
def hodl_benchmark(cfg: dict, coin: CoinDCX, assets: list[dict],
                   candles: dict[str, list[dict]],
                   start_cash: float | None = None) -> dict:
    """Same starting cash, split EQUAL-WEIGHT across all assets at the first
    candle, held to the end (valued at each asset's last close). Assets that
    have no candle at the very first timestamp (e.g. listed later) are
    skipped from the benchmark, so it never looks better than it should.
    No TDS is charged - HODL never sells, and TDS is only owed on disposal.
    """
    cash = start_cash if start_cash is not None else cfg["backtest"]["start_cash_inr"]
    start = cash
    invested = 0.0
    if not candles or not any(candles.values()):
        # nothing fetched (e.g. total API outage) - report a flat benchmark
        # instead of crashing on min() of an empty sequence
        return {"final_value": cash, "invested": 0.0, "pnl": 0.0, "pnl_pct": 0.0}
    first_ts = min(c[0]["time"] for c in candles.values() if c)
    for a in assets:
        cls = candles[a["name"]]
        first = next((c for c in cls if c["time"] == first_ts), None)
        if first is None:
            continue
        md = coin.market(a["name"])
        step = md.get("step", 1e-6) or 1e-6
        prec = md.get("target_currency_precision", 6)
        fill = first["open"] * (1 + cfg["backtest"]["slippage_bps"] / 10_000.0)
        # equal-weight split, computed ONCE - inside the loop `cash` already
        # includes earlier assets' final values, which skewed later shares
        share = start / len(assets)
        qty = qty_from_inr(share, fill, step, prec)
        notional = qty * fill
        fee = notional * cfg["backtest"]["fee_rate"]
        if notional + fee <= cash and notional >= 100:
            cash -= notional + fee
            invested += notional + fee
            cash += qty * cls[-1]["close"]  # value at the end, same valuation basis
    return {"final_value": cash, "invested": invested,
            "pnl": cash - start,
            "pnl_pct": (cash / start - 1) * 100}


def fetch_hours(coin: CoinDCX, pair: str, days: int) -> list[dict]:
    """Fetch hourly candles going back `days`, paging the API by ~990h chunks.

    Each page requests [end - 990h, end]; the next page ends just before the
    oldest candle received, so pages walk backwards to `start_ms` without
    overlap. Candles are de-duplicated by timestamp and returned oldest
    first. Stops early on API error or an empty page.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    window = 990 * 3600 * 1000
    out: dict[int, dict] = {}
    end = now_ms
    while end > start_ms:
        begin = max(start_ms, end - window)
        try:
            chunk = coin.candles(pair, interval="1h", start_ms=begin, end_ms=end,
                                 limit=1000)
        except CoinDCXError as exc:
            print(f"    (fetch stopped: {exc})")
            break
        if not chunk:
            break
        for c in chunk:
            out[c["time"]] = c
        oldest = chunk[0]["time"]
        if oldest >= end:
            break
        end = oldest - 1
    return sorted(out.values(), key=lambda c: c["time"])


def fetch_history(cfg: dict, coin: CoinDCX, assets: list[dict],
                  days: int) -> dict[str, list[dict]]:
    """Fetch `days` of 1h history per asset (in PARALLEL, see network.fetch_workers),
    shared by every account/backtest so the data — and the comparison — is fair."""
    def fetch_one(a: dict):
        try:
            return a["name"], fetch_hours(coin, a["pair"], days), None
        except Exception as exc:  # noqa: BLE001
            return a["name"], None, exc

    candles: dict[str, list[dict]] = {}
    label = "history" if len(assets) > 25 else None
    for name, cls, exc in _run_parallel(fetch_one, assets, fetch_workers(cfg),
                                        progress_label=label):
        if exc is not None:
            print(f"  {name}: SKIPPED ({exc})")
            continue
        candles[name] = cls
        last = cls[-1]["close"] if cls else float("nan")
        print(f"  {name}: {len(cls)} hourly candles  (last close Rs.{last:,.2f})")
    return candles
