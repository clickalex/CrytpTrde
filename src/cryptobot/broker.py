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
    trades.csv audit log, both inside `state_dir`. Run ONE bot process per
    state dir - concurrent writers can clobber each other's portfolio.
    """

    def __init__(self, state_dir: Path, cash: float, fee_rate: float,
                 slippage_bps: float, tds_rate: float = 0.01, simulate_tds: bool = True):
        self.dir = Path(state_dir)
        self.file = self.dir / "portfolio.json"
        self.trades_csv = self.dir / "trades.csv"
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000.0
        self.tds_rate = tds_rate
        self.simulate_tds = simulate_tds
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self):
        if self.file.exists():
            data = json.loads(self.file.read_text())
            self.cash = float(data.get("cash_inr", self.cash))
            self.positions = {k: Position.from_dict(v) for k, v in data.get("holdings", {}).items()}
            self.realized_pnl = float(data.get("realized_pnl", 0.0))

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {
            "cash_inr": self.cash,
            "realized_pnl": self.realized_pnl,
            "holdings": {k: p.to_dict() for k, p in self.positions.items()},
        }
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.file)

    def _log_trade(self, asset, side, price, qty, notional, fee, tds):
        self.dir.mkdir(parents=True, exist_ok=True)
        new = not self.trades_csv.exists()
        with self.trades_csv.open("a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["timestamp_utc", "asset", "side", "price_inr", "quantity",
                            "notional_inr", "fee_inr", "tds_inr", "realized_pnl_inr"])
            realized = round(self.realized_pnl, 2) if side == "sell" else ""
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        asset, side, f"{price:.6f}", f"{qty:.8f}",
                        f"{notional:.2f}", f"{fee:.2f}", f"{tds:.2f}", realized])

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
            return {"ok": False, "reason": f"insufficient cash (need â¹{total:.2f}, have â¹{self.cash:.2f})"}
        if notional < min_notional:
            return {"ok": False, "reason": f"below exchange min notional â¹{min_notional:.0f}"}

        self.cash -= total
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

        self.cash += proceeds
        self.realized_pnl += (notional - fee) - cost_basis
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
        with self.trades_csv.open() as fh:
            return list(csv.DictReader(fh))

    def tax_summary(self) -> dict:
        """Aggregate the trades.csv log: realized gains/losses, a flat 30%
        tax estimate on gross gains (India taxes VDA gains at 30% + cess,
        no loss offset), and the TDS paid (creditable against that tax)."""
        trades = self.read_trades()
        sells = [t for t in trades if t["side"] == "sell"]
        gains = sum(float(t["realized_pnl_inr"]) for t in sells if float(t["realized_pnl_inr"]) > 0)
        losses = sum(float(t["realized_pnl_inr"]) for t in sells if float(t["realized_pnl_inr"]) < 0)
        tds = sum(float(t["tds_inr"]) for t in sells)
        return {
            "sell_count": len(sells),
            "total_realized": sum(float(t["realized_pnl_inr"]) for t in sells),
            "gross_gains": gains,
            "gross_losses": losses,
            "estimated_tax_30pct": gains * 0.30,
            "tds_credit": tds,
        }
