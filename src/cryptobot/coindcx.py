"""CoinDCX REST client: public market data and optional live endpoints."""

import hashlib
import hmac
import json
import os
import threading
import time

import requests

API_BASE = "https://api.coindcx.com"
PUBLIC_BASE = "https://public.coindcx.com"


class CoinDCXError(RuntimeError):
    pass


def _json_rows(data, keys: tuple[str, ...] = ()) -> list:
    """Normalise CoinDCX JSON into a list of dict rows.

    The public API usually returns a bare list. Some responses wrap it in
    ``{"data": [...]}`` or send a dict keyed by market name. Iterating a
    dict as if it were a list of objects raises TypeError and GitHub
    Actions only reports that as 'Process completed with exit code 1'.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
        if data and all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


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
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        })
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
                  retries: int = 5):
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
                # 403 is often Cloudflare blocking datacenter IPs (GitHub
                # Actions). Treat it like 429: back off and retry. Other 4xx
                # (bad pair / bad param) still fail immediately.
                if 400 <= status < 500 and status not in (403, 408, 429):
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
                    rows = _json_rows(data, keys=("markets", "data", "ticker"))
                    self._tickers = {
                        t["market"]: t for t in rows
                        if isinstance(t, dict) and t.get("market")
                    }
                    if not self._tickers:
                        raise CoinDCXError(
                            f"Unexpected ticker response: {str(data)[:200]}")
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
                    rows = _json_rows(data, keys=("markets", "data", "markets_details"))
                    self._markets = {
                        m["coindcx_name"]: m for m in rows
                        if isinstance(m, dict) and m.get("coindcx_name")
                    }
                    if not self._markets:
                        raise CoinDCXError(
                            f"Unexpected markets_details response: {str(data)[:200]}")
        return self._markets

    def market(self, name: str) -> dict:
        return self.markets_details().get(name) or {}

    def discover_inr_markets(self, min_volume_inr: float = 0.0,
                             limit: int | None = None,
                             require_market_order: bool = True) -> list[dict]:
        """Return active INR spot markets, sorted by 24h INR turnover.

        This lets the bot scan every eligible CoinDCX INR pair instead of being
        limited to the hard-coded BTC/ETH/SOL list. The result contains the same
        ``{"name": "BTCINR", "pair": "I-BTC_INR"}`` shape used by config/config.yaml.
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
