"""Historical replay (backtest) plus the HODL benchmark."""

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .coindcx import CoinDCX, qty_from_inr
from .engine import fetch_history, hodl_benchmark, make_public_coin
from .indicators import rsi_series
from .strategy import entry_signal, exit_reason, position_amount

WINDOW_MS = 990 * 3600 * 1000   # ~990 hours per API request


def _simulate(cfg: dict, coin: CoinDCX, assets: list[dict],
              candles: dict[str, list[dict]]) -> dict:
    """Replay the RSI-swing strategy over shared 1h history (the `backtest`
    command). Rules match live trading exactly: signal from bars BEFORE the
    current bar (rs[i-1]), execute at the current bar's OPEN, fee-inclusive
    qty sizing, fees + slippage + 1% TDS on every side. No look-ahead.

    Returns metrics (round_trips, win_rate, pnl_pct, max_drawdown_pct, ...)
    plus the full equity curve and per-round-trip trade list for CSV/chart.
    """
    s = cfg["strategy"]
    fee = cfg["backtest"]["fee_rate"]
    slip = cfg["backtest"]["slippage_bps"] / 10_000.0
    tds = cfg["backtest"]["tds_rate"]

    cash = cfg["backtest"]["start_cash_inr"]
    holdings: dict[str, float] = {a["name"]: 0.0 for a in assets}
    entry_cost: dict[str, float] = {}          # avg cost incl. fee per asset
    invested = 0.0
    trades: list[dict] = []                    # one entry per round trip
    open_trade: dict[str, dict] = {}

    # precompute per-asset arrays for O(1) signal lookup (same pattern as
    # _sim_account: rsi_series once per asset instead of recomputing the RSI
    # from a growing close list at EVERY bar, which made backtests O(n^2))
    idx_by_time: dict[str, dict[int, int]] = {}
    rs: dict[str, list] = {}
    bar_by_time: dict[str, dict[int, dict]] = {}
    meta: dict[str, dict] = {}
    last_close: dict[str, float | None] = {a["name"]: None for a in assets}
    period = int(s.get("rsi_period", 14))
    for a in assets:
        cls = candles[a["name"]]
        idx_by_time[a["name"]] = {c["time"]: i for i, c in enumerate(cls)}
        rs[a["name"]] = rsi_series([c["close"] for c in cls], period)
        bar_by_time[a["name"]] = {c["time"]: c for c in cls}
        md = coin.market(a["name"])
        meta[a["name"]] = {"step": md.get("step", 1e-6) or 1e-6,
                           "prec": md.get("target_currency_precision", 6),
                           "min_notional": float(md.get("min_notional", 100))}

    all_ts = sorted({c["time"] for cls in candles.values() for c in cls})
    equity = []

    for ts in all_ts:
        for a in assets:
            name = a["name"]
            bar = bar_by_time[name].get(ts)
            if bar is None:
                continue
            last_close[name] = bar["close"]
            i = idx_by_time[name][ts]
            # signal from bars BEFORE this one (rs[i-1] == rsi of closes[:i]),
            # execute at this bar's OPEN - identical to live run_cycle rules
            rsi_val = rs[name][i - 1] if i > 0 else None
            step = meta[name]["step"]
            prec = meta[name]["prec"]
            min_notional = meta[name]["min_notional"]

            if holdings.get(name, 0.0) > 0:
                held_hours = ((ts - open_trade[name]["ts"]) / 3_600_000.0
                              if name in open_trade else None)
                reason = exit_reason(rsi_val, bar["open"], entry_cost.get(name, 0.0), s,
                                     held_hours)
                if reason:
                    qty = holdings[name]
                    fill = bar["open"] * (1 - slip)
                    notional = qty * fill
                    fe = notional * fee
                    td = notional * tds
                    cost_basis = qty * entry_cost[name]
                    cash += notional - fe - td
                    pnl = (notional - fe) - cost_basis
                    trades.append({
                        "asset": name, "entry": open_trade[name],
                        "exit_ts": ts, "exit": fill, "reason": reason,
                        "pnl": pnl,
                        "pnl_pct": (fill / entry_cost[name] - 1) * 100,
                    })
                    del open_trade[name]
                    holdings[name] = 0.0
                    entry_cost[name] = 0.0
            else:
                if entry_signal(rsi_val, s):
                    amount = position_amount(cash, s)
                    if amount >= max(s.get("min_buy_inr", 500), min_notional):
                        fill = bar["open"] * (1 + slip)
                        # fee-inclusive sizing, same as PaperBroker.buy, so the
                        # backtest fills match live paper fills exactly (with
                        # position_size_pct 100 the old fee-exclusive qty made
                        # notional + fee > cash and silently skipped EVERY buy)
                        qty = qty_from_inr(amount / (1 + fee), fill, step, prec)
                        notional = qty * fill
                        fe = notional * fee
                        if notional >= min_notional and notional + fe <= cash:
                            cash -= notional + fe
                            holdings[name] = qty
                            entry_cost[name] = (notional + fe) / qty
                            invested += notional + fe
                            open_trade[name] = {"ts": ts, "price": fill, "qty": qty,
                                                     "rsi": rsi_val}

        value = cash + sum(holdings[a["name"]] * (last_close[a["name"]] or 0.0)
                           for a in assets if last_close[a["name"]] is not None)
        equity.append((datetime.fromtimestamp(ts / 1000, tz=timezone.utc), value))

    # ---- metrics ----
    final_value = equity[-1][1] if equity else cash
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    peak, mdd = -1e18, 0.0
    for _, v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak > 0 else 0.0)
    return {
        "round_trips": len(trades),
        "wins": len(wins),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "avg_win_pct": (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss_pct": (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0,
        "invested": invested,
        "final_value": final_value,
        "pnl": final_value - cfg["backtest"]["start_cash_inr"],
        "pnl_pct": (final_value / cfg["backtest"]["start_cash_inr"] - 1) * 100,
        "max_drawdown_pct": mdd * 100,
        "equity": equity,
        "trades": trades,
    }


def run_backtest(cfg: dict, days: int | None = None, chart_path: str | None = None) -> dict:
    days = days or cfg["backtest"]["days"]
    coin = make_public_coin(cfg)
    assets = cfg["assets"]

    print(f"Fetching {days} days of 1h candles from CoinDCX for {len(assets)} assets ...")
    candles = fetch_history(cfg, coin, assets, days)
    if not candles or all(len(v) < 100 for v in candles.values()):
        raise SystemExit("Not enough hourly data â aborting backtest.")

    sig = _simulate(cfg, coin, assets, candles)
    hodl = hodl_benchmark(cfg, coin, assets, candles)
    _print_results(sig, hodl, days)
    _save_results(sig, hodl, days)
    if chart_path:
        _make_chart(chart_path, cfg, sig, hodl, days)
    return {"signal": sig, "hodl": hodl}


def _print_results(sig: dict, hodl: dict, days: int) -> None:
    print(f"\n===== SIGNAL BACKTEST â {days} days of REAL 1h CoinDCX data =====")
    print(f"  Strategy (RSI swing): round trips {sig['round_trips']} | "
          f"win rate {sig['win_rate']:.0f}% | avg win {sig['avg_win_pct']:+.2f}% | "
          f"avg loss {sig['avg_loss_pct']:+.2f}%")
    print(f"  invested â¹{sig['invested']:,.0f} | final â¹{sig['final_value']:,.0f} | "
          f"P&L â¹{sig['pnl']:+,.0f} ({sig['pnl_pct']:+.1f}%) | maxDD {sig['max_drawdown_pct']:.1f}%")
    print(f"  Benchmark HODL:      invested â¹{hodl['invested']:,.0f} | "
          f"final â¹{hodl['final_value']:,.0f} | "
          f"P&L â¹{hodl['pnl']:+,.0f} ({hodl['pnl_pct']:+.1f}%)")
    print("  (fees + slippage + 1% TDS included; results vary â past â  future)")


def _save_results(sig: dict, hodl: dict, days: int) -> None:
    paths.BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(paths.BACKTEST_RESULTS_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "signal_strategy", "hodl_benchmark"])
        for key in ("round_trips", "win_rate", "invested", "final_value", "pnl",
                    "pnl_pct", "max_drawdown_pct"):
            w.writerow([key, round(sig.get(key, 0), 4), "" if key in ("round_trips", "win_rate") else round(hodl.get(key, 0), 4)])
        w.writerow(["days", days, days])
    with open(paths.BACKTEST_EQUITY_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "signal_value"])
        for d, v in sig["equity"]:
            w.writerow([d.date().isoformat(), round(v, 2)])
    with open(paths.BACKTEST_TRADES_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["asset", "entry_time", "entry_price", "rsi_at_entry",
                    "exit_time", "exit_price", "reason", "pnl_inr", "pnl_pct"])
        for t in sig["trades"]:
            w.writerow([t["asset"],
                        datetime.fromtimestamp(t["entry"]["ts"] / 1000, tz=timezone.utc).isoformat(timespec="minutes"),
                        f"{t['entry']['price']:.2f}", f"{t['entry']['rsi']:.1f}",
                        datetime.fromtimestamp(t["exit_ts"] / 1000, tz=timezone.utc).isoformat(timespec="minutes"),
                        f"{t['exit']:.2f}", t["reason"],
                        f"{t['pnl']:.2f}", f"{t['pnl_pct']:.2f}"])
    print("Saved data/backtest/backtest_results.csv, backtest_equity.csv, backtest_trades.csv")


def _make_chart(path: str, cfg: dict, sig: dict, hodl: dict, days: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed â skipping chart (pip install matplotlib).")
        return
    dates = [d for d, _ in sig["equity"]]
    plt.figure(figsize=(11, 6))
    plt.plot(dates, [v for _, v in sig["equity"]], label="RSI Swing strategy", lw=1.6)
    plt.axhline(cfg["backtest"]["start_cash_inr"], color="grey", ls=":", lw=1,
                label="starting cash")
    plt.axhline(hodl["final_value"], color="tab:orange", ls="--", lw=1.2,
                label=f"HODL benchmark (â¹{hodl['final_value']:,.0f})")
    plt.title(f"RSI Swing strategy â last {days} days, 1h signals (fees incl.)")
    plt.ylabel("Portfolio value (INR)")
    plt.xlabel("Date")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=130)
    print(f"Chart saved: {path}")
