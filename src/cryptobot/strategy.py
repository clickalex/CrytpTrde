"""Entry/exit signal logic shared by live trading, backtest, and sweep."""

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .indicators import rsi

if TYPE_CHECKING:
    from .broker import Position

def rsi_value(candles: list[dict], period: int = 14) -> float | None:
    """RSI of the hourly closes. None when there's not enough data yet."""
    return rsi([c["close"] for c in candles], period)


def entry_signal(rsi_val: float | None, cfg: dict) -> bool:
    """Entry gate for the two strategy families (`entry_mode`):

      dip      (default) - buy WEAKNESS:  RSI <= entry_rsi (oversold dip)
      momentum           - buy STRENGTH:  RSI >= entry_rsi (breakout/strong)

    `entry_rsi` means the opposite threshold in the two families (30-ish for
    dip bots, 55-70 for momentum bots), which is why the sweep grid carries
    its own entry_rsi list covering both. Used identically by run_cycle,
    _simulate and _sim_account, so backtests always match live behaviour.
    """
    if rsi_val is None:
        return False
    mode = str(cfg.get("entry_mode", "dip") or "dip").lower()
    entry = float(cfg.get("entry_rsi", 30))
    if mode == "momentum":
        return rsi_val >= entry
    return rsi_val <= entry


def position_hold_hours(pos: "Position", now: datetime | None = None) -> float | None:
    """Hours since the position's last buy (drives max_hold_hours exits).

    Returns None when the entry time is unknown (pre-existing state without
    a timestamp) - those positions are never timed out, they still exit via
    take-profit / stop-loss / RSI.
    """
    if not pos or not pos.last_buy_at:
        return None
    try:
        t0 = datetime.fromisoformat(str(pos.last_buy_at))
    except ValueError:
        return None
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - t0).total_seconds() / 3600.0)


def exit_reason(rsi_val: float | None, price: float, avg_cost: float,
                cfg: dict, held_hours: float | None = None) -> str | None:
    """Which exit condition is met, or None if we keep holding.

    Priority: take-profit, stop-loss, hold_timeout, then the opportunistic
    RSI exit. `hold_timeout` fires when the position has been held at least
    `max_hold_hours` (0 disables) - that is what the week-holding (168h) and
    month-holding (720h) strategies use to realize on a schedule. Callers
    that don't track entry time pass held_hours=None and simply never time
    out.
    """
    if avg_cost <= 0 or price <= 0:
        return None
    tp = float(cfg.get("take_profit_pct", 0) or 0)
    sl = float(cfg.get("stop_loss_pct", 0) or 0)
    if tp > 0 and price >= avg_cost * (1 + tp / 100):
        return "take_profit"
    if sl > 0 and price <= avg_cost * (1 - sl / 100):
        return "stop_loss"
    max_hold = float(cfg.get("max_hold_hours", 0) or 0)
    if max_hold > 0 and held_hours is not None and held_hours >= max_hold:
        return "hold_timeout"
    exit_rsi = float(cfg.get("exit_rsi", 999))
    if rsi_val is not None and rsi_val >= exit_rsi:
        return "rsi_overbought"
    return None


def position_amount(cash: float, cfg: dict) -> float:
    """How much INR to deploy on an entry: position_size_pct of available cash.

    Floored (not rounded) to the paisa so the amount can never exceed the
    cash that is actually available - with position_size_pct 100 a rounded-up
    amount made `notional + fee <= cash` a coin flip that silently skipped
    buys in the sims and occasionally in the live broker."""
    pct = float(cfg.get("position_size_pct", 40))
    amount = math.floor(max(0.0, cash) * pct / 100.0 * 100.0) / 100.0
    return amount
