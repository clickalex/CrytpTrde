#!/usr/bin/env python3
"""Offline tests for the parallel-fetch speedup (no network needed).

Stubs CoinDCX endpoints with realistic latency and checks:
  1. build_market_cache: parallel == serial results, and much faster
  2. CoinDCX global rate limiter: request starts are spaced across threads
  3. fetch_history: parallel backtest-style fetch works and is faster
  4. discover_inr_markets: min_volume / max_assets filtering (config caps)
  5. config.yaml: parses; caps flow through resolve_assets
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cryptobot as cb  # noqa: E402

LAT = 0.30  # simulated network round-trip per request


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeCoin:
    """Duck-typed stand-in for CoinDCX with simulated latency."""

    def __init__(self, n_assets=12, fail_every=0):
        self.names = [f"A{i}INR" for i in range(n_assets)]
        self.fail_every = fail_every
        self.candle_calls = 0
        self._lock = threading.Lock()
        self._mk = {
            nm: {"coindcx_name": nm, "pair": f"I-A{i}_INR", "status": "active",
                 "base_currency_short_name": "INR", "ecode": "I",
                 "order_types": ["market_order"], "step": 1e-6,
                 "target_currency_precision": 6, "min_notional": 100}
            for i, nm in enumerate(self.names)
        }
        self._tk = {nm: {"bid": 100.0 + i, "ask": 101.0 + i, "last_price": 100.0 + i,
                         "volume": 1000.0 - i}
                    for i, nm in enumerate(self.names)}

    # -- CoinDCX API surface -------------------------------------------------
    def tickers(self, refresh=False):
        time.sleep(LAT)
        return dict(self._tk)

    def market(self, name):
        return self._mk.get(name, {})

    def markets_details(self, refresh=False):
        time.sleep(LAT)
        return dict(self._mk)

    def candles(self, pair, interval="1h", start_ms=None, end_ms=None, limit=200):
        with self._lock:
            self.candle_calls += 1
            n = self.fail_every and self.candle_calls % self.fail_every == 0
        time.sleep(LAT)
        if n:
            raise cb.CoinDCXError("simulated 5xx")
        base = 100.0
        t0 = 1_700_000_000_000
        return [{"open": base, "high": base, "low": base, "close": base,
                 "volume": 1.0, "time": t0 + k * 3_600_000} for k in range(50)]

    def best_bid_ask(self, pair):
        time.sleep(LAT)
        return 100.0, 101.0

    def discover_inr_markets(self, min_volume_inr=0.0, limit=None,
                             require_market_order=True):
        # reuse the real filtering logic against stubbed payloads
        real = cb.CoinDCX.discover_inr_markets
        return real(self, min_volume_inr=min_volume_inr, limit=limit,
                    require_market_order=require_market_order)


def test_build_market_cache():
    cfg = {"strategy": {"timeframe": "1h", "signal_lookback": 50},
           "network": {"fetch_workers": 8}}
    assets = [{"name": n, "pair": f"I-{n[:-3]}_INR"} for n in FakeCoin(12).names]

    coin_serial = FakeCoin(12)
    t0 = time.monotonic()
    serial = cb.build_market_cache({**cfg, "network": {"fetch_workers": 1}},
                                   coin_serial, assets)
    t_serial = time.monotonic() - t0

    coin_par = FakeCoin(12)
    t0 = time.monotonic()
    par = cb.build_market_cache(cfg, coin_par, assets)
    t_par = time.monotonic() - t0

    assert serial == par, "parallel cache differs from serial cache"
    assert len(par) == 12 and all(len(c) == 50 for c, _, _ in par.values())
    speedup = t_serial / t_par
    print(f"  build_market_cache: serial {t_serial:.2f}s -> parallel {t_par:.2f}s "
          f"({speedup:.1f}x), identical results")
    assert t_par < t_serial * 0.6, f"expected speedup, got {t_par:.2f}s vs {t_serial:.2f}s"

    # failing assets must not kill the others (12 calls, every 5th fails -> 2 dead)
    coin_flaky = FakeCoin(12, fail_every=5)
    out = cb.build_market_cache(cfg, coin_flaky, assets)
    assert len(out) == 10, f"expected 10 survivors, got {len(out)}"


def test_rate_limiter_global_pacing():
    interval = 0.05
    coin = cb.CoinDCX(request_interval=interval)
    starts = []
    lock = threading.Lock()

    def fake_get(url, params=None, timeout=0):
        with lock:
            starts.append(time.monotonic())
        time.sleep(0.01)  # fast "server"
        return FakeResp([{"open": 1, "high": 1, "low": 1, "close": 1,
                          "volume": 1, "time": 1}])

    orig_get = cb.requests.get
    cb.requests.get = fake_get
    try:
        threads = [threading.Thread(target=lambda i=i: coin.candles(f"I-A{i}_INR"))
                   for i in range(10)]
        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = time.monotonic() - t0
    finally:
        cb.requests.get = orig_get

    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    worst = min(gaps)
    print(f"  rate limiter: 10 concurrent calls, min gap {worst*1000:.1f}ms "
          f"(cap {interval*1000:.0f}ms), total {total:.2f}s")
    assert worst >= interval * 0.9, f"limiter violated: gap {worst:.4f}s < {interval}s"
    assert total >= interval * 9, "calls finished too fast - limiter not applied"


def test_fetch_history():
    cfg = {"strategy": {}, "network": {"fetch_workers": 8}}
    assets = [{"name": n, "pair": f"I-{n[:-3]}_INR"} for n in FakeCoin(8).names]
    coin = FakeCoin(8)
    t0 = time.monotonic()
    out = cb.fetch_history(cfg, coin, assets, days=30)
    dt = time.monotonic() - t0
    assert len(out) == 8 and all(len(v) == 50 for v in out.values())
    print(f"  fetch_history: 8 assets in {dt:.2f}s (serial would be >= {LAT*8:.1f}s)")
    assert dt < LAT * 8 * 0.7


def test_discovery_caps():
    coin = FakeCoin(12)  # turnover(i) = (100+i)*(1000-i), ascending in i
    top5 = coin.discover_inr_markets(min_volume_inr=0, limit=5)
    assert len(top5) == 5, top5
    assert all(a["pair"] for a in top5)
    assert top5[0]["name"] == "A11INR", top5[0]  # highest turnover first
    liquid = coin.discover_inr_markets(min_volume_inr=100_000)
    assert len(liquid) == 12  # every turnover >= 100k
    stricter = coin.discover_inr_markets(min_volume_inr=105_000)
    assert len(stricter) == 6, stricter  # turnover(5)=104,475 < 105k <= turnover(6)=105,364
    print(f"  discover_inr_markets: limit=5 -> 5 (top by turnover), "
          f"min_volume 1L -> {len(liquid)}, 1.05L -> {len(stricter)}")


def test_config_caps_flow():
    cfg = cb.load_cfg(Path(__file__).resolve().parent / "config.yaml")
    assert cfg["assets"] == "auto"
    assert cfg["asset_discovery"]["max_assets"] == 50
    assert cfg["asset_discovery"]["min_volume_inr"] == 1000000
    assert cfg["network"]["fetch_workers"] == 8
    # resolve_assets should pass the caps to discovery
    fake = FakeCoin(12)
    for i, t in enumerate(fake._tk.values()):  # turnover ~ (100+i)*50k = Rs.50L+
        t["volume"] = 50000.0
    orig = cb.make_public_coin
    cb.make_public_coin = lambda c: fake
    orig_disc = fake.discover_inr_markets
    seen = {}

    def spy(min_volume_inr=0.0, limit=None, require_market_order=True):
        seen["min_volume"], seen["limit"] = min_volume_inr, limit
        return orig_disc(min_volume_inr=min_volume_inr, limit=limit,
                         require_market_order=require_market_order)
    fake.discover_inr_markets = spy
    try:
        assets = cb.resolve_assets(cfg)
    finally:
        cb.make_public_coin = orig
        del fake.discover_inr_markets
    assert seen["min_volume"] == 1000000.0 and seen["limit"] == 50, seen
    assert len(assets) == 12, f"expected 12 liquid fake assets, got {len(assets)}"
    print(f"  resolve_assets: min_volume={seen['min_volume']:,.0f} max_assets={seen['limit']} "
          f"-> {len(assets)} assets")


if __name__ == "__main__":
    tests = [test_build_market_cache, test_rate_limiter_global_pacing,
             test_fetch_history, test_discovery_caps, test_config_caps_flow]
    for t in tests:
        print(f"* {t.__name__}")
        t()
    print("\nALL TESTS PASSED")
