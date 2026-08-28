"""PaperBroker: simulated fills, fees, 1% TDS, and plain-file persistence."""

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .coindcx import qty_from_inr

@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0          # per-unit cost basis, INCLUDING buy fee
    invested: float = 0.0          # total INR put in (notional + fees)
    last_buy_at: str = ""

    def to_dict(self) -> dict:
        return {"qty": self.qty, "avg_cost": self.avg_cost,
                "invested": self.invested, "last_buy_at": self.last_buy_at}

    @staticmethod
    def from_dict(d: dict) -> "Position":
        return Position(
            qty=float(d.get("qty", 0)), avg_cost=float(d.get("avg_cost", 0)),
            invested=float(d.get("invested", 0)),
            last_buy_at=d.get("last_buy_at", ""),
        )


class PaperBroker:
    """Simulated exchange account (the bot's book-keeping core).

    Fill model: market orders fill at ticker price +/- `slippage`, pay a
    taker `fee_rate` on notional, and sells additionally deduct a 1% TDS
    (Section 194S, simulated; it is a credit you get back at tax time, so it
    is NOT counted into realized_pnl - see tax_summary).

    Accounting invariants (verified by tests/test_speed_fix.py):
      cash        -= notional + fee              on buys
      cash        += notional - fee - tds        on sells
      avg_cost     = (notional + fee) / qty      fee included in cost basis
      realized_pnl = (sell notional - fee) - qty * avg_cost
      cash_delta over the account's life == realized_pnl - total TDS

    State is one JSON file (atomic tmp+rename save) plus an append-only
    trades.csv audit log, both inside `state_dir`. When `max_trades` is set
    (default 100), trades.csv retains at most the most recent N fills to prevent
    unbounded disk/repo growth, while portfolio.json preserves lifetime trade
    counts, cumulative P&L and tax metrics.
    """

    def __init__(self, state_dir: Path, cash: float, fee_rate: float,
                 slippage_bps: float, tds_rate: float = 0.01, simulate_tds: bool = True,
                 max_trades: int = 100):
        self.dir = Path(state_dir)
        self.file = self.dir / "portfolio.json"
        self.trades_csv = self.dir / "trades.csv"
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000.0
        self.tds_rate = tds_rate
        self.simulate_tds = simulate_tds
        self.max_trades = max_trades
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.total_trades = 0
        self.sell_count = 0
        self.gross_gains = 0.0
        self.gross_losses = 0.0
        self.total_tds = 0.0
        self._load()

    # ---------------------------------------------------------------- persistence
    def _reconstruct_lifetime_from_csv(self):
        """Populate lifetime counters from trades.csv for legacy state files."""
        try:
            trades = self.read_trades()
            if not trades:
                return
            self.total_trades = len(trades)
            sells = [t for t in trades if str(t.get("side") or "").lower() == "sell"]
            self.sell_count = len(sells)
            self.total_tds = sum(float(t.get("tds_inr") or 0.0) for t in sells)

            state: dict[str, dict] = {}
            total_realized = gains = losses = 0.0
            for t in trades:
                asset = str(t.get("asset") or "")
                side = str(t.get("side") or "").lower()
                try:
                    qty = float(t.get("quantity") or 0)
                    notional = float(t.get("notional_inr") or 0)
                    fee = float(t.get("fee_inr") or 0)
                except (TypeError, ValueError):
                    continue
                st = state.setdefault(asset, {"qty": 0.0, "avg_cost": 0.0})
                if side == "buy":
                    new_qty = st["qty"] + qty
                    if new_qty:
                        st["avg_cost"] = (st["avg_cost"] * st["qty"] + notional + fee) / new_qty
                    st["qty"] = new_qty
                elif side == "sell":
                    pnl = (notional - fee) - qty * st["avg_cost"] if st["avg_cost"] > 0 else 0.0
                    st["qty"] = max(0.0, st["qty"] - qty)
                    total_realized += pnl
                    if pnl > 0:
                        gains += pnl
                    else:
                        losses += pnl
            self.gross_gains = gains
            self.gross_losses = losses
            if self.realized_pnl == 0.0 and total_realized != 0.0:
                self.realized_pnl = total_realized
        except Exception:
            pass

    def _load(self):
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text())
            self.cash = float(data.get("cash_inr", self.cash))
            self.positions = {k: Position.from_dict(v) for k, v in data.get("holdings", {}).items()}
            self.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.total_trades = int(data.get("total_trades", 0))
            self.sell_count = int(data.get("sell_count", 0))
            self.gross_gains = float(data.get("gross_gains", 0.0))
            self.gross_losses = float(data.get("gross_losses", 0.0))
            self.total_tds = float(data.get("total_tds", 0.0))

            if "total_trades" not in data and self.trades_csv.exists():
                self._reconstruct_lifetime_from_csv()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # A truncated write, a disk-full save or a hand-edited file must
            # not brick the bot forever — the hourly run would keep crashing on
            # startup with no way back. Keep the bad file for inspection and
            # start from the configured cash instead.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.file.with_name(f"{self.file.name}.corrupt-{stamp}")
            try:
                self.file.replace(backup)
                kept = backup.name
            except OSError:
                kept = f"{self.file.name} (left in place)"
            print(f"  ! {self.file} is unreadable ({exc}).")
            print(f"    Kept a copy at {kept}; starting fresh at "
                  f"Rs.{self.cash:,.2f}. Restore it by hand if you need the "
                  f"old state.")

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {
            "cash_inr": self.cash,
            "realized_pnl": self.realized_pnl,
            "total_trades": self.total_trades,
            "sell_count": self.sell_count,
            "gross_gains": round(self.gross_gains, 2),
            "gross_losses": round(self.gross_losses, 2),
            "total_tds": round(self.total_tds, 2),
            "holdings": {k: p.to_dict() for k, p in self.positions.items()},
        }
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.file)

    def prune_trades(self, max_trades: int | None = None) -> int:
        """Trim trades.csv to keep only the newest `max_trades` rows.
        Returns the count of pruned rows."""
        limit = self.max_trades if max_trades is None else max_trades
        if not limit or limit <= 0 or not self.trades_csv.exists():
            return 0
        try:
            with self.trades_csv.open("r", newline="", encoding="utf-8") as fh:
                r = csv.reader(fh)
                header = next(r, None)
                if not header:
                    return 0
                rows = list(r)
            if len(rows) <= limit:
                return 0
            pruned_count = len(rows) - limit
            kept = rows[-limit:]
            tmp = self.trades_csv.with_suffix(".csv.tmp")
            with tmp.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                w.writerows(kept)
            tmp.replace(self.trades_csv)
            return pruned_count
        except OSError:
            return 0

    def _log_trade(self, asset, side, price, qty, notional, fee, tds):
        self.dir.mkdir(parents=True, exist_ok=True)
        new = not self.trades_csv.exists()
        realized = round(self.realized_pnl, 2) if side == "sell" else ""
        new_row = [
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            asset, side, f"{price:.6f}", f"{qty:.8f}",
            f"{notional:.2f}", f"{fee:.2f}", f"{tds:.2f}", realized
        ]

        if not new and self.max_trades and self.max_trades > 0:
            try:
                with self.trades_csv.open("r", newline="", encoding="utf-8") as fh:
                    r = csv.reader(fh)
                    header = next(r, None)
                    rows = list(r)
                if header is None:
                    header = ["timestamp_utc", "asset", "side", "price_inr", "quantity",
                              "notional_inr", "fee_inr", "tds_inr", "realized_pnl_inr"]
                rows.append(new_row)
                if len(rows) > self.max_trades:
                    rows = rows[-self.max_trades:]
                tmp = self.trades_csv.with_suffix(".csv.tmp")
                with tmp.open("w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(header)
                    w.writerows(rows)
                tmp.replace(self.trades_csv)
                return
            except OSError:
                pass

        with self.trades_csv.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["timestamp_utc", "asset", "side", "price_inr", "quantity",
                            "notional_inr", "fee_inr", "tds_inr", "realized_pnl_inr"])
            w.writerow(new_row)

    # ------------------------------------------------------------------ trading
    def buy(self, asset: str, amount_inr: float, ask: float, step: float,
            precision: int, min_notional: float = 100.0) -> dict:
        """Market-buy `amount_inr` of `asset` at `ask` (pre-slippage). Returns fill info."""
        fill_price = ask * (1 + self.slippage)
        if fill_price <= 0:
            return {"ok": False, "reason": f"invalid price Rs.{fill_price}"}
        # Largest step-multiple qty whose cost INCLUDING the taker fee fits in
        # amount_inr - solved directly. (The old code floored qty to amount
        # excluding the fee and then decremented one step at a time; on a
        # 1e-6-step market that loop spun ~200k times per large buy.)
        budget = amount_inr / (1 + self.fee_rate)
        qty = qty_from_inr(budget, fill_price, step, precision)
        if qty <= 0:
            return {"ok": False, "reason": f"quantity rounds to zero at Rs.{fill_price:.2f}"}
        notional = qty * fill_price
        # float-safety trim (normally 0 iterations)
        while notional + notional * self.fee_rate > amount_inr and qty > 0:
            qty = round(qty - step, precision)
            if qty < step:
                return {"ok": False, "reason": "order below one step"}
            notional = qty * fill_price
        fee = notional * self.fee_rate
        total = notional + fee
        if total > self.cash:
            return {"ok": False, "reason": f"insufficient cash (need ₹{total:.2f}, have ₹{self.cash:.2f})"}
        if notional < min_notional:
            return {"ok": False, "reason": f"below exchange min notional ₹{min_notional:.0f}"}

        self.cash -= total
        self.total_trades += 1
        pos = self.positions.setdefault(asset, Position())
        new_qty = pos.qty + qty
        pos.avg_cost = (pos.avg_cost * pos.qty + notional + fee) / new_qty if new_qty else 0.0
        pos.qty = new_qty
        pos.invested += notional + fee
        pos.last_buy_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._log_trade(asset, "buy", fill_price, qty, notional, fee, 0.0)
        self.save()
        return {"ok": True, "asset": asset, "price": fill_price, "qty": qty,
                "notional": notional, "fee": fee}

    def sell(self, asset: str, qty: float, bid: float, step: float, precision: int) -> dict:
        """Market-sell the given quantity at `bid` (pre-slippage). TDS simulated."""
        pos = self.positions.get(asset)
        if not pos or pos.qty < qty:
            return {"ok": False, "reason": "no such position/quantity"}
        qty = min(qty, pos.qty)
        fill_price = bid * (1 - self.slippage)
        notional = qty * fill_price
        fee = notional * self.fee_rate
        tds = notional * self.tds_rate if self.simulate_tds else 0.0
        proceeds = notional - fee - tds
        cost_basis = qty * pos.avg_cost
        trade_pnl = (notional - fee) - cost_basis

        self.cash += proceeds
        self.realized_pnl += trade_pnl
        self.total_trades += 1
        self.sell_count += 1
        self.total_tds += tds
        if trade_pnl > 0:
            self.gross_gains += trade_pnl
        else:
            self.gross_losses += trade_pnl
        pos.qty -= qty
        pos.invested -= cost_basis
        if pos.qty <= 1e-12:
            del self.positions[asset]
        self._log_trade(asset, "sell", fill_price, qty, notional, fee, tds)
        self.save()
        return {"ok": True, "asset": asset, "price": fill_price, "qty": qty,
                "notional": notional, "fee": fee, "tds": tds, "realized_pnl": self.realized_pnl}

    # ------------------------------------------------------------------- reports
    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices.get(a, 0.0) for a, p in self.positions.items())

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(p.qty * (prices.get(a, 0.0) - p.avg_cost) for a, p in self.positions.items())

    def read_trades(self) -> list[dict]:
        if not self.trades_csv.exists():
            return []
        with self.trades_csv.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def tax_summary(self) -> dict:
        """Aggregate realized gains/losses, a flat 30% tax estimate on gross
        gains (India taxes VDA gains at 30% + cess, no loss offset), and the
        TDS paid (creditable against that tax)."""
        if self.sell_count > 0 or self.total_trades > 0:
            return {
                "sell_count": self.sell_count,
                "total_realized": self.realized_pnl,
                "gross_gains": self.gross_gains,
                "gross_losses": self.gross_losses,
                "estimated_tax_30pct": self.gross_gains * 0.30,
                "tds_credit": self.total_tds,
            }
        trades = self.read_trades()
        sells = [t for t in trades if str(t.get("side") or "").lower() == "sell"]
        gains = sum(float(t.get("realized_pnl_inr") or 0.0) for t in sells
                    if float(t.get("realized_pnl_inr") or 0.0) > 0)
        losses = sum(float(t.get("realized_pnl_inr") or 0.0) for t in sells
                     if float(t.get("realized_pnl_inr") or 0.0) < 0)
        tds = sum(float(t.get("tds_inr") or 0.0) for t in sells)
        return {
            "sell_count": len(sells),
            "total_realized": sum(float(t.get("realized_pnl_inr") or 0.0) for t in sells),
            "gross_gains": gains,
            "gross_losses": losses,
            "estimated_tax_30pct": gains * 0.30,
            "tds_credit": tds,
        }
