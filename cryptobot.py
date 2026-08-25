#!/usr/bin/env python3
# cryptobot.py - CryptoBot for CoinDCX (India) - SINGLE-FILE build.
# Generated from the modular sources by build_single_file.py; edit the modules
# and re-run that script, or edit this file directly (it is self-contained).
#
# Command examples:
#   python3 cryptobot.py status           live portfolio view
#   python3 cryptobot.py check --force    run one DCA buy cycle now
#   python3 cryptobot.py backtest         Smart DCA vs plain DCA on real data
# See README.md / CLOUD_SETUP.md for the full guide.
#
# DISCLAIMER: educational software, not financial advice.
import argparse
import copy
import csv
import hashlib
import hmac
import itertools
import json
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import yaml


# =============================================================================
# indicators.py
# =============================================================================
def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None when there is not enough data."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """RSI for every prefix of `closes`, computed in ONE forward pass.

    rsi_series[i] == rsi(closes[:i+1]) for every i (exactly), so the sweep can
    evaluate signals at any bar in O(1) instead of recomputing RSI from scratch
    at every step.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    for i in range(period, n):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


# =============================================================================
# strategy.py
# =============================================================================
def rsi_value(candles: list[dict], period: int = 14) -> float | None:
    """RSI of the hourly closes. None when there's not enough data yet."""
    return rsi([c["close"] for c in candles], period)


def entry_signal(rsi_val: float | None, cfg: dict) -> bool:
    """Buy when hourly RSI is oversold (dip) â 'condition met' for entries."""
    return rsi_val is not None and rsi_val <= float(cfg.get("entry_rsi", 30))


def exit_reason(rsi_val: float | None, price: float, avg_cost: float,
                cfg: dict) -> str | None:
    """Which exit condition is met, or None if we keep holding."""
    if avg_cost <= 0 or price <= 0:
        return None
    tp = float(cfg.get("take_profit_pct", 0) or 0)
    sl = float(cfg.get("stop_loss_pct", 0) or 0)
    if tp > 0 and price >= avg_cost * (1 + tp / 100):
        return "take_profit"
    if sl > 0 and price <= avg_cost * (1 - sl / 100):
        return "stop_loss"
    exit_rsi = float(cfg.get("exit_rsi", 999))
    if rsi_val is not None and rsi_val >= exit_rsi:
        return "rsi_overbought"
    return None


def position_amount(cash: float, cfg: dict) -> float:
    """How much INR to deploy on an entry: position_size_pct of available cash."""
    pct = float(cfg.get("position_size_pct", 40))
    amount = max(0.0, cash * pct / 100.0)
    return round(amount, 2)


# =============================================================================
# coindcx.py
# =============================================================================
API_BASE = "https://api.coindcx.com"
PUBLIC_BASE = "https://public.coindcx.com"


class CoinDCXError(RuntimeError):
    pass


class CoinDCX:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 timeout: int = 20):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self._tickers: dict[str, dict] | None = None
        self._markets: dict[str, dict] | None = None

    # ------------------------------------------------------------------ public
    def _get_json(self, url: str, params: dict | None = None):
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise CoinDCXError(f"Network error hitting {url}: {exc}") from exc
        if isinstance(data, dict) and data.get("status") == "error":
            raise CoinDCXError(f"CoinDCX error {data.get('code')}: {data.get('message')}")
        return data

    def tickers(self, refresh: bool = False) -> dict[str, dict]:
        """All tickers keyed by market name, e.g. {"BTCINR": {...}}."""
        if self._tickers is None or refresh:
            data = self._get_json(f"{API_BASE}/exchange/ticker")
            self._tickers = {t["market"]: t for t in data}
        return self._tickers

    def ticker(self, market: str, refresh: bool = False) -> dict:
        t = self.tickers(refresh).get(market)
        if not t:
            raise CoinDCXError(f"Unknown market '{market}' (check coindcx_name, e.g. BTCINR)")
        return t

    def markets_details(self, refresh: bool = False) -> dict[str, dict]:
        """Market rules keyed by coindcx_name (e.g. 'BTCINR'): precision, step, min_notional."""
        if self._markets is None or refresh:
            data = self._get_json(f"{API_BASE}/exchange/v1/markets_details")
            self._markets = {m["coindcx_name"]: m for m in data}
        return self._markets

    def market(self, name: str) -> dict:
        return self.markets_details().get(name) or {}

    def candles(self, pair: str, interval: str = "1d", start_ms: int | None = None,
                end_ms: int | None = None, limit: int = 1000) -> list[dict]:
        """OHLCV candles. Interval: 1m 5m 15m 30m 1h 2h 4h 6h 8h 1d 3d 1w 1M.
        Returns list of {"open","high","low","close","volume","time"(ms)}, newest first."""
        params = {"pair": pair, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        data = self._get_json(f"{PUBLIC_BASE}/market_data/candles/", params=params)
        if not isinstance(data, list):
            raise CoinDCXError(f"Unexpected candles response: {str(data)[:200]}")
        return sorted(data, key=lambda c: c["time"])  # oldest -> newest

    def orderbook(self, pair: str, depth: int = 10) -> dict:
        """Top-of-book bids/asks: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}."""
        data = self._get_json(f"{PUBLIC_BASE}/market_data/orderbook",
                              params={"pair": pair})
        def levels(d: dict) -> list[list[float]]:
            try:
                items = sorted(d.items(), key=lambda kv: float(kv[0]))
            except (AttributeError, ValueError):
                items = sorted(d) if isinstance(d, list) else []
            return [[float(k), float(v)] for k, v in items] if not isinstance(d, list) else [[float(x[0]), float(x[1])] for x in d]
        return {"bids": levels(data.get("bids", {})), "asks": levels(data.get("asks", {}))}

    def best_bid_ask(self, pair: str) -> tuple[float, float]:
        """Return (best_bid, best_ask) from the order book, falling back to ticker."""
        try:
            ob = self.orderbook(pair, depth=5)
            if ob["bids"] and ob["asks"]:
                return ob["bids"][-1][0], ob["asks"][0][0]
        except CoinDCXError:
            pass
        market = pair.split("_")[-1] + pair.split("_")[1]  # I-BTC_INR -> BTCINR
        t = self.ticker(market, refresh=True)
        return float(t["bid"]), float(t["ask"])

    # ----------------------------------------------------------------- private
    def _sign(self, body: dict) -> str:
        if not self.api_secret:
            raise CoinDCXError("API secret not set")
        body = dict(body)
        body["timestamp"] = int(round(time.time()))
        payload = json.dumps(body, separators=(",", ":"))
        return hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def private_post(self, path: str, body: dict) -> dict:
        """Signed POST to api.coindcx.com. Used ONLY in live mode."""
        if not (self.api_key and self.api_secret):
            raise CoinDCXError("API key/secret not configured (live mode needs both)")
        body = dict(body)
        body["timestamp"] = int(round(time.time()))
        payload = json.dumps(body, separators=(",", ":"))
        headers = {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": self.api_key,
            "X-AUTH-SIGNATURE": hmac.new(
                self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest(),
        }
        resp = requests.post(f"{API_BASE}{path}", data=payload, headers=headers,
                             timeout=self.timeout)
        return resp.json()

    def create_market_order(self, market: str, side: str, quantity: float,
                            ecode: str = "I") -> dict:
        """Live market order (spot). side: 'buy' | 'sell'. quantity in the asset.
        NOTE: verify order-response format on your account with a tiny order first."""
        return self.private_post("/exchange/v1/orders/create", {
            "side": side, "order_type": "market_order", "market": market,
            "quantity": quantity, "ecode": ecode,
        })


def qty_from_inr(amount_inr: float, price: float, step: float, precision: int) -> float:
    """Largest quantity of an asset buyable with amount_inr, rounded down to the
    market's step/precision rules (like a real exchange would accept)."""
    if price <= 0 or amount_inr <= 0:
        return 0.0
    raw = amount_inr / price
    steps = int(raw / step) if step > 0 else int(raw * (10 ** precision))
    qty = steps * step
    return round(qty, precision)


def env_keys(api_key_env: str, api_secret_env: str) -> tuple[str | None, str | None]:
    return os.environ.get(api_key_env), os.environ.get(api_secret_env)


# =============================================================================
# paper_broker.py
# =============================================================================
@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0          # per-unit cost basis (includes fees)
    invested: float = 0.0          # total INR put in
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
        qty = qty_from_inr(amount_inr, fill_price, step, precision)
        if qty <= 0:
            return {"ok": False, "reason": f"quantity rounds to zero at â¹{fill_price:.2f}"}
        notional = qty * fill_price
        # floor qty so fee fits inside the intended amount
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


# =============================================================================
# engine.py
# =============================================================================
IST = ZoneInfo("Asia/Kolkata")


# ------------------------------------------------------------------ construction
def load_cfg(path: Path) -> dict:
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh)


def make_broker(cfg: dict, state_dir: Path | None = None) -> PaperBroker:
    return PaperBroker(
        state_dir=state_dir or (Path(__file__).resolve().parent / "state"),
        cash=float(cfg["initial_cash_inr"]),
        fee_rate=float(cfg["fee_rate"]),
        slippage_bps=float(cfg["slippage_bps"]),
        tds_rate=float(cfg["tds_rate"]),
        simulate_tds=bool(cfg["simulate_tds"]),
    )


def make_coin(cfg: dict) -> CoinDCX:
    if cfg.get("live", {}).get("enabled"):
        k, s = env_keys(cfg["live"]["api_key_env"], cfg["live"]["api_secret_env"])
        return CoinDCX(api_key=k, api_secret=s)
    return CoinDCX()


def ensure_initialised(cfg: dict, broker: PaperBroker) -> None:
    """If no paper state exists yet (e.g. first run in the cloud), create it."""
    if broker.file.exists():
        return
    print(f"No paper state found ({broker.file.parent.name}) â initialising a "
          f"fresh paper portfolio first.")
    broker.cash = float(cfg["initial_cash_inr"])
    broker.positions = {}
    broker.save()


# ----------------------------------------------------------------- market data
def build_market_cache(cfg: dict, coin: CoinDCX) -> dict[str, tuple]:
    """Fetch candles + bid/ask ONCE per asset so a sweep of many accounts uses
    identical market data (fair comparison) with minimal API calls."""
    s = cfg["strategy"]
    cache: dict[str, tuple] = {}
    for a in cfg["assets"]:
        try:
            candles = coin.candles(a["pair"],
                                   interval=s.get("timeframe", "1h"),
                                   limit=int(s.get("signal_lookback", 200)))
            bid, ask = coin.best_bid_ask(a["pair"])
            cache[a["name"]] = (candles, bid, ask)
        except (CoinDCXError, KeyError, ValueError, IndexError) as exc:
            print(f"  {a['name']}: market data failed ({exc})")
    return cache


# --------------------------------------------------------------- trading cycle
def run_cycle(cfg: dict, broker: PaperBroker, coin: CoinDCX,
              market_cache: dict | None = None, verbose: bool = True) -> None:
    """One signal-check cycle. Trades ONLY when a condition is met:
       - BUY  : 1h RSI <= entry_rsi and no open position
       - SELL : take-profit / stop-loss / RSI >= exit_rsi
    """
    ensure_initialised(cfg, broker)
    s = cfg["strategy"]
    if verbose:
        print(f"[{datetime.now(IST):%Y-%m-%d %H:%M IST}] Signal check "
              f"(entry RSI â¤ {s.get('entry_rsi', 30)}, "
              f"exit RSI â¥ {s.get('exit_rsi', 999)})")

    for a in cfg["assets"]:
        md = coin.market(a["name"])
        step = md.get("step", 1e-6) or 1e-6
        prec = md.get("target_currency_precision", 6)
        min_notional = float(md.get("min_notional", 100))

        if market_cache and a["name"] in market_cache:
            candles, bid, ask = market_cache[a["name"]]
        else:
            try:
                candles = coin.candles(a["pair"], interval=s.get("timeframe", "1h"),
                                       limit=int(s.get("signal_lookback", 200)))
                bid, ask = coin.best_bid_ask(a["pair"])
            except (CoinDCXError, KeyError, ValueError, IndexError) as exc:
                print(f"  {a['name']}: market data failed ({exc}) â will retry next check")
                continue

        rsi_val = rsi_value(candles, int(s.get("rsi_period", 14)))
        pos = broker.positions.get(a["name"])

        if pos and pos.qty > 0:
            reason = exit_reason(rsi_val, bid, pos.avg_cost, s)
            if reason:
                res = broker.sell(a["name"], pos.qty, bid, step=step, precision=prec)
                if res["ok"]:
                    msg = (f"  ð» SELL ALL {a['name']} ({reason}) @ â¹{res['price']:,.2f}"
                           f" x {res['qty']:.8f} â â¹{res['notional']:,.2f}"
                           f" (fee â¹{res['fee']:.2f}, TDS â¹{res['tds']:.2f})")
                    print(msg) if verbose else None
                else:
                    print(f"  {a['name']}: sell failed â {res['reason']}") if verbose else None
            else:
                pnl_pct = (bid / pos.avg_cost - 1) * 100 if pos.avg_cost else 0.0
                if verbose:
                    print(f"  {a['name']}: HOLD {pos.qty:.8f} @ â¹{pos.avg_cost:,.2f} "
                          f"(now â¹{bid:,.2f}, {pnl_pct:+.1f}%, RSI {rsi_val:.1f})")
        elif pos is None or pos.qty <= 0:
            if entry_signal(rsi_val, s):
                amount = position_amount(broker.cash, s)
                if amount < float(s.get("min_buy_inr", 500)):
                    print(f"  {a['name']}: BUY condition MET (RSI {rsi_val:.1f} â¤ "
                          f"{s.get('entry_rsi')}) but â¹{amount:,.0f} < min_buy_inr â "
                          f"top up the paper cash or lower min_buy_inr.") if verbose else None
                    continue
                res = broker.buy(a["name"], amount, ask, step=step, precision=prec,
                                 min_notional=min_notional)
                if res["ok"]:
                    if verbose:
                        print(f"  ð¢ BUY {a['name']} â¹{res['notional']:,.2f} @ â¹{res['price']:,.2f}"
                              f" x {res['qty']:.8f} (fee â¹{res['fee']:.2f}) | RSI {rsi_val:.1f} â¤ "
                              f"{s.get('entry_rsi')} â opened, watching for exit")
                else:
                    print(f"  {a['name']}: BUY skipped â {res['reason']}") if verbose else None
            else:
                if verbose:
                    print(f"  {a['name']}: no position, RSI {rsi_val:.1f} â no entry "
                          f"(need â¤ {s.get('entry_rsi')} to buy)")
    if verbose:
        print("Check done.")


# -------------------------------------------------------------- HODL benchmark
def hodl_benchmark(cfg: dict, coin: CoinDCX, assets: list[dict],
                   candles: dict[str, list[dict]],
                   start_cash: float | None = None) -> dict:
    """Same starting cash, all invested once at the start, held to the end."""
    cash = start_cash if start_cash is not None else cfg["backtest"]["start_cash_inr"]
    start = cash
    invested = 0.0
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
        share = cash / len(assets)
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
    """Fetch hourly candles going back `days`, paging the API by ~990h chunks."""
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


# =============================================================================
# backtest.py
# =============================================================================
WINDOW_MS = 990 * 3600 * 1000   # ~990 hours per API request


def _simulate(cfg: dict, coin: CoinDCX, assets: list[dict],
              candles: dict[str, list[dict]]) -> dict:
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

    # merged hourly timeline
    all_ts = sorted({c["time"] for cls in candles.values() for c in cls})
    last_close = {a["name"]: None for a in assets}
    equity = []

    for ts in all_ts:
        bars = {}
        for a in assets:
            c = next((x for x in candles[a["name"]] if x["time"] == ts), None)
            if c is not None:
                bars[a["name"]] = c
                last_close[a["name"]] = c["close"]

        for a in assets:
            bar = bars.get(a["name"])
            if bar is None:
                continue
            price_open = bar["open"]
            closes_up_to_now = [x["close"] for x in candles[a["name"]] if x["time"] < ts]
            rsi_val = rsi_value([{"close": v} for v in closes_up_to_now],
                                int(s.get("rsi_period", 14))) if len(closes_up_to_now) >= 15 else None
            md = coin.market(a["name"])
            step = md.get("step", 1e-6) or 1e-6
            prec = md.get("target_currency_precision", 6)
            min_notional = float(md.get("min_notional", 100))

            if holdings.get(a["name"], 0.0) > 0:
                reason = exit_reason(rsi_val, price_open, entry_cost.get(a["name"], 0.0), s)
                if reason:
                    qty = holdings[a["name"]]
                    fill = price_open * (1 - slip)
                    notional = qty * fill
                    fe = notional * fee
                    td = notional * tds
                    cost_basis = qty * entry_cost[a["name"]]
                    cash += notional - fe - td
                    pnl = (notional - fe) - cost_basis
                    trades.append({
                        "asset": a["name"], "entry": open_trade[a["name"]],
                        "exit_ts": ts, "exit": fill, "reason": reason,
                        "pnl": pnl,
                        "pnl_pct": (fill / entry_cost[a["name"]] - 1) * 100,
                    })
                    del open_trade[a["name"]]
                    holdings[a["name"]] = 0.0
                    entry_cost[a["name"]] = 0.0
            else:
                if entry_signal(rsi_val, s):
                    amount = position_amount(cash, s)
                    if amount >= max(s.get("min_buy_inr", 500), min_notional):
                        fill = price_open * (1 + slip)
                        qty = qty_from_inr(amount, fill, step, prec)
                        notional = qty * fill
                        fe = notional * fee
                        if notional >= min_notional and notional + fe <= cash:
                            cash -= notional + fe
                            holdings[a["name"]] = qty
                            entry_cost[a["name"]] = (notional + fe) / qty
                            invested += notional + fe
                            open_trade[a["name"]] = {"ts": ts, "price": fill, "qty": qty,
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
    coin = CoinDCX(timeout=30)
    assets = cfg["assets"]

    print(f"Fetching {days} days of 1h candles from CoinDCX ...")
    candles: dict[str, list[dict]] = {}
    for a in assets:
        try:
            cls = fetch_hours(coin, a["pair"], days)
            candles[a["name"]] = cls
            print(f"  {a['name']}: {len(cls)} hourly candles  "
                  f"(last close â¹{cls[-1]['close']:,.2f})")
        except Exception as exc:  # noqa: BLE001
            print(f"  {a['name']}: SKIPPED ({exc})")
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
    with open("backtest_results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "signal_strategy", "hodl_benchmark"])
        for key in ("round_trips", "win_rate", "invested", "final_value", "pnl",
                    "pnl_pct", "max_drawdown_pct"):
            w.writerow([key, round(sig.get(key, 0), 4), "" if key in ("round_trips", "win_rate") else round(hodl.get(key, 0), 4)])
        w.writerow(["days", days, days])
    with open("backtest_equity.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "signal_value"])
        for d, v in sig["equity"]:
            w.writerow([d.date().isoformat(), round(v, 2)])
    with open("backtest_trades.csv", "w", newline="") as fh:
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
    print("Saved backtest_results.csv, backtest_equity.csv, backtest_trades.csv")


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


# =============================================================================
# sweep.py
# =============================================================================
HERE = Path(__file__).resolve().parent
SWEEP_DIR = HERE / "sweep"
ACCOUNTS_DIR = SWEEP_DIR / "accounts"
IST = ZoneInfo("Asia/Kolkata")

DEFAULT_GRID = {
    "entry_rsi": [22, 25, 28, 30, 33, 36, 40],
    "exit_rsi": [64, 68, 72, 76, 80],
    "take_profit_pct": [3, 4, 5, 6, 8, 10, 12],
    "stop_loss_pct": [1, 1.5, 2, 3],
    "position_size_pct": [20, 30, 40, 50, 60],
}
PARAM_KEYS = list(DEFAULT_GRID.keys())
ACCOUNT_CSV = SWEEP_DIR / "accounts.csv"
SIGNATURE_FILE = SWEEP_DIR / "grid_signature.txt"


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
    Row 0 is always the baseline (values from config.yaml -> strategy)."""
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

    # row 0 = baseline exactly as configured
    baseline = tuple(float(base.get(k)) for k in PARAM_KEYS)
    if unique[0] != baseline:
        unique[0] = baseline
    unique = list(dict.fromkeys(unique))  # guarantee: no duplicate strategies
    unique = unique[:count]

    rows = []
    for i, combo in enumerate(unique):
        params = dict(zip(PARAM_KEYS, combo))
        params["account"] = f"acc_{i + 1:03d}"
        params["name"] = (f"e{params['entry_rsi']:.0f}_x{params['exit_rsi']:.0f}"
                          f"_tp{params['take_profit_pct']:.0f}"
                          f"_sl{params['stop_loss_pct']:.1f}"
                          f"_p{params['position_size_pct']:.0f}")
        rows.append(params)
    return rows


def apply_overrides(cfg: dict, row: dict) -> dict:
    """Copy of cfg with the account's strategy parameters merged in."""
    out = copy.deepcopy(cfg)
    for k in PARAM_KEYS:
        out["strategy"][k] = row[k]
    return out


def save_account_rows(rows: list[dict]) -> Path:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACCOUNT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["account", "name"] + PARAM_KEYS)
        w.writeheader()
        w.writerows(rows)
    return ACCOUNT_CSV


def load_account_rows() -> list[dict]:
    with open(ACCOUNT_CSV) as fh:
        return [{k: (float(v) if k in PARAM_KEYS else v) for k, v in r.items()}
                for r in csv.DictReader(fh)]


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
    min_notional_all = float(meta[assets[0]["name"]].get("min_notional", 100)) if assets else 100.0

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
                reason = exit_reason(rsi_prev, o, entry_cost[a["name"]], strat)
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
                        qty = qty_from_inr(amount, fill, step, prec)
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
    candles = {}
    for a in assets:
        try:
            cls = fetch_hours(coin, a["pair"], days)
            candles[a["name"]] = cls
            print(f"  {a['name']}: {len(cls)} hourly candles (last â¹{cls[-1]['close']:,.2f})")
        except Exception as exc:  # noqa: BLE001
            print(f"  {a['name']}: SKIPPED ({exc})")
    if not candles:
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
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    path = SWEEP_DIR / "results.csv"
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
    with open(SWEEP_DIR / "equity_top10.csv", "w", newline="") as fh:
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
    print(f" HODL benchmark for the same cash: â¹{hodl['final_value']:,.0f} "
          f"({hodl['pnl_pct']:+.1f}%)")
    print("=" * 108)
    hodl_ret = hodl["pnl_pct"]
    hdr = (f"{'#':>3} {'account':<8}{'entry':>6}{'exit':>6}{'tp%':>6}{'sl%':>6}"
           f"{'pos%':>6}{'trades':>7}{'win%':>6}{'PF':>6}{'ret%':>9}"
           f"{'vsHODL':>8}{'maxDD%':>8}")
    print(hdr)
    for i, r in enumerate(leaderboard[:15], 1):
        print(f"{i:>3} {r['account']:<8}{r['entry_rsi']:>6.0f}{r['exit_rsi']:>6.0f}"
              f"{r['take_profit_pct']:>6.0f}{r['stop_loss_pct']:>6.1f}"
              f"{r['position_size_pct']:>6.0f}{r['round_trips']:>7}"
              f"{r['win_rate']:>6.0f}{r['profit_factor']:>6.2f}"
              f"{r['pnl_pct']:>+9.2f}{r['pnl_pct'] - hodl_ret:>+8.2f}"
              f"{r['max_drawdown_pct']:>8.1f}")
    if len(leaderboard) > 15:
        print(f"  ... {len(leaderboard) - 15} more accounts (see sweep/results.csv)")
    worst = leaderboard[-3:]
    print("\n Bottom 3 (what to avoid):")
    for r in worst:
        print(f"   {r['account']}  entryâ¤{r['entry_rsi']:.0f} exitâ¥{r['exit_rsi']:.0f} "
              f"tp{r['take_profit_pct']:.0f}% sl{r['stop_loss_pct']:.1f}% "
              f"pos{r['position_size_pct']:.0f}% -> "
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
    path = SWEEP_DIR / "best_strategy.yaml"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f"# Winner of the {len(leaderboard)}-account tournament "
                 f"({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})\n")
        fh.write(f"# account {best['account']} | {best['name']} | "
                 f"return {best['pnl_pct']:+.2f}% | maxDD {best['max_drawdown_pct']:.1f}%\n")
        fh.write("# Use:  python3 bot.py --config sweep/best_strategy.yaml check\n")
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
    if ACCOUNT_CSV.exists():
        rows = load_account_rows()
        old_sig = SIGNATURE_FILE.read_text().strip() if SIGNATURE_FILE.exists() else ""
        if len(rows) != desired or old_sig != sig:
            needs_wipe = True
            print(f"Strategy grid changed (accounts {len(rows)} -> {desired} or "
                  f"different parameters) â restarting ALL demo accounts.")
    if needs_wipe:
        shutil.rmtree(ACCOUNTS_DIR, ignore_errors=True)
        for leftover in ("live_summary.csv",):
            p = SWEEP_DIR / leftover
            if p.exists():
                p.unlink()
        print("All demo accounts wiped & will restart at "
              f"â¹{sweep_start_cash(cfg):,.0f} each with their new unique strategy.")

    if not ACCOUNT_CSV.exists() or needs_wipe:
        rows = account_grid(cfg, desired)
        save_account_rows(rows)
    else:
        rows = load_account_rows()
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    SIGNATURE_FILE.write_text(sig)

    trade_rows = rows[:top_n] if top_n else rows
    coin = make_coin(cfg)
    cache = build_market_cache(cfg, coin)
    if cache:
        print(f"Live signal data cached for {len(cache)} assets "
              f"(shared by all {len(trade_rows)} traded accounts).")
    start_cash = sweep_start_cash(cfg)
    if not rank_only:
        for row in trade_rows:
            cfg2 = apply_overrides(cfg, row)
            cfg2["initial_cash_inr"] = start_cash   # every account starts at â¹10,000
            broker = make_broker(cfg2, state_dir=ACCOUNTS_DIR / row["account"])
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
            prices[a["name"]] = float(t["last_price"])
    out = []
    for row in rows:
        broker = make_broker(cfg, state_dir=ACCOUNTS_DIR / row["account"])
        mv = broker.market_value(prices)
        out.append({**row, "value": broker.cash + mv, "cash": broker.cash,
                    "holdings": mv, "realized": broker.realized_pnl,
                    "trades": len(broker.read_trades())})
    out.sort(key=lambda r: r["value"], reverse=True)
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(SWEEP_DIR / "live_summary.csv", "w", newline="") as fh:
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
        print(f"  ... {len(out) - 15} more (see sweep/live_summary.csv)")


# =============================================================================
# bot.py
# =============================================================================
HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"


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
    print(f"  The bot will buy only when conditions are met (hourly RSI check), "
          f"and sell on take-profit / stop-loss / overbought.")


def _print_price_header():
    print(f"\n{'ASSET':<8}{'QTY':>14}{'LAST PRICE':>16}{'VALUE':>14}"
          f"{'AVG COST':>14}{'P&L':>14}{'P&L%':>9}")


def cmd_status(cfg: dict, args) -> None:
    broker = make_broker(cfg)
    coin = make_coin(cfg)
    tickers = coin.tickers()
    prices = {}
    _print_price_header()
    for a in cfg["assets"]:
        t = tickers.get(a["name"])
        price = float(t["last_price"]) if t else 0.0
        prices[a["name"]] = price
        pos = broker.positions.get(a["name"])
        if pos and pos.qty > 0:
            pnl = pos.qty * (price - pos.avg_cost)
            pnl_pct = (price / pos.avg_cost - 1) * 100 if pos.avg_cost else 0.0
            print(f"{a['name']:<8}{pos.qty:>14,.8f}{price:>16,.2f}{pos.qty * price:>14,.2f}"
                  f"{pos.avg_cost:>14,.2f}{pnl:>+14,.2f}{pnl_pct:>+8.1f}%")
        else:
            print(f"{a['name']:<8}{'-':>14}{price:>16,.2f}{'-':>14}{'-':>14}{'-':>14}{'-':>9}")
    mv = broker.market_value(prices)
    print(f"\nCash: â¹{broker.cash:,.2f} | Holdings value: â¹{mv:,.2f} | "
          f"Total: â¹{broker.cash + mv:,.2f}")
    print(f"Unrealized P&L: â¹{broker.unrealized_pnl(prices):+,.2f} | "
          f"Realized P&L: â¹{broker.realized_pnl:+,.2f}")
    tax = broker.tax_summary()
    if tax["sell_count"]:
        print(f"\n--- Tax (paper estimate) ---\n"
              f"Sells logged: {tax['sell_count']}\n"
              f"Realized P&L total: â¹{tax['total_realized']:,.2f}\n"
              f"Gross gains (taxable @30%): â¹{tax['gross_gains']:,.2f}\n"
              f"Gross losses (NOT offsettable in India): â¹{tax['gross_losses']:,.2f}\n"
              f"Estimated tax on gains: â¹{tax['estimated_tax_30pct']:,.2f}\n"
              f"1% TDS withheld (claim as credit): â¹{tax['tds_credit']:,.2f}")
    print("(See tax_notes.md â this is an estimate, not tax advice.)")


def cmd_check(cfg: dict, args) -> None:
    run_cycle(cfg, make_broker(cfg), make_coin(cfg))


def cmd_run(cfg: dict, args) -> None:
    interval = int(cfg.get("check_interval_min", 60))
    print(f"Daemon running â checking every {interval} min (Ctrl+C to stop).")
    while True:
        try:
            run_cycle(cfg, make_broker(cfg), make_coin(cfg))
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 â daemon must not die
            print(f"  ! cycle error: {exc}")
        time.sleep(interval * 60)


def cmd_reset(cfg: dict, args) -> None:
    if not args.yes:
        print("This deletes state/ (paper portfolio + trade log). Pass --yes to confirm.")
        return
    for f in ("portfolio.json", "trades.csv"):
        p = STATE_DIR / f
        if p.exists():
            p.unlink()
    print("Paper state reset.")


def cmd_backtest(cfg: dict, args) -> None:
    run_backtest(cfg, days=args.days, chart_path=args.chart)


def cmd_sweep(cfg: dict, args) -> None:
    run_sweep(cfg, days=args.days, count=args.count, chart_path=args.chart)


def cmd_sweep_live(cfg: dict, args) -> None:
    live_sweep(cfg, top_n=args.top, rank_only=args.rank_only, reset=args.reset)


def cmd_sweep_status(cfg: dict, args) -> None:
    live_sweep(cfg, rank_only=True)


# ------------------------------------------------------------------------ main
def main():
    parser = argparse.ArgumentParser(description="CoinDCX signal trading bot (paper)")
    parser.add_argument("--config", default=str(HERE / "config.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create/reset paper portfolio")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="portfolio & tax view")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="run one signal-check cycle")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="run forever (hourly checker)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("reset", help="delete paper state")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("backtest", help="backtest signal strategy on 1h data")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--chart", default=None)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("sweep", help="100-account strategy tournament (history)")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--chart", default=None)
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("sweep-live", help="run all demo accounts live (paper)")
    p.add_argument("--top", type=int, default=None, help="only first N accounts")
    p.add_argument("--rank-only", action="store_true", help="no trading, just ranking")
    p.add_argument("--reset", action="store_true",
                   help="wipe all demo accounts & restart at â¹10,000 each")
    p.set_defaults(func=cmd_sweep_live)

    p = sub.add_parser("sweep-status", help="live ranking of demo accounts")
    p.set_defaults(func=cmd_sweep_status)

    args = parser.parse_args()
    cfg = load_cfg(Path(args.config))
    args.func(cfg, args)


if __name__ == "__main__":
    main()
