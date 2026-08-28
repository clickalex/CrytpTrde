"""CLI commands and command-line entry point."""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import paths
from .backtest import run_backtest
from .coindcx import CoinDCXError
from .engine import (load_cfg, make_broker, make_coin, resolve_assets,
                      run_cycle, runtime_assets)
from .sweep import (live_sweep, load_account_rows, prune_tournament_trades,
                    run_sweep)

# `bot` / `coin` / `prune` / `wipe` are offline, read-only or maintenance
# commands — not bot runs — so they never touch the "last bot run" heartbeat.
NO_HEARTBEAT_COMMANDS = {"bot", "coin", "prune", "wipe"}


def record_last_run(command: str, status: str = "ok", note: str = "") -> None:
    """Write data/state/last_run.json — WHEN the bot last ran, and how it went.

    The static dashboard can't ask the bot anything, so this file is the
    source of truth: build_data_js.py embeds it into data.js as `bot_status`,
    and every dashboard page renders it as the "Last bot run" badge in the
    header. `status` is "ok" (cycle completed), "skipped" (e.g. CoinDCX
    unreachable this hour) or "error" (daemon cycle crashed). Best-effort:
    a failure here must never break the bot itself.
    """
    try:
        data = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "command": command,
            "status": status,
            "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
        }
        if note:
            data["note"] = note
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = paths.LAST_RUN_FILE.with_suffix(".json.tmp")   # atomic save, like portfolio.json
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(paths.LAST_RUN_FILE)
    except OSError as exc:
        print(f"  ! could not write last-run heartbeat: {exc}", file=sys.stderr)


# --------------------------------------------------------------------- commands
def cmd_init(cfg: dict, args) -> None:
    broker = make_broker(cfg)
    if broker.file.exists() and not args.yes:
        print(f"{broker.file} already exists. Re-run with --yes to overwrite.")
        return
    broker.cash = float(cfg["initial_cash_inr"])
    broker.positions = {}
    broker.save()
    print(f"Paper portfolio created: â¹{broker.cash:,.2f} cash, "
          f"assets: {', '.join(a['name'] for a in cfg['assets'])}")
    print("  The bot will buy only when conditions are met (hourly RSI check), "
          "and sell on take-profit / stop-loss / overbought.")


def _print_price_header():
    print(f"\n{'ASSET':<8}{'QTY':>14}{'LAST PRICE':>16}{'VALUE':>14}"
          f"{'AVG COST':>14}{'P&L':>14}{'P&L%':>9}")


def cmd_status(cfg: dict, args) -> None:
    broker = make_broker(cfg)
    cfg["assets"] = runtime_assets(cfg, broker)
    coin = make_coin(cfg)
    tickers = coin.tickers()
    prices = {}
    rows = []
    held_count = 0
    for a in cfg["assets"]:
        t = tickers.get(a["name"])
        price = float(t["last_price"]) if t else 0.0
        prices[a["name"]] = price
        pos = broker.positions.get(a["name"])
        if pos and pos.qty > 0:
            held_count += 1
            pnl = pos.qty * (price - pos.avg_cost)
            pnl_pct = (price / pos.avg_cost - 1) * 100 if pos.avg_cost else 0.0
            rows.append((a["name"], pos.qty, price, pos.qty * price,
                         pos.avg_cost, pnl, pnl_pct))

    if rows and len(cfg["assets"]) <= 20:
        _print_price_header()
        for a in cfg["assets"]:
            t = tickers.get(a["name"])
            price = float(t["last_price"]) if t else 0.0
            pos = broker.positions.get(a["name"])
            if pos and pos.qty > 0:
                pnl = pos.qty * (price - pos.avg_cost)
                pnl_pct = (price / pos.avg_cost - 1) * 100 if pos.avg_cost else 0.0
                print(f"{a['name']:<8}{pos.qty:>14,.8f}{price:>16,.2f}{pos.qty * price:>14,.2f}"
                      f"{pos.avg_cost:>14,.2f}{pnl:>+14,.2f}{pnl_pct:>+8.1f}%")
            else:
                print(f"{a['name']:<8}{'-':>14}{price:>16,.2f}{'-':>14}{'-':>14}{'-':>14}{'-':>9}")
    else:
        print(f"Watching {len(cfg['assets'])} assets; {held_count} currently held.")
        if rows:
            _print_price_header()
            for name, qty, price, value, avg, pnl, pnl_pct in rows:
                print(f"{name:<8}{qty:>14,.8f}{price:>16,.2f}{value:>14,.2f}"
                      f"{avg:>14,.2f}{pnl:>+14,.2f}{pnl_pct:>+8.1f}%")

    mv = broker.market_value(prices)
    print(f"\nCash: Rs.{broker.cash:,.2f} | Holdings value: Rs.{mv:,.2f} | "
          f"Total: Rs.{broker.cash + mv:,.2f}")
    print(f"Unrealized P&L: Rs.{broker.unrealized_pnl(prices):+,.2f} | "
          f"Realized P&L: Rs.{broker.realized_pnl:+,.2f}")
    tax = broker.tax_summary()
    if tax["sell_count"]:
        print(f"\n--- Tax (paper estimate) ---\n"
              f"Sells logged: {tax['sell_count']}\n"
              f"Realized P&L total: Rs.{tax['total_realized']:,.2f}\n"
              f"Gross gains (taxable @30%): Rs.{tax['gross_gains']:,.2f}\n"
              f"Gross losses (NOT offsettable in India): Rs.{tax['gross_losses']:,.2f}\n"
              f"Estimated tax on gains: Rs.{tax['estimated_tax_30pct']:,.2f}\n"
              f"1% TDS withheld (claim as credit): Rs.{tax['tds_credit']:,.2f}")
    print("(See docs/tax_notes.md - this is an estimate, not tax advice.)")


def cmd_assets(cfg: dict, args) -> None:
    assets = cfg["assets"]
    print(f"Resolved {len(assets)} assets:")
    for a in assets:
        print(f"  {a['name']:<16} {a['pair']}")


# ------------------------------------------------------------------ drill-down
# These helpers read the local tournament state (data/sweep/accounts/acc_XXX) so the
# `bot` and `coin` commands work WITHOUT touching CoinDCX — they are pure file
# reads and run fine in CI or on a machine with no network.

def _sweep_accounts() -> list[str]:
    """Sorted tournament account ids from data/sweep/accounts/ (e.g. acc_001..acc_500)."""
    if not paths.ACCOUNTS_DIR.exists():
        return []
    return sorted(d.name for d in paths.ACCOUNTS_DIR.iterdir() if d.is_dir())


def _account_row(account: str) -> dict:
    """The strategy row for `account` from data/sweep/accounts.csv ({} if unknown)."""
    for r in load_account_rows():
        if r["account"] == account:
            return r
    return {}


def _account_trades(account: str) -> list[dict]:
    """Read one account's append-only data/sweep/accounts/acc_XXX/trades.csv log."""
    path = paths.ACCOUNTS_DIR / account / "trades.csv"
    if not path.exists():
        return []
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    # append-only file is already chronological; keep a stable order for ties.
    rows.sort(key=lambda r: (r.get("timestamp_utc", ""), r.get("asset", ""),
                             r.get("side", "")))
    return rows


def _account_portfolio(account: str) -> dict:
    """Read one account's current data/sweep/accounts/acc_XXX/portfolio.json state."""
    path = paths.ACCOUNTS_DIR / account / "portfolio.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _analyze_fills(fills: list[dict]) -> dict:
    """Reconstruct per-fill attributed realized P&L and current holdings.

    The broker (PaperBroker) logs each sell's CUMULATIVE account realized P&L
    in trades.csv, so `realized_pnl_inr` on a row is not that sell's own P&L.
    This replays the fills with the broker's moving-average cost accounting
    (identical to PaperBroker: a buy's cost basis = notional + fee, averaged
    per unit; a sell realizes (notional - fee) - qty * avg_cost) to attribute a
    per-sell P&L and work out what is still held. Returns a summary dict.
    """
    state: dict[str, dict] = {}   # asset -> {"qty": float, "avg_cost": float}
    enriched: list[dict] = []
    buy_count = sell_count = 0
    buy_notional = buy_fee = 0.0
    sell_notional = sell_fee = sell_tds = 0.0
    total_realized = wins = losses = 0.0

    for f in fills:
        asset = str(f.get("asset") or "")
        side = str(f.get("side") or "").lower()
        try:
            qty = float(f.get("quantity") or 0)
            notional = float(f.get("notional_inr") or 0)
            fee = float(f.get("fee_inr") or 0)
            tds = float(f.get("tds_inr") or 0)
        except (TypeError, ValueError):
            continue
        st = state.setdefault(asset, {"qty": 0.0, "avg_cost": 0.0})
        attributed: float | None = None
        if side == "buy":
            new_qty = st["qty"] + qty
            if new_qty:
                st["avg_cost"] = (st["avg_cost"] * st["qty"] + notional + fee) / new_qty
            st["qty"] = new_qty
            buy_count += 1
            buy_notional += notional
            buy_fee += fee
        elif side == "sell":
            sell_count += 1
            attributed = (notional - fee) - qty * st["avg_cost"] if st["avg_cost"] > 0 else 0.0
            st["qty"] = max(0.0, st["qty"] - qty)
            total_realized += attributed
            if attributed > 0:
                wins += 1
            else:
                losses += 1
            sell_notional += notional
            sell_fee += fee
            sell_tds += tds
        enriched.append({**f, "attributed_pnl": attributed})

    holdings = {a: s for a, s in state.items() if s["qty"] > 0}
    return {
        "fills": enriched, "holdings": holdings,
        "buy_count": buy_count, "sell_count": sell_count,
        "buy_notional": buy_notional, "buy_fee": buy_fee,
        "sell_notional": sell_notional, "sell_fee": sell_fee,
        "sell_tds": sell_tds, "total_realized": total_realized,
        "win_sells": wins, "loss_sells": losses,
        "win_rate": (wins / sell_count * 100) if sell_count else 0.0,
    }


def cmd_bot(cfg: dict, args) -> None:
    """Show the FULL trade history of one tournament bot (all fills + holdings)."""
    account = args.account
    available = _sweep_accounts()
    if account not in available:
        msg = (f"No tournament account '{account}'. Found {len(available)} accounts "
               f"(e.g. acc_001..acc_{len(available):03d}).")
        # JSON consumers want a clean stream on stdout; errors go to stderr.
        print(msg, file=sys.stderr if getattr(args, "json", False) else sys.stdout)
        sys.exit(1)

    row = _account_row(account)
    portfolio = _account_portfolio(account)
    fills = _account_trades(account)
    a = _analyze_fills(fills)

    if getattr(args, "json", False):
        print(json.dumps({
            "account": account, "strategy": row, "portfolio": portfolio,
            **a,
        }, indent=2, ensure_ascii=False))
        return

    print(f"\n===== {account} — FULL TRADE HISTORY =====")
    if row:
        name = row.get("name", "")
        print(f"  strategy : {name or '(unknown)'}")
        print(f"  params   : {row.get('entry_mode')}  entry_RSI {row.get('entry_rsi')}  "
              f"exit_RSI {row.get('exit_rsi')}  TP {row.get('take_profit_pct')}%  "
              f"SL {row.get('stop_loss_pct')}%  size {row.get('position_size_pct')}%  "
              f"hold {row.get('max_hold_hours')}h")
    print(f"  cash     : Rs.{float(portfolio.get('cash_inr', 0)):,.2f}   "
          f"realized Rs.{float(portfolio.get('realized_pnl', 0)):+,.2f}")
    if portfolio.get("holdings"):
        print("  holdings :")
        for asset, pos in portfolio["holdings"].items():
            print(f"      {asset:<10} qty {float(pos['qty']):,.8f}  "
                  f"avg cost Rs.{float(pos['avg_cost']):,.2f}  "
                  f"invested Rs.{float(pos['invested']):,.2f}")
    else:
        print("  holdings : none (all flat)")

    if not a["fills"]:
        print("  trades   : no fills recorded yet.")
        return

    print(f"\n  {'TIME (UTC)':<22}{'ASSET':<10}{'SIDE':<6}{'PRICE':>14}"
          f"{'QTY':>14}{'NOTIONAL':>14}{'FEE':>9}{'TDS':>9}{'REALIZED':>11}")
    for f in a["fills"]:
        pnl = f["attributed_pnl"]
        pnl_txt = "   -" if pnl is None else f"{pnl:>+11,.2f}"
        print(f"  {f.get('timestamp_utc',''):<22}{f.get('asset',''):<10}"
              f"{f.get('side',''):<6}{float(f.get('price_inr',0)):>14,.6f}"
              f"{float(f.get('quantity',0)):>14,.8f}{float(f.get('notional_inr',0)):>14,.2f}"
              f"{float(f.get('fee_inr',0)):>9,.2f}{float(f.get('tds_inr',0)):>9,.2f}"
              f"{pnl_txt}")

    print(f"\n  SUMMARY: {a['buy_count']} buys (Rs.{a['buy_notional']:,.2f} notional, "
          f"Rs.{a['buy_fee']:,.2f} fees) | {a['sell_count']} sells "
          f"(Rs.{a['sell_notional']:,.2f} notional, Rs.{a['sell_fee']:,.2f} fees, "
          f"Rs.{a['sell_tds']:,.2f} TDS)")
    print(f"           attributed realized P&L: Rs.{a['total_realized']:+,.2f} "
          f"({a['win_sells']:.0f} wins / {a['loss_sells']:.0f} losses, "
          f"{a['win_rate']:.1f}% sell win rate)")


def cmd_coin(cfg: dict, args) -> None:
    """Show which tournament bots bought/sold a given coin (in detail)."""
    asset = str(args.coin).upper()
    per_bot: list[dict] = []

    for acct in _sweep_accounts():
        fills = _account_trades(acct)
        coin_fills = [f for f in fills if str(f.get("asset") or "").upper() == asset]
        if not coin_fills:
            continue
        a = _analyze_fills(coin_fills)
        row = _account_row(acct)
        per_bot.append({
            "account": acct,
            "name": row.get("name", ""),
            "buy_count": a["buy_count"],
            "sell_count": a["sell_count"],
            "net_qty": sum(s["qty"] for s in a["holdings"].values()),
            "total_realized": a["total_realized"],
            "buy_notional": a["buy_notional"],
            "sell_notional": a["sell_notional"],
            "fees": a["buy_fee"] + a["sell_fee"],
            "tds": a["sell_tds"],
            "fills": a["fills"],   # already per-account attributed
        })

    if getattr(args, "json", False):
        # JSON is the aggregate-per-bot view: don't embed the per-fill list
        # (the `bot` command + the text report already cover the fills).
        slim = [{k: v for k, v in b.items() if k != "fills"} for b in per_bot]
        print(json.dumps({"coin": asset, "bots": slim}, indent=2, ensure_ascii=False))
        return

    if not per_bot:
        print(f"\nNo tournament bot has traded {asset} yet.")
        return

    total_buys = sum(b["buy_count"] for b in per_bot)
    total_sells = sum(b["sell_count"] for b in per_bot)
    total_realized = sum(b["total_realized"] for b in per_bot)
    total_fees = sum(b["fees"] for b in per_bot)
    total_tds = sum(b["tds"] for b in per_bot)
    total_notional = sum(b["buy_notional"] + b["sell_notional"] for b in per_bot)
    holding_bots = sum(1 for b in per_bot if b["net_qty"] > 0)

    print(f"\n===== {asset} — WHICH BOTS TRADED IT =====")
    print(f"  {len(per_bot)} bots traded {asset}: {total_buys} buys / {total_sells} sells "
          f"| {holding_bots} still holding")
    print(f"  notional Rs.{total_notional:,.2f} | fees Rs.{total_fees:,.2f} | "
          f"TDS Rs.{total_tds:,.2f}")
    print(f"  attributed realized P&L across all sells: Rs.{total_realized:+,.2f}")

    print(f"\n  {'BOT':<10}{'BUYS':>6}{'SELLS':>6}{'NET QTY':>14}{'NOTIONAL':>14}"
          f"{'FEES':>10}{'TDS':>10}{'REALIZED':>12}")
    for b in sorted(per_bot, key=lambda x: x["total_realized"], reverse=True):
        print(f"  {b['account']:<10}{b['buy_count']:>6}{b['sell_count']:>6}"
              f"{b['net_qty']:>14,.8f}{b['buy_notional'] + b['sell_notional']:>14,.2f}"
              f"{b['fees']:>10,.2f}{b['tds']:>10,.2f}{b['total_realized']:>+12,.2f}")

    # drill into the individual fills of the most-traded bots (each bot's
    # fills were already attributed to ITS OWN cost basis during aggregation)
    print(f"\n  All {asset} fills across bots:")
    print(f"    {'TIME (UTC)':<22}{'BOT':<10}{'SIDE':<6}{'PRICE':>14}{'QTY':>14}"
          f"{'NOTIONAL':>14}{'REALIZED':>11}")
    for b in sorted(per_bot, key=lambda x: x["total_realized"], reverse=True):
        for f in b["fills"]:
            pnl = f["attributed_pnl"]
            pnl_txt = "   -" if pnl is None else f"{pnl:>+11,.2f}"
            print(f"    {f.get('timestamp_utc',''):<22}{b['account']:<10}"
                  f"{f.get('side',''):<6}{float(f.get('price_inr',0)):>14,.6f}"
                  f"{float(f.get('quantity',0)):>14,.8f}{float(f.get('notional_inr',0)):>14,.2f}"
                  f"{pnl_txt}")


def cmd_check(cfg: dict, args) -> None:
    run_cycle(cfg, make_broker(cfg), make_coin(cfg))


def cmd_run(cfg: dict, args) -> None:
    interval = int(cfg.get("check_interval_min", 60))
    print(f"Daemon running â checking every {interval} min (Ctrl+C to stop).")
    while True:
        try:
            run_cycle(cfg, make_broker(cfg), make_coin(cfg))
            record_last_run("run", "ok")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 â daemon must not die
            print(f"  ! cycle error: {exc}")
            record_last_run("run", "error", note=str(exc))
        time.sleep(interval * 60)


def cmd_reset(cfg: dict, args) -> None:
    if not args.yes:
        print("This deletes data/state/ (paper portfolio + trade log). Pass --yes to confirm.")
        return
    for f in ("portfolio.json", "trades.csv"):
        p = paths.STATE_DIR / f
        if p.exists():
            p.unlink()
    print("Paper state reset.")


def cmd_wipe(cfg: dict, args) -> None:
    """Manual-only maintenance: wipe ALL bot data and rebuild a fresh site.

    This is the **Reset Bot**. It NEVER runs on a schedule (it is not in the
    hourly GitHub Actions workflow) — you invoke it by hand when you want to
    throw everything away and start the website over as a blank slate:

        python3 cryptobot.py wipe --yes

    It deletes every paper portfolio, every one of the 500 tournament
    accounts, every backtest result and the live heartbeat, clears the
    mirrored ``web/state/`` copy, then regenerates ``web/data.js`` so the
    dashboard opens on an empty leaderboard. ``--dry-run`` previews what would
    be removed without touching anything.
    """
    import shutil
    import subprocess

    dry = bool(getattr(args, "dry_run", False))
    if not args.yes and not dry:
        print("This WIPES ALL bot data and resets the website to a blank slate:")
        print("  - data/state/      paper portfolio, trades.csv, last-run heartbeat")
        print("  - data/sweep/      all tournament accounts + rankings + results")
        print("  - data/backtest/   backtest trades, results, equity curves")
        print("  - web/state/       mirrored paper portfolio shown on the site")
        print("Then it rebuilds web/data.js so the dashboard starts fresh.")
        print("Pass --yes to confirm, or --dry-run to preview without deleting.")
        return

    # Targets whose *contents* we clear (the directories themselves stay, so
    # the paths the rest of the code expects keep existing).
    targets = [paths.STATE_DIR, paths.SWEEP_DIR, paths.BACKTEST_DIR]
    removed_files = 0
    removed_bytes = 0

    def _tally(p: Path) -> None:
        nonlocal removed_files, removed_bytes
        try:
            if p.is_dir():
                for c in p.rglob("*"):
                    if c.is_file():
                        removed_files += 1
                        removed_bytes += c.stat().st_size
            elif p.exists():
                removed_files += 1
                removed_bytes += p.stat().st_size
        except OSError:
            pass

    for d in targets:
        if not d.exists():
            continue
        if dry:
            for child in sorted(d.rglob("*")):
                if child.is_file():
                    _tally(child)
            print(f"  [dry-run] would clear {d}")
            continue
        for child in list(d.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
        print(f"  cleared {d}")

    # The mirrored paper portfolio under web/state is part of the public site.
    web_state = paths.WEB_DIR / "state"
    if web_state.exists():
        if dry:
            print("  [dry-run] would clear web/state/")
        else:
            shutil.rmtree(web_state, ignore_errors=True)
            print("  cleared web/state/")

    if dry:
        print(f"\n[dry-run] Would remove ~{removed_files:,} files "
              f"({removed_bytes/1024:.0f} KiB). Nothing deleted.")
        return

    # Heartbeat is written into data/state/ AFTER it is cleared, so it survives
    # and the rebuilt data.js can show 'last run: wipe'.
    record_last_run("wipe", "ok", note="site wiped & reset to a clean slate")

    # Regenerate the dashboard dataset from the now-empty data/ tree so the
    # site opens on a fresh, empty leaderboard (only bot_status survives).
    try:
        subprocess.run(
            [sys.executable, str(paths.REPO_ROOT / "scripts" / "build_data_js.py")],
            check=False,
        )
    except OSError as exc:
        print(f"  ! could not rebuild web/data.js: {exc}")

    print("\nSite wiped. All bot data cleared and web/data.js rebuilt — "
          "the dashboard now opens as a new, empty site.")


def cmd_backtest(cfg: dict, args) -> None:
    run_backtest(cfg, days=args.days, chart_path=args.chart)


def cmd_sweep(cfg: dict, args) -> None:
    run_sweep(cfg, days=args.days, count=args.count, chart_path=args.chart)


def cmd_sweep_live(cfg: dict, args) -> None:
    live_sweep(cfg, top_n=args.top, rank_only=args.rank_only, reset=args.reset)


def cmd_sweep_status(cfg: dict, args) -> None:
    live_sweep(cfg, rank_only=True)


def cmd_prune(cfg: dict, args) -> None:
    max_trades = getattr(args, "max_trades", None)
    if max_trades is None:
        sweep_cfg = cfg.get("sweep") or {}
        max_trades = sweep_cfg.get("max_trades", cfg.get("max_trades", 100))
    try:
        max_trades = int(max_trades)
    except (TypeError, ValueError):
        max_trades = 100

    main_broker = make_broker(cfg)
    pruned_main = main_broker.prune_trades(max_trades=max_trades)
    pruned_sweep = prune_tournament_trades(cfg, max_trades=max_trades)
    total_pruned = pruned_main + pruned_sweep
    print(f"Pruned {total_pruned} excess trade log entries (retained last {max_trades} fills per account).")
    if pruned_main:
        print(f"  data/state/trades.csv: {pruned_main} pruned")
    if pruned_sweep:
        print(f"  data/sweep/accounts/*/trades.csv: {pruned_sweep} pruned across tournament accounts")


# ------------------------------------------------------------------------ main
def main():
    parser = argparse.ArgumentParser(description="CoinDCX signal trading bot (paper)")
    parser.add_argument("--config", default=str(paths.CONFIG_PATH))
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument(
        "--all-assets", action="store_true",
        help="ignore the configured asset list and discover all active INR spot markets")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[global_parser], help="create/reset paper portfolio")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", parents=[global_parser], help="portfolio & tax view")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("assets", parents=[global_parser],
                       help="list the assets this config will check/trade")
    p.set_defaults(func=cmd_assets)

    p = sub.add_parser("check", parents=[global_parser], help="run one signal-check cycle")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", parents=[global_parser], help="run forever (hourly checker)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("reset", parents=[global_parser], help="delete paper state")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("backtest", parents=[global_parser],
                       help="backtest signal strategy on 1h data")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--chart", default=None)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("sweep", parents=[global_parser],
                       help="offline strategy tournament over historical "
                            "candles (sweep.accounts bots, from config)")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--chart", default=None)
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("sweep-live", parents=[global_parser],
                       help="run all demo accounts live (paper)")
    p.add_argument("--top", type=int, default=None, help="only first N accounts")
    p.add_argument("--rank-only", action="store_true", help="no trading, just ranking")
    p.add_argument("--reset", action="store_true",
                   help="wipe all demo accounts & restart at Rs.10,000 each")
    p.set_defaults(func=cmd_sweep_live)

    p = sub.add_parser("sweep-status", parents=[global_parser],
                       help="live ranking of demo accounts")
    p.set_defaults(func=cmd_sweep_status)

    p = sub.add_parser("bot", parents=[global_parser],
                       help="full trade history of a single tournament bot (from data/sweep/accounts)")
    p.add_argument("account", help="e.g. acc_001")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.set_defaults(func=cmd_bot)

    p = sub.add_parser("coin", parents=[global_parser],
                       help="which tournament bots traded a given coin (from data/sweep/accounts)")
    p.add_argument("coin", help="e.g. BTCINR")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.set_defaults(func=cmd_coin)

    p = sub.add_parser("prune", parents=[global_parser],
                       help="trim trades.csv logs across all accounts to max-trades retention limit")
    p.add_argument("--max-trades", type=int, default=None,
                   help="max fills to keep per account (defaults to config max_trades, 100)")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("wipe", parents=[global_parser],
                       help="MANUAL ONLY: wipe ALL data & reset the website to a clean slate")
    p.add_argument("--yes", action="store_true",
                   help="confirm deletion of all bot data and the site reset")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="preview what would be deleted, change nothing")
    p.set_defaults(func=cmd_wipe)

    args = parser.parse_args()
    # The startup banner is for human logs; `--json` consumers want a pure
    # JSON stream on stdout, so route the banner to stderr for those commands.
    banner_stream = sys.stderr if (args.command in {"bot", "coin"} and args.json) else sys.stdout
    print(f"cryptobot starting: {' '.join(sys.argv[1:])}", flush=True, file=banner_stream)
    try:
        cfg = load_cfg(Path(args.config))
        # fail fast on an unknown strategy family - entry_signal would
        # otherwise silently trade it as a dip bot (e.g. a "mometum" typo)
        emode = str((cfg.get("strategy") or {}).get("entry_mode", "dip") or "dip").lower()
        if emode not in ("dip", "momentum"):
            raise SystemExit(
                f"config strategy.entry_mode must be 'dip' or 'momentum', "
                f"got {emode!r}")
        # Every command works with an explicit [{name, pair}, ...] list:
        # "auto" (and --all-assets) is resolved HERE, once, before dispatch,
        # so cmd_* functions and the sims never see the raw "auto" string

        if args.command not in {"bot", "coin", "prune", "wipe"}:
            extra_state_dirs = []
            if args.command in {"sweep-live", "sweep-status"} and paths.ACCOUNTS_DIR.exists():
                extra_state_dirs = [p for p in paths.ACCOUNTS_DIR.iterdir() if p.is_dir()]
            # backtest/sweep compare strategies over a STABLE universe -> no
            # dipped-market extras there (they flap hour to hour); live checks
            # (check/run/sweep-live/status) do want them so nothing crashed is missed.
            include_dipped = args.command not in {"backtest", "sweep"}
            cfg["assets"] = resolve_assets(cfg, force_all=args.all_assets,
                                           extra_state_dirs=extra_state_dirs,
                                           include_dipped=include_dipped)
        # `bot` / `coin` / `prune` read or maintain local state only — no asset scan,
        # no CoinDCX call needed, so they run offline (and in CI).

        args.func(cfg, args)
        if args.command not in NO_HEARTBEAT_COMMANDS:
            record_last_run(args.command, "ok")
    except CoinDCXError as exc:
        # Hourly GitHub Actions should not go red when CoinDCX/Cloudflare
        # blocks datacenter IPs — skip this hour and try again next cron.
        msg = f"CoinDCX unreachable this hour ({exc}). Skipping; will retry next run."
        print(msg, flush=True)
        print(msg, file=sys.stderr, flush=True)
        if args.command in {"sweep-live", "sweep-status", "check", "status", "assets"}:
            record_last_run(args.command, "skipped", note=msg)
            raise SystemExit(0) from exc
        raise SystemExit(1) from exc
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001 — always show a real traceback in CI
        import traceback
        traceback.print_exc()
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
