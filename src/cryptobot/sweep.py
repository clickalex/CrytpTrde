"""Strategy grid, historical tournament simulation, and live tournament."""

import copy
import csv
import hashlib
import itertools
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import paths
from .coindcx import qty_from_inr
from .engine import (build_market_cache, fetch_history, hodl_benchmark,
                     make_broker, make_coin, run_cycle)
from .indicators import rsi_series
from .strategy import entry_signal, exit_reason, position_amount

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_GRID = {
    # two strategy families: dip (buy oversold) and momentum (buy strength)
    "entry_mode": ["dip", "momentum"],
    "entry_rsi": [15, 18, 22, 25, 28, 30, 33, 36, 40, 45],
    "exit_rsi": [60, 64, 68, 72, 76, 80, 85],
    "take_profit_pct": [2, 3, 4, 5, 6, 8, 10, 12, 15],
    "stop_loss_pct": [1, 1.5, 2, 3, 5],
    "position_size_pct": [10, 20, 30, 40, 50, 60, 80],
    # holding-period bots: 0 = hold until tp/sl/exit RSI, 72 = 3 days,
    # 168 = one week, 336 = two weeks, 720 = ~a month
    "max_hold_hours": [0, 72, 168, 336, 720],
}
PARAM_KEYS = list(DEFAULT_GRID.keys())


def grid_signature(cfg: dict) -> str:
    """Fingerprint of the strategy grid + baseline. If it changes, the mapping
    account->strategy changes, so demo accounts must restart fresh."""
    s = cfg.get("sweep") or {}
    base = cfg["strategy"]
    payload = {
        "grid": {k: list(s.get(k, DEFAULT_GRID[k])) for k in PARAM_KEYS},
        "base": {k: base.get(k) for k in PARAM_KEYS},
        "start_cash_inr": s.get("start_cash_inr", 10000),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def sweep_start_cash(cfg: dict) -> float:
    """Starting paper balance for EVERY demo account (default â¹10,000)."""
    s = cfg.get("sweep") or {}
    return float(s.get("start_cash_inr", 10000))


# ------------------------------------------------------------------- the grid
def account_grid(cfg: dict, count: int | None = None) -> list[dict]:
    """Deterministically build `count` strategy configs spread over the grid.
    Row 0 is always the baseline (values from config/config.yaml -> strategy)."""
    s = cfg.get("sweep") or {}
    count = count or int(s.get("accounts", 200))
    count = max(1, count)
    grid = {k: list(s.get(k, DEFAULT_GRID[k])) for k in PARAM_KEYS}
    base = cfg["strategy"]

    combos = list(itertools.product(*(grid[k] for k in PARAM_KEYS)))
    chosen: list[tuple] = []
    if count == 1:
        chosen = [combos[0]]
    elif len(combos) >= count:
        # sample `count` combos EVENLY across the whole product grid:
        # index j walks from 0 to len-1 in equal strides, so the chosen
        # strategies cover every corner of the grid, not just one region
        for i in range(count):
            j = round(i * (len(combos) - 1) / (count - 1))
            chosen.append(combos[j])
    else:
        chosen = list(combos)
        cyc = itertools.cycle(combos)
        while len(chosen) < count:
            chosen.append(next(cyc))
    # ensure uniqueness (dedupe keeping order)
    seen: set[tuple] = set()
    unique = [c for c in chosen if not (c in seen or seen.add(c))]
    while len(unique) < count:
        unique.append(unique[len(unique) % len(unique)])
    unique = unique[:count]

    # row 0 = baseline exactly as configured. Numeric params are compared as
    # floats; string params (entry_mode) as-is. If the strategy section is
    # missing a parameter entirely, keep combos[0] rather than crashing -
    # the grid still fills `count` rows.
    if all(isinstance(base.get(k), (int, float, str))
           and not isinstance(base.get(k), bool) for k in PARAM_KEYS):
        baseline = tuple(base[k] if isinstance(base[k], str) else float(base[k])
                         for k in PARAM_KEYS)
        if unique[0] != baseline:
            unique[0] = baseline
    unique = list(dict.fromkeys(unique))  # guarantee: no duplicate strategies
    # The dedupe above can SHRINK the list (the baseline may already sit in the
    # grid, and the padding path repeats combos). Returning fewer than `count`
    # rows is never acceptable: live_sweep compares len(rows) to the configured
    # account count and would wipe the whole tournament again on EVERY run.
    if len(unique) < count:
        pool = [c for c in chosen if c not in set(unique)] or chosen
        unique.extend(itertools.islice(itertools.cycle(pool), count - len(unique)))
    unique = unique[:count]

    rows = []
    for i, combo in enumerate(unique):
        params = dict(zip(PARAM_KEYS, combo))
        params["account"] = f"acc_{i + 1:03d}"
        params["name"] = (f"{params['entry_mode'][:3]}_e{params['entry_rsi']:.0f}"
                          f"_x{params['exit_rsi']:.0f}"
                          f"_tp{params['take_profit_pct']:.0f}"
                          f"_sl{params['stop_loss_pct']:.1f}"
                          f"_p{params['position_size_pct']:.0f}"
                          f"_h{params['max_hold_hours']:.0f}")
        rows.append(params)
    return rows


def apply_overrides(cfg: dict, row: dict) -> dict:
    """Copy of cfg with the account's strategy parameters merged in."""
    out = copy.deepcopy(cfg)
    for k in PARAM_KEYS:
        out["strategy"][k] = row[k]
    return out


def save_account_rows(rows: list[dict]) -> Path:
    paths.SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(paths.ACCOUNT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["account", "name"] + PARAM_KEYS)
        w.writeheader()
        w.writerows(rows)
    return paths.ACCOUNT_CSV


def load_account_rows() -> list[dict]:
    """Read data/sweep/accounts.csv, restoring numeric types for numeric params.
    String params (entry_mode) are kept as-is - float() would crash on them.

    Older CSVs (before the dip/momentum grid) may omit columns such as
    entry_mode. Missing keys used to raise KeyError and kill GitHub Actions
    with only 'Process completed with exit code 1'.
    """
    with open(paths.ACCOUNT_CSV) as fh:
        rows = []
        for r in csv.DictReader(fh):
            for k in PARAM_KEYS:
                raw = r.get(k, "")
                if raw in (None, ""):
                    r[k] = DEFAULT_GRID[k][0]
                    continue
                try:
                    r[k] = float(raw)
                except (TypeError, ValueError):
                    r[k] = raw  # not numeric (entry_mode) - keep the string
            rows.append(r)
        return rows


# --------------------------------------------------- fast historical sim (exact)
def _sim_account(cfg: dict, row: dict, assets: list[dict],
                 candles: dict[str, list[dict]],
                 meta: dict[str, dict]) -> dict:
    """Replay one strategy on the shared candle history. Rules are IDENTICAL to
    engine.run_cycle / backtest._simulate: signal from bars BEFORE the current
    bar, execute at the current bar's OPEN, fees + slippage + 1% TDS."""
    strat = {**cfg["strategy"], **{k: row[k] for k in PARAM_KEYS}}
    fee = cfg["backtest"]["fee_rate"]
    slip = cfg["backtest"]["slippage_bps"] / 10_000.0
    tds = cfg["backtest"]["tds_rate"]
    start_cash = sweep_start_cash(cfg)
    period = int(strat.get("rsi_period", 14))
    min_buy = float(strat.get("min_buy_inr", 500))

    # precompute per-asset arrays for O(1) signal lookup
    times: dict[str, list[int]] = {}
    idx_by_time: dict[str, dict[int, int]] = {}
    rs: dict[str, list] = {}
    bar_by_time: dict[str, dict[int, dict]] = {}
    last_close: dict[str, float | None] = {a["name"]: None for a in assets}
    for a in assets:
        cls = candles[a["name"]]
        t = [c["time"] for c in cls]
        times[a["name"]] = t
        idx_by_time[a["name"]] = {c["time"]: i for i, c in enumerate(cls)}
        rs[a["name"]] = rsi_series([c["close"] for c in cls], period)
        bar_by_time[a["name"]] = {c["time"]: c for c in cls}

    cash = start_cash
    holdings = {a["name"]: 0.0 for a in assets}
    entry_cost = {a["name"]: 0.0 for a in assets}
    open_trade: dict[str, dict] = {}
    trades: list[dict] = []
    fees_paid = tds_paid = 0.0

    all_ts = sorted({c["time"] for a in assets for c in candles[a["name"]]})
    equity: list[tuple[datetime, float]] = []

    for ts in all_ts:
        for a in assets:
            bar = bar_by_time[a["name"]].get(ts)
            if bar is None:
                continue
            last_close[a["name"]] = bar["close"]
            i = idx_by_time[a["name"]][ts]
            rsi_prev = rs[a["name"]][i - 1] if i > 0 else None
            o = bar["open"]
            md = meta[a["name"]]
            step = md.get("step", 1e-6) or 1e-6
            prec = md.get("target_currency_precision", 6)
            min_notional = float(md.get("min_notional", 100))

            if holdings[a["name"]] > 0:
                held_hours = ((ts - open_trade[a["name"]]["ts"]) / 3_600_000.0
                              if a["name"] in open_trade else None)
                reason = exit_reason(rsi_prev, o, entry_cost[a["name"]], strat,
                                     held_hours)
                if reason:
                    qty = holdings[a["name"]]
                    fill = o * (1 - slip)
                    notional = qty * fill
                    fe = notional * fee
                    td = notional * tds
                    cost_basis = qty * entry_cost[a["name"]]
                    cash += notional - fe - td
                    pnl = (notional - fe) - cost_basis
                    trades.append({"asset": a["name"], "entry": open_trade[a["name"]],
                                   "exit_ts": ts, "exit": fill, "reason": reason,
                                   "pnl": pnl,
                                   "pnl_pct": (fill / entry_cost[a["name"]] - 1) * 100})
                    fees_paid += fe
                    tds_paid += td
                    del open_trade[a["name"]]
                    holdings[a["name"]] = 0.0
                    entry_cost[a["name"]] = 0.0
            else:
                if entry_signal(rsi_prev, strat):
                    amount = position_amount(cash, strat)
                    if amount >= max(min_buy, min_notional):
                        fill = o * (1 + slip)
                        # fee-inclusive sizing, identical to PaperBroker.buy
                        qty = qty_from_inr(amount / (1 + fee), fill, step, prec)
                        notional = qty * fill
                        fe = notional * fee
                        if notional >= min_notional and notional + fe <= cash:
                            cash -= notional + fe
                            holdings[a["name"]] = qty
                            entry_cost[a["name"]] = (notional + fe) / qty
                            fees_paid += fe
                            open_trade[a["name"]] = {"ts": ts, "price": fill,
                                                     "qty": qty, "rsi": rsi_prev}

        value = cash + sum(holdings[a["name"]] * (last_close[a["name"]] or 0.0)
                           for a in assets if last_close[a["name"]] is not None)
        equity.append((datetime.fromtimestamp(ts / 1000, tz=timezone.utc), value))

    final_value = equity[-1][1] if equity else cash
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    peak, mdd = -1e18, 0.0
    for _, v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak > 0 else 0.0)
    return {
        "round_trips": len(trades),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "avg_win_pct": (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss_pct": (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0),
        "fees_paid": fees_paid,
        "tds_paid": tds_paid,
        "final_value": final_value,
        "pnl": final_value - start_cash,
        "pnl_pct": (final_value / start_cash - 1) * 100,
        "max_drawdown_pct": mdd * 100,
        "equity": equity,
        "trades": trades,
    }


# ------------------------------------------------------------- historical sweep
def run_sweep(cfg: dict, days: int | None = None, count: int | None = None,
              chart_path: str | None = None) -> dict:
    days = days or cfg.get("sweep", {}).get("days", cfg["backtest"]["days"])
    rows = account_grid(cfg, count)
    assets = cfg["assets"]
    coin = make_coin(cfg)

    print(f"Fetching {days} days of real 1h candles from CoinDCX (once, shared "
          f"by all {len(rows)} accounts) ...")
    candles = fetch_history(cfg, coin, assets, days)
    if not candles or not any(candles.values()):
        raise SystemExit("No candle data â aborting sweep.")

    meta = {a["name"]: coin.market(a["name"]) for a in assets}
    start_cash = sweep_start_cash(cfg)
    hodl = hodl_benchmark(cfg, coin, assets, candles, start_cash=start_cash)
    uniq = len({tuple(r[k] for k in PARAM_KEYS) for r in rows})
    print(f"  {len(rows)} demo accounts, {uniq} UNIQUE strategies, each "
          f"starting with â¹{start_cash:,.0f} paper cash.")
    print(f"  HODL benchmark (same cash, held): â¹{hodl['final_value']:,.0f} "
          f"({hodl['pnl_pct']:+.1f}%)")

    results = []
    for n, row in enumerate(rows, 1):
        res = _sim_account(cfg, row, assets, candles, meta)
        res.update({k: row[k] for k in PARAM_KEYS})
        res["account"] = row["account"]
        res["name"] = row["name"]
        results.append(res)
        if n % 20 == 0 or n == len(rows):
            print(f"  simulated {n}/{len(rows)} accounts ...")

    results.sort(key=lambda r: r["pnl_pct"], reverse=True)
    save_account_rows(rows)
    leaderboard = _write_leaderboard(cfg, results, hodl, days)
    _print_leaderboard(leaderboard, hodl)
    _export_best(cfg, leaderboard)
    if chart_path:
        _make_chart(chart_path, cfg, rows, results, hodl, days)
    return {"results": results, "leaderboard": leaderboard, "hodl": hodl}


def _write_leaderboard(cfg: dict, results: list[dict], hodl: dict, days: int) -> list[dict]:
    paths.SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    path = paths.SWEEP_DIR / "results.csv"
    fields = (["rank"] + ["account"] + PARAM_KEYS +
              ["round_trips", "win_rate", "avg_win_pct", "avg_loss_pct",
               "profit_factor", "final_value", "pnl", "pnl_pct",
               "max_drawdown_pct", "fees_paid", "tds_paid"])
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for i, r in enumerate(results, 1):
            w.writerow([i] + [r.get(k, "") for k in
                              ["account"] + PARAM_KEYS] +
                       [round(r.get(k, 0), 3) for k in
                        ("round_trips", "win_rate", "avg_win_pct", "avg_loss_pct",
                         "profit_factor", "final_value", "pnl", "pnl_pct",
                         "max_drawdown_pct", "fees_paid", "tds_paid")])
    print(f"\nLeaderboard saved: {path}")

    top10 = results[:10]
    with open(paths.SWEEP_DIR / "equity_top10.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date"] + [r["account"] for r in top10] + ["hodl"])
        dates = [d for d, _ in top10[0]["equity"]]
        by_date = {r["account"]: dict(r["equity"]) for r in top10}
        for d in dates:
            w.writerow([d.date().isoformat()] +
                       [round(by_date[r["account"]].get(d, 0), 2) for r in top10] +
                       [round(hodl["final_value"], 2)])
    return results


def _print_leaderboard(leaderboard: list[dict], hodl: dict) -> None:
    print("\n" + "=" * 108)
    print(f" {len(leaderboard)}-ACCOUNT TOURNAMENT â ranked by return | "
          f"everything after fees + slippage + 1% TDS")
    print(" entry: dip = buys oversold (RSI<=entry), momentum = buys strength (RSI>=entry)")
    print(" hold: hours after which the bot exits on schedule (hold_timeout); 0 = until tp/sl/exit-RSI")
    print(f" HODL benchmark for the same cash: â¹{hodl['final_value']:,.0f} "
          f"({hodl['pnl_pct']:+.1f}%)")
    print("=" * 108)
    hodl_ret = hodl["pnl_pct"]
    hdr = (f"{'#':>3} {'account':<8}{'entry':>6}{'exit':>6}{'tp%':>6}{'sl%':>6}"
           f"{'pos%':>6}{'hold':>6}{'trades':>7}{'win%':>6}{'PF':>6}{'ret%':>9}"
           f"{'vsHODL':>8}{'maxDD%':>8}")
    print(hdr)
    for i, r in enumerate(leaderboard[:15], 1):
        print(f"{i:>3} {r['account']:<8}{r['entry_rsi']:>6.0f}{r['exit_rsi']:>6.0f}"
              f"{r['take_profit_pct']:>6.0f}{r['stop_loss_pct']:>6.1f}"
              f"{r['position_size_pct']:>6.0f}{r['max_hold_hours']:>6.0f}"
              f"{r['round_trips']:>7}"
              f"{r['win_rate']:>6.0f}{r['profit_factor']:>6.2f}"
              f"{r['pnl_pct']:>+9.2f}{r['pnl_pct'] - hodl_ret:>+8.2f}"
              f"{r['max_drawdown_pct']:>8.1f}")
    if len(leaderboard) > 15:
        print(f"  ... {len(leaderboard) - 15} more accounts (see data/sweep/results.csv)")
    worst = leaderboard[-3:]
    print("\n Bottom 3 (what to avoid):")
    for r in worst:
        print(f"   {r['account']}  entryâ¤{r['entry_rsi']:.0f} exitâ¥{r['exit_rsi']:.0f} "
              f"tp{r['take_profit_pct']:.0f}% sl{r['stop_loss_pct']:.1f}% "
              f"pos{r['position_size_pct']:.0f}% h{r['max_hold_hours']:.0f} -> "
              f"{r['pnl_pct']:+.2f}% (win {r['win_rate']:.0f}%)")
    def best(key, label):
        r = max(leaderboard, key=lambda x: x.get(key, -1e9))
        print(f"\n Best {label}: {r['account']} ({r['name']}) "
              f"=> {r.get(key):.1f}")
    best("profit_factor", "profit factor")
    best("win_rate", "win rate")
    r = min(leaderboard, key=lambda x: x.get("max_drawdown_pct", 1e9))
    print(f"\n Lowest drawdown: {r['account']} ({r['name']}) "
          f"=> maxDD {r['max_drawdown_pct']:.1f}%")


def _export_best(cfg: dict, leaderboard: list[dict]) -> Path:
    best = leaderboard[0]
    out = copy.deepcopy(cfg)
    for k in PARAM_KEYS:
        out["strategy"][k] = best[k]
    path = paths.SWEEP_DIR / "best_strategy.yaml"
    paths.SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f"# Winner of the {len(leaderboard)}-account tournament "
                 f"({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})\n")
        fh.write(f"# account {best['account']} | {best['name']} | "
                 f"return {best['pnl_pct']:+.2f}% | maxDD {best['max_drawdown_pct']:.1f}%\n")
        fh.write("# Use:  python3 cryptobot.py --config data/sweep/best_strategy.yaml check\n")
        # export the FULL config so it works standalone with --config
        full = copy.deepcopy(cfg)
        full["strategy"] = {k: best[k] for k in PARAM_KEYS}
        yaml.safe_dump(full, fh, sort_keys=False)
    print(f"\nâ WINNER {best['account']} ({best['name']}): {best['pnl_pct']:+.2f}% "
          f"return, {best['round_trips']} round trips, win rate {best['win_rate']:.0f}%")
    print(f"  config exported: {path}")


def _make_chart(path: str, cfg: dict, rows: list[dict], results: list[dict],
                hodl: dict, days: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed â skipping chart (pip install matplotlib).")
        return
    top = results[:10]
    plt.figure(figsize=(12, 6))
    for r in top:
        dates = [d for d, _ in r["equity"]]
        plt.plot(dates, [v for _, v in r["equity"]], lw=1.0, alpha=0.9,
                 label=f"{r['account']} ({r['pnl_pct']:+.1f}%)")
    plt.axhline(sweep_start_cash(cfg), color="grey", ls=":", lw=1,
                label=f"starting cash â¹{sweep_start_cash(cfg):,.0f}")
    plt.title(f"Top-10 strategies of {len(results)} â last {days} days of real "
              "1h data (fees incl.)")
    plt.ylabel("Portfolio value (INR)")
    plt.xlabel("Date")
    plt.legend(fontsize=7, ncol=2)
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=130)
    print(f"Chart saved: {path}")


# ----------------------------------------------------------- LIVE tournament
def live_sweep(cfg: dict, top_n: int | None = None, rank_only: bool = False,
               reset: bool = False) -> None:
    """Run one live cycle for every demo account (paper, real prices).

    reset=True wipes all account states and starts everyone again at
    start_cash_inr (default â¹10,000) with the (possibly new) strategy grid.
    """
    desired = int((cfg.get("sweep") or {}).get("accounts", 200))
    sig = grid_signature(cfg)
    needs_wipe = reset
    if paths.ACCOUNT_CSV.exists() and not reset:
        rows = load_account_rows()
        old_sig = paths.SIGNATURE_FILE.read_text().strip() if paths.SIGNATURE_FILE.exists() else ""
        if len(rows) != desired or old_sig != sig:
            needs_wipe = True
            print(f"Strategy grid changed (accounts {len(rows)} -> {desired} or "
                  f"different parameters) â restarting ALL demo accounts.")
    if needs_wipe:
        shutil.rmtree(paths.ACCOUNTS_DIR, ignore_errors=True)
        for leftover in ("live_summary.csv",):
            p = paths.SWEEP_DIR / leftover
            if p.exists():
                p.unlink()
        print("All demo accounts wiped & will restart at "
              f"â¹{sweep_start_cash(cfg):,.0f} each with their new unique strategy.")

    if not paths.ACCOUNT_CSV.exists() or needs_wipe:
        rows = account_grid(cfg, desired)
        save_account_rows(rows)
    else:
        rows = load_account_rows()
    paths.SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    paths.SIGNATURE_FILE.write_text(sig)

    trade_rows = rows[:top_n] if top_n else rows
    coin = make_coin(cfg)
    start_cash = sweep_start_cash(cfg)
    if not rank_only:
        cache = build_market_cache(cfg, coin)
        if cache:
            print(f"Live signal data cached for {len(cache)} assets "
                  f"(shared by all {len(trade_rows)} traded accounts).")
        for row in trade_rows:
            cfg2 = apply_overrides(cfg, row)
            cfg2["initial_cash_inr"] = start_cash   # every account starts at ₹10,000
            broker = make_broker(cfg2, state_dir=paths.ACCOUNTS_DIR / row["account"])
            run_cycle(cfg2, broker, coin, market_cache=cache, verbose=False)
    _live_summary(cfg, rows)


def _live_summary(cfg: dict, rows: list[dict]) -> None:
    """Rank all live accounts by current paper value (prices from one tick)."""
    coin = make_coin(cfg)
    tickers = coin.tickers()
    prices = {}
    for a in cfg["assets"]:
        t = tickers.get(a["name"])
        if t:
            try:
                prices[a["name"]] = float(t.get("last_price") or 0.0)
            except (TypeError, ValueError):
                continue
    out = []
    for row in rows:
        broker = make_broker(cfg, state_dir=paths.ACCOUNTS_DIR / row["account"])
        mv = broker.market_value(prices)
        out.append({**row, "value": broker.cash + mv, "cash": broker.cash,
                    "holdings": mv, "realized": broker.realized_pnl,
                    "trades": len(broker.read_trades())})
    out.sort(key=lambda r: r["value"], reverse=True)
    paths.SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(paths.SWEEP_DIR / "live_summary.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "account"] + PARAM_KEYS + ["value_inr", "cash_inr",
                                                       "holdings_inr", "realized_pnl_inr", "trades"])
        for i, r in enumerate(out, 1):
            w.writerow([i, r["account"]] + [r[k] for k in PARAM_KEYS] +
                       [round(r["value"], 2), round(r["cash"], 2),
                        round(r["holdings"], 2), round(r["realized"], 2), r["trades"]])
    print(f"\nLive accounts ranked by current value ({datetime.now(IST):%Y-%m-%d %H:%M IST}):")
    for i, r in enumerate(out[:15], 1):
        print(f"  {i:>2}. {r['account']} ({r['name']:<26}) â¹{r['value']:>12,.2f} "
              f"| trades {r['trades']:>2} | realized â¹{r['realized']:+,.2f}")
    if len(out) > 15:
        print(f"  ... {len(out) - 15} more (see data/sweep/live_summary.csv)")
