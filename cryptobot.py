#!/usr/bin/env python3
# cryptobot.py — CryptoBot for CoinDCX (India) — SINGLE-FILE build.
#
# A paper-trading bot that scans INR spot markets on CoinDCX, computes hourly
# RSI signals, and fills a simulated portfolio (fees + slippage + 1% TDS).
# Nothing here places real orders unless you explicitly enable live mode.
#
# Command examples:
#   python3 cryptobot.py status           portfolio & tax view
#   python3 cryptobot.py check            run one RSI signal-check cycle now
#   python3 cryptobot.py run              hourly checker loop (daemon)
#   python3 cryptobot.py assets           list the discovered/resolved universe
#   python3 cryptobot.py backtest         RSI swing vs HODL on real 1h data
#   python3 cryptobot.py sweep            200-account strategy tournament
#   python3 cryptobot.py sweep-live       tournament on live prices (paper)
# See README.md for the full guide and config.yaml for every knob.
#
# File layout (sections were once separate modules; kept as markers):
#   indicators.py  - RSI / SMA math
#   coindcx.py     - API client: global rate limiting, retries, discovery
#   broker.py      - PaperBroker: fills, fee/TDS accounting, persistence
#   engine.py      - market-data cache + run_cycle (the live decision loop)
#   backtest.py    - historical replay (_simulate) + HODL benchmark
#   sweep.py       - strategy grid, tournament sim, live tournament
#   bot.py         - CLI commands and main()
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
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import yaml


# =============================================================================
# indicators.py
# =============================================================================
def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None when there is not enough data.

    RSI = 100 - 100/(1+RS), where RS = avg gain / avg loss over `period`
    bars. Value is 0..100: high = overbought (recent bars mostly up),
    low = oversold. The bot BUYS oversold dips (RSI <= entry_rsi) and can
    exit overbought holds (RSI >= exit_rsi).
    """
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
    """Simple moving average of the last `period` values (None if too short)."""
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


# =============================================================================
# coindcx.py
# =============================================================================
API_BASE = "https://api.coindcx.com"
PUBLIC_BASE = "https://public.coindcx.com"


class CoinDCXError(RuntimeError):
    pass


class CoinDCX:
    """CoinDCX REST client (thread-safe).

    Public market data (tickers, market rules, candles, order books) needs no
    auth; private_post/create_market_order are only used by live mode. All
    requests go through _get_json, which (a) reserves a GLOBAL pacing slot so
    request starts stay `request_interval` apart no matter how many worker
    threads call concurrently, and (b) retries transient failures with
    exponential backoff while backing the global pace off after failures
    (adaptive backoff). Responses are cached per instance: tickers/markets
    are fetched once and reused across the ~500-market scans.
    """

    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 timeout: int = 20, request_interval: float = 0.0):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        # Optional throttle for scanning hundreds of markets without hammering
        # CoinDCX's public endpoints.
        self.request_interval = max(0.0, request_interval)
        self._last_request_at = 0.0
        self._tickers: dict[str, dict] | None = None
        self._markets: dict[str, dict] | None = None
        # Thread-safe pacing/caching so parallel market-data fetching never
        # exceeds the configured requests/sec cap or double-fetches caches.
        self._rate_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        # Keep-alive connection pool: parallel workers reuse TCP/TLS
        # connections instead of paying a handshake per request.
        self._session = requests.Session()
        # Adaptive politeness: after any failed request, slow the global pace
        # (3x interval) for a while instead of hammering a struggling server.
        self._penalty_until = 0.0

    # ------------------------------------------------------------------ public
    def _reserve_request_slot(self) -> None:
        """Global request pacing across ALL worker threads.

        Request STARTS are spaced at least `request_interval` apart no matter
        how many threads are fetching, so parallel fetching overlaps network
        latency without exceeding the configured requests/sec cap. While a
        recent request FAILED, the pace triples (adaptive backoff).
        """
        if not self.request_interval:
            return
        with self._rate_lock:
            now = time.monotonic()
            interval = self.request_interval
            if now < self._penalty_until:
                interval *= 3.0
            slot = max(now, self._last_request_at + interval)
            self._last_request_at = slot
        wait = slot - now
        if wait > 0:
            time.sleep(wait)

    def _note_failure(self, retry_after: float | None = None) -> None:
        """Back off globally after a failed/429/5xx request."""
        penalty = float(retry_after) if retry_after is not None else 10.0
        penalty = min(max(penalty, 5.0), 60.0)
        with self._rate_lock:
            self._penalty_until = max(self._penalty_until,
                                      time.monotonic() + penalty)

    def _get_json(self, url: str, params: dict | None = None,
                  retries: int = 3):
        """GET `url` and return the parsed JSON body.

        Retry policy per call (default 3 attempts):
          - success                           -> return data
          - 4xx client error (except 408/429) -> fail IMMEDIATELY, no retry,
            and no global penalty (a bad pair is our bug, not the server's)
          - 429 / 5xx / network / bad JSON    -> note failure (global pace
            slows to 1/3 for ~10s, longer if the server sends Retry-After),
            then retry after a 2s/4s backoff
        Raises CoinDCXError when every attempt failed.
        """
        last_exc: Exception | None = None
        for attempt in range(1, max(1, retries) + 1):
            self._reserve_request_slot()
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and data.get("status") == "error":
                    self._note_failure()
                    raise CoinDCXError(
                        f"CoinDCX error {data.get('code')}: {data.get('message')}")
                return data
            except requests.HTTPError as exc:
                last_exc = exc
                resp_exc = getattr(exc, "response", None)
                status = int(getattr(resp_exc, "status_code", 0) or 0)
                if 400 <= status < 500 and status not in (408, 429):
                    # Deterministic client error (bad pair, bad param, auth):
                    # retrying cannot help and it says nothing about server
                    # health, so fail fast without penalising the global pace.
                    raise CoinDCXError(
                        f"CoinDCX client error {status} on {url}: {exc}") from exc
                retry_after = None
                try:
                    retry_after = float(resp_exc.headers.get("Retry-After"))
                except (TypeError, ValueError, AttributeError):
                    retry_after = None
                self._note_failure(retry_after)
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 5))
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                self._note_failure()
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 5))
        raise CoinDCXError(f"Network error hitting {url}: {last_exc}") from last_exc

    def tickers(self, refresh: bool = False) -> dict[str, dict]:
        """All tickers keyed by market name, e.g. {"BTCINR": {...}}."""
        if self._tickers is None or refresh:
            with self._cache_lock:
                if self._tickers is None or refresh:
                    data = self._get_json(f"{API_BASE}/exchange/ticker")
                    if not isinstance(data, list):
                        raise CoinDCXError(
                            f"Unexpected ticker response: {str(data)[:200]}")
                    self._tickers = {
                        t["market"]: t for t in data
                        if isinstance(t, dict) and t.get("market")
                    }
        return self._tickers

    def ticker(self, market: str, refresh: bool = False) -> dict:
        t = self.tickers(refresh).get(market)
        if not t:
            raise CoinDCXError(f"Unknown market '{market}' (check coindcx_name, e.g. BTCINR)")
        return t

    def markets_details(self, refresh: bool = False) -> dict[str, dict]:
        """Market rules keyed by coindcx_name (e.g. 'BTCINR'): precision, step, min_notional."""
        if self._markets is None or refresh:
            with self._cache_lock:
                if self._markets is None or refresh:
                    data = self._get_json(f"{API_BASE}/exchange/v1/markets_details")
                    self._markets = {m["coindcx_name"]: m for m in data}
        return self._markets

    def market(self, name: str) -> dict:
        return self.markets_details().get(name) or {}

    def discover_inr_markets(self, min_volume_inr: float = 0.0,
                             limit: int | None = None,
                             require_market_order: bool = True) -> list[dict]:
        """Return active INR spot markets, sorted by 24h INR turnover.

        This lets the bot scan every eligible CoinDCX INR pair instead of being
        limited to the hard-coded BTC/ETH/SOL list. The result contains the same
        ``{"name": "BTCINR", "pair": "I-BTC_INR"}`` shape used by config.yaml.
        """
        details = self.markets_details(refresh=True)
        tickers = self.tickers(refresh=True)

        discovered: list[tuple[float, dict]] = []
        for md in details.values():
            if str(md.get("status", "")).lower() != "active":
                continue
            if str(md.get("base_currency_short_name", "")).upper() != "INR":
                continue
            # CoinDCX's own INR spot book uses ecode "I"; pairs from other venues
            # may end in INR but are not always the same spot market/order route.
            if str(md.get("ecode", "")).upper() != "I":
                continue
            if require_market_order and "market_order" not in (md.get("order_types") or []):
                continue

            name = str(md.get("coindcx_name") or "")
            pair = str(md.get("pair") or "")
            if not name or not pair:
                continue

            t = tickers.get(name, {})
            try:
                last = float(t.get("last_price") or 0.0)
                volume = float(t.get("volume") or 0.0)
            except (TypeError, ValueError):
                last, volume = 0.0, 0.0
            turnover = last * volume
            if turnover < min_volume_inr:
                continue

            discovered.append((turnover, {"name": name, "pair": pair}))

        discovered.sort(key=lambda item: item[0], reverse=True)
        assets = [asset for _, asset in discovered]
        if limit is not None and limit > 0:
            assets = assets[:limit]
        return assets

    def dipped_inr_markets(self, dip_pct: float = 8.0, limit: int = 150,
                           exclude: set[str] | None = None,
                           require_market_order: bool = True) -> list[dict]:
        """Active INR spot markets that fell hard in the last 24h.

        Safety net for top-N turnover caps: an hourly-RSI oversold dip almost
        always coincides with a sharp 24h drop, and the all-markets ticker
        list (already fetched for discovery) carries 24h change/high/low for
        EVERY market - so crashed coins outside the liquidity cap get caught
        at zero extra API cost. A market qualifies if its 24h change is
        <= -dip_pct OR its last price is within 2% of the 24h low while
        trading meaningfully (>= 2%) below the 24h high - flat/stale books
        with low == high == last are NOT dips.
        """
        exclude = exclude or set()
        details = self.markets_details()
        tickers = self.tickers()

        out: list[tuple[float, dict]] = []
        for md in details.values():
            if str(md.get("status", "")).lower() != "active":
                continue
            if str(md.get("base_currency_short_name", "")).upper() != "INR":
                continue
            if str(md.get("ecode", "")).upper() != "I":
                continue
            if require_market_order and "market_order" not in (md.get("order_types") or []):
                continue
            name = str(md.get("coindcx_name") or "")
            pair = str(md.get("pair") or "")
            if not name or not pair or name in exclude:
                continue
            t = tickers.get(name, {})
            try:
                chg = float(t.get("change_24_hour") or 0.0)
                last = float(t.get("last_price") or 0.0)
                low = float(t.get("low") or 0.0)
                high = float(t.get("high") or 0.0)
            except (TypeError, ValueError):
                continue
            # "Near the low" only counts when the market actually has a 24h
            # range and sits meaningfully below its high. A stale book that
            # traded flat all day has low == high == last (0% change) and is
            # NOT a dip - without the high check every flat market qualified.
            off_high = high > 0 and last <= high * 0.98
            near_low = last > 0 and low > 0 and off_high and last <= low * 1.02
            if chg <= -dip_pct or near_low:
                out.append((chg, {"name": name, "pair": pair}))

        out.sort(key=lambda item: item[0])   # biggest drop first
        assets = [asset for _, asset in out]
        if limit is not None and limit > 0:
            assets = assets[:limit]
        return assets

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
        # Prefer market metadata (e.g. I-BTC_INR -> BTCINR) and only fall back
        # to string parsing for the usual I-ASSET_INR form.
        market = next((name for name, md in self.markets_details().items()
                       if md.get("pair") == pair), "")
        if not market and "_" in pair:
            parts = pair.split("_", 1)
            prefix, base = parts[0].split("-", 1) if "-" in parts[0] else ("", parts[0])
            market = f"{base}{parts[-1]}" if prefix == "I" else ""
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
    if step > 0:
        return round(int(raw / step) * step, precision)
    # step-less market: floor on the precision grid instead
    return round(int(raw * (10 ** precision)) / (10 ** precision), precision)


def env_keys(api_key_env: str, api_secret_env: str) -> tuple[str | None, str | None]:
    return os.environ.get(api_key_env), os.environ.get(api_secret_env)


# =============================================================================
# paper_broker.py
# =============================================================================
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

    Accounting invariants (verified by test_speed_fix.py):
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

    state_dirs = [Path(__file__).resolve().parent / "state"]
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


# =============================================================================
# backtest.py
# =============================================================================
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
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACCOUNT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["account", "name"] + PARAM_KEYS)
        w.writeheader()
        w.writerows(rows)
    return ACCOUNT_CSV


def load_account_rows() -> list[dict]:
    """Read sweep/accounts.csv, restoring numeric types for numeric params.
    String params (entry_mode) are kept as-is - float() would crash on them."""
    with open(ACCOUNT_CSV) as fh:
        rows = []
        for r in csv.DictReader(fh):
            for k in PARAM_KEYS:
                try:
                    r[k] = float(r[k])
                except (TypeError, ValueError):
                    pass  # not numeric (entry_mode) - keep the string
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
        print(f"  ... {len(leaderboard) - 15} more accounts (see sweep/results.csv)")
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
    start_cash = sweep_start_cash(cfg)
    if not rank_only:
        cache = build_market_cache(cfg, coin)
        if cache:
            print(f"Live signal data cached for {len(cache)} assets "
                  f"(shared by all {len(trade_rows)} traded accounts).")
        for row in trade_rows:
            cfg2 = apply_overrides(cfg, row)
            cfg2["initial_cash_inr"] = start_cash   # every account starts at ₹10,000
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
            try:
                prices[a["name"]] = float(t.get("last_price") or 0.0)
            except (TypeError, ValueError):
                continue
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
    print("(See tax_notes.md - this is an estimate, not tax advice.)")


def cmd_assets(cfg: dict, args) -> None:
    assets = cfg["assets"]
    print(f"Resolved {len(assets)} assets:")
    for a in assets:
        print(f"  {a['name']:<16} {a['pair']}")


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
                       help="100-account strategy tournament (history)")
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

    args = parser.parse_args()
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

        extra_state_dirs = []
        if args.command in {"sweep-live", "sweep-status"} and ACCOUNTS_DIR.exists():
            extra_state_dirs = [p for p in ACCOUNTS_DIR.iterdir() if p.is_dir()]
        # backtest/sweep compare strategies over a STABLE universe -> no
        # dipped-market extras there (they flap hour to hour); live checks
        # (check/run/sweep-live/status) do want them so nothing crashed is missed.
        include_dipped = args.command not in {"backtest", "sweep"}
        cfg["assets"] = resolve_assets(cfg, force_all=args.all_assets,
                                       extra_state_dirs=extra_state_dirs,
                                       include_dipped=include_dipped)

        args.func(cfg, args)
    except CoinDCXError as exc:
        print(f"CoinDCX request failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
