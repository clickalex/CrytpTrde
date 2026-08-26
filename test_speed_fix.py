#!/usr/bin/env python3
"""Offline tests for the parallel-fetch speedup + con fixes (no network).

Stubs CoinDCX endpoints with realistic latency and checks:
  1. build_market_cache: parallel == serial results, and much faster
  2. CoinDCX global rate limiter: request starts are spaced across threads
  3. fetch_history: parallel backtest-style fetch works and is faster
  4. discover_inr_markets: min_volume / max_assets filtering (config caps)
  5. config.yaml: parses; caps flow through resolve_assets
  6. dipped-markets safety net: crashed coins outside the cap get scanned
  7. adaptive rate backoff: pace triples after a failed request
  8. dead markets are skipped without orderbook/ticker-refetch fallbacks
  9. progress lines appear on long scans
  10. run_cycle smoke: oversold candles -> BUY, rally -> take-profit SELL
  11. PaperBroker round-trip: fee/TDS/PnL accounting, persistence, rejections
"""
import io
import sys
import tempfile
import threading
import time
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cryptobot as cb  # noqa: E402
import requests as requests_mod  # noqa: E402

LAT = 0.30  # simulated network round-trip per request


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests_mod.exceptions.HTTPError(
                f"{self.status_code} error", response=self)

    def json(self):
        return self._payload


class FakeCoin:
    """Duck-typed stand-in for CoinDCX with simulated latency."""

    def __init__(self, n_assets=12, fail_every=0):
        self.names = [f"A{i}INR" for i in range(n_assets)]
        self.fail_every = fail_every
        self.candle_calls = 0
        self.book_calls = 0
        self._lock = threading.Lock()
        self._mk = {
            nm: {"coindcx_name": nm, "pair": f"I-A{i}_INR", "status": "active",
                 "base_currency_short_name": "INR", "ecode": "I",
                 "order_types": ["market_order"], "step": 1e-6,
                 "target_currency_precision": 6, "min_notional": 100}
            for i, nm in enumerate(self.names)
        }
        # field names/values mirror the real /exchange/ticker payload (strings)
        self._tk = {nm: {"bid": str(100.0 + i), "ask": str(101.0 + i),
                         "last_price": str(100.0 + i), "volume": str(1000.0 - i),
                         "change_24_hour": "0.0", "low": "50.0", "high": "200.0"}
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
        self.book_calls += 1
        time.sleep(LAT)
        return 100.0, 101.0

    def discover_inr_markets(self, min_volume_inr=0.0, limit=None,
                             require_market_order=True):
        real = cb.CoinDCX.discover_inr_markets
        return real(self, min_volume_inr=min_volume_inr, limit=limit,
                    require_market_order=require_market_order)

    def dipped_inr_markets(self, dip_pct=8.0, limit=150, exclude=None,
                           require_market_order=True):
        real = cb.CoinDCX.dipped_inr_markets
        return real(self, dip_pct=dip_pct, limit=limit, exclude=exclude,
                    require_market_order=require_market_order)


def asset_list(coin):
    return [{"name": n, "pair": coin._mk[n]["pair"]} for n in coin.names]


def test_build_market_cache():
    cfg = {"strategy": {"timeframe": "1h", "signal_lookback": 50},
           "network": {"fetch_workers": 8}}
    assets = asset_list(FakeCoin(12))

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

    orig_get = coin._session.get
    coin._session.get = fake_get
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
        coin._session.get = orig_get

    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    worst = min(gaps)
    print(f"  rate limiter: 10 concurrent calls, min gap {worst*1000:.1f}ms "
          f"(cap {interval*1000:.0f}ms), total {total:.2f}s")
    assert worst >= interval * 0.9, f"limiter violated: gap {worst:.4f}s < {interval}s"
    assert total >= interval * 9, "calls finished too fast - limiter not applied"


def test_fetch_history():
    cfg = {"strategy": {}, "network": {"fetch_workers": 8}}
    assets = asset_list(FakeCoin(8))
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


def _patched_public_coin(fake):
    orig = cb.make_public_coin
    cb.make_public_coin = lambda c: fake
    return orig


def test_config_caps_flow():
    cfg = cb.load_cfg(Path(__file__).resolve().parent / "config.yaml")
    assert cfg["assets"] == "auto"
    assert cfg["asset_discovery"]["max_assets"] == 50
    assert cfg["asset_discovery"]["min_volume_inr"] == 1000000
    assert cfg["asset_discovery"]["dipped_scan_pct"] == 8
    assert cfg["network"]["fetch_workers"] == 8

    fake = FakeCoin(12)
    for i, t in enumerate(fake._tk.values()):  # turnover ~ (100+i)*50k = Rs.50L+
        t["volume"] = "50000.0"
    orig = _patched_public_coin(fake)
    orig_disc = fake.discover_inr_markets
    seen = {}

    def spy(min_volume_inr=0.0, limit=None, require_market_order=True):
        seen["min_volume"], seen["limit"] = min_volume_inr, limit
        return orig_disc(min_volume_inr=min_volume_inr, limit=limit,
                         require_market_order=require_market_order)
    fake.discover_inr_markets = spy
    try:
        assets = cb.resolve_assets(cfg)  # no ticker dipped here (all flat)
    finally:
        cb.make_public_coin = orig
        del fake.discover_inr_markets
    assert seen["min_volume"] == 1000000.0 and seen["limit"] == 50, seen
    assert len(assets) == 12, f"expected 12 liquid fake assets, got {len(assets)}"
    print(f"  resolve_assets: min_volume={seen['min_volume']:,.0f} max_assets={seen['limit']} "
          f"-> {len(assets)} assets")


def test_dipped_scan():
    coin = FakeCoin(12)
    # A3 crashed -18% (biggest drop), A7 near its 24h low, others flat
    coin._tk["A3INR"]["change_24_hour"] = "-18.0"
    coin._tk["A7INR"]["low"] = str(100.0 + 7)  # last == low, but high 200 ->
    #                                            a real 24h range -> within 2%
    # A5 traded dead-flat all day (low == high == last): stale book, NOT a dip
    coin._tk["A5INR"].update({"low": "105.0", "high": "105.0"})
    top3 = {"A9INR", "A10INR", "A11INR"}
    dipped = coin.dipped_inr_markets(dip_pct=8, exclude=top3)
    got = [a["name"] for a in dipped]
    assert got == ["A3INR", "A7INR"], got  # biggest drop first; A5 excluded
    # cap
    dipped_cap = coin.dipped_inr_markets(dip_pct=8, exclude=top3, limit=1)
    assert [a["name"] for a in dipped_cap] == ["A3INR"]
    # already-included markets are excluded
    dipped_none = coin.dipped_inr_markets(dip_pct=8, exclude={"A3INR", "A7INR"})
    assert dipped_none == []

    # and resolve_assets actually adds them (+config gating)
    cfg = {"assets": "auto",
           "asset_discovery": {"min_volume_inr": 100_000, "max_assets": 3,
                               "dipped_scan_pct": 8, "dipped_scan_max": 150}}
    orig = _patched_public_coin(coin)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            assets = cb.resolve_assets(cfg)
        names = [a["name"] for a in assets]
        assert "A3INR" in names and "A7INR" in names, names
        assert len(assets) == 5, names  # top-3 + 2 dipped
        assert "sharply-dipped" in buf.getvalue()
        # gating: dipped extras off for backtest/sweep
        stable = cb.resolve_assets(cfg, include_dipped=False)
        assert [a["name"] for a in stable] == ["A11INR", "A10INR", "A9INR"]
        # off switch: dipped_scan_pct = 0 disables the safety net entirely
        cfg_off = {"assets": "auto",
                   "asset_discovery": {"min_volume_inr": 100_000, "max_assets": 3,
                                       "dipped_scan_pct": 0}}
        off = cb.resolve_assets(cfg_off)
        assert [a["name"] for a in off] == ["A11INR", "A10INR", "A9INR"]
    finally:
        cb.make_public_coin = orig
    print("  dipped scan: crashed (-18%) + near-low markets added; "
          "backtest/sweep keep a stable universe")


def test_adaptive_backoff():
    interval = 0.05
    coin = cb.CoinDCX(request_interval=interval)
    calls = {"n": 0}
    lock = threading.Lock()

    def flaky_get(url, params=None, timeout=0):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n == 1:  # first request blows up
            raise requests_mod.exceptions.ConnectionError("boom")
        return FakeResp([{"open": 1, "high": 1, "low": 1, "close": 1,
                          "volume": 1, "time": 1}])

    orig_get = coin._session.get
    coin._session.get = flaky_get
    try:
        coin.candles("I-A0_INR")   # fails once, retries, then succeeds
        t0 = time.monotonic()
        coin.candles("I-A1_INR")   # must be paced at 3x interval (penalized)
        slow_gap = time.monotonic() - t0
    finally:
        coin._session.get = orig_get
    assert time.monotonic() < coin._penalty_until, "penalty window should be active"
    assert slow_gap >= interval * 3 * 0.8, f"post-error gap {slow_gap:.3f}s too small"

    # deterministic client errors (4xx, e.g. a delisted pair) must fail fast:
    # a single attempt, no retry loop, and NO global penalty - they say
    # nothing about server health (unlike 429/5xx/network failures)
    nf = {"n": 0}

    def notfound_get(url, params=None, timeout=0):
        nf["n"] += 1
        return FakeResp({"error": "not found"}, status=404)

    penalty_before = coin._penalty_until
    coin._session.get = notfound_get
    try:
        try:
            coin.candles("I-GONE_INR")
            raise AssertionError("404 must surface as CoinDCXError")
        except cb.CoinDCXError:
            pass
    finally:
        coin._session.get = orig_get
    assert nf["n"] == 1, f"404 was attempted {nf['n']}x - must fail fast"
    assert coin._penalty_until == penalty_before, "404 must not penalise the global pace"
    print(f"  adaptive backoff: after a failure, next request delayed "
          f"{slow_gap*1000:.0f}ms (3x {interval*1000:.0f}ms cap); "
          f"4xx fails fast with no penalty")


def test_dead_market_skipped():
    cfg = {"strategy": {"timeframe": "1h", "signal_lookback": 50},
           "network": {"fetch_workers": 4}}
    coin = FakeCoin(4)
    # A2 book is dead: no bid/ask/last
    for k in ("bid", "ask", "last_price"):
        coin._tk["A2INR"][k] = "0.00000000"
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = cb.build_market_cache(cfg, coin, asset_list(coin))
    assert "A2INR" not in out and len(out) == 3
    assert coin.book_calls == 0, "dead market must not hit the orderbook fallback"
    assert coin.candle_calls == 3, "dead market must not fetch candles"
    assert "dead market" in buf.getvalue()
    print("  dead market: skipped without orderbook/candle calls")


def test_progress_lines():
    cfg = {"strategy": {"timeframe": "1h", "signal_lookback": 50},
           "network": {"fetch_workers": 8}}

    global LAT
    old_lat = LAT
    LAT = 0.02  # methods read the module global at call time
    try:
        coin = FakeCoin(60)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cb.build_market_cache(cfg, coin, asset_list(coin))
        assert "market data: 25/60 fetched" in buf.getvalue(), buf.getvalue()[-300:]
        assert "50/60 fetched" in buf.getvalue()
        assert "60/60" not in buf.getvalue()  # no pointless 100% line
    finally:
        LAT = old_lat
    print("  progress: 'market data: 25/60 fetched ...' shown on long scans")


class CrashCoin(FakeCoin):
    """FakeCoin whose candles are in a steady decline -> RSI ~ 0 (oversold)."""

    def candles(self, pair, interval="1h", start_ms=None, end_ms=None, limit=200):
        with self._lock:
            self.candle_calls += 1
        time.sleep(LAT)
        t0 = 1_700_000_000_000
        return [{"open": 100.0 - k, "high": 100.0 - k, "low": 100.0 - k,
                 "close": 100.0 - k, "volume": 1.0, "time": t0 + k * 3_600_000}
                for k in range(50)]


def test_run_cycle_smoke():
    """Full signal-check cycle offline: fresh paper state -> BUY on oversold,
    price rally -> SELL via take-profit. Exercises ensure_initialised,
    runtime_assets, build_market_cache, run_cycle and the paper broker."""
    with tempfile.TemporaryDirectory() as td:
        cfg = {"initial_cash_inr": 100000, "fee_rate": 0.001, "slippage_bps": 5,
               "tds_rate": 0.01, "simulate_tds": True,
               "network": {"fetch_workers": 4},
               "assets": [{"name": "A0INR", "pair": "I-A0_INR"},
                          {"name": "A1INR", "pair": "I-A1_INR"}],
               "strategy": {"timeframe": "1h", "signal_lookback": 50,
                            "rsi_period": 14, "entry_rsi": 30, "exit_rsi": 72,
                            "take_profit_pct": 1, "stop_loss_pct": 0,
                            "position_size_pct": 40, "min_buy_inr": 500}}
        coin = CrashCoin(2)
        broker = cb.make_broker(cfg, state_dir=Path(td))
        start_cash = broker.cash

        buf = io.StringIO()
        with redirect_stdout(buf):
            cb.run_cycle(cfg, broker, coin)          # crashing market -> buys
        out1 = buf.getvalue()
        assert "Signal check" in out1 and "Check done." in out1, out1
        assert "BUY A0INR" in out1 and "BUY A1INR" in out1, out1
        assert set(broker.positions) == {"A0INR", "A1INR"}, broker.positions
        assert broker.cash < start_cash * 0.5        # 2 x 40% deployed
        assert broker.file.exists(), "portfolio.json must be persisted"
        trades = broker.read_trades()
        assert [t["side"] for t in trades] == ["buy", "buy"], trades
        cash_after_buys = broker.cash

        # market rallies 50% -> take-profit (avg cost +1%) fires on the next check
        for t in coin._tk.values():
            t["bid"] = t["ask"] = t["last_price"] = "150.0"
        buf = io.StringIO()
        with redirect_stdout(buf):
            cb.run_cycle(cfg, broker, coin)
        out2 = buf.getvalue()
        assert "SELL ALL A0INR (take_profit)" in out2, out2
        assert "SELL ALL A1INR (take_profit)" in out2, out2
        assert broker.positions == {}, "take-profit must flatten positions"
        assert broker.cash > cash_after_buys, "selling at +50% must return cash"

        rows = broker.read_trades()
        assert [r["side"] for r in rows] == ["buy", "buy", "sell", "sell"], rows
        sells = [r for r in rows if r["side"] == "sell"]
        assert all(float(r["tds_inr"]) > 0 for r in sells), "1% TDS must be simulated on sells"
        assert broker.realized_pnl != 0.0
        # broker reloads its own state faithfully
        again = cb.make_broker(cfg, state_dir=Path(td))
        assert again.cash == broker.cash and again.realized_pnl == broker.realized_pnl
    print("  run_cycle smoke: oversold -> 2 BUYs, +50% rally -> take-profit SELLs, "
          "state persisted")


def test_paper_broker_roundtrip():
    """Buy -> partial sell -> full sell with fee/TDS accounting, persistence
    and the rejection paths (min notional, insufficient cash, unknown asset)."""
    with tempfile.TemporaryDirectory() as td:
        b = cb.PaperBroker(state_dir=Path(td), cash=100000.0, fee_rate=0.001,
                           slippage_bps=5, tds_rate=0.01, simulate_tds=True)

        r1 = b.buy("A0INR", 20000.0, ask=100.0, step=1e-6, precision=6,
                   min_notional=100.0)
        assert r1["ok"], r1
        assert r1["notional"] == r1["qty"] * r1["price"]
        assert abs(r1["fee"] - r1["notional"] * 0.001) < 1e-6
        assert r1["notional"] + r1["fee"] <= 20000.0, "fee must fit in the amount"
        assert r1["notional"] + r1["fee"] > 19999.0, "O(1) fee-fit must not over-trim"
        cash_after_buy = 100000.0 - r1["notional"] - r1["fee"]
        assert abs(b.cash - cash_after_buy) < 1e-6
        pos = b.positions["A0INR"]
        assert abs(pos.qty - r1["qty"]) < 1e-9
        assert abs(pos.avg_cost - (r1["notional"] + r1["fee"]) / r1["qty"]) < 1e-9

        # partial sell at a profit
        half = r1["qty"] / 2
        r2 = b.sell("A0INR", half, bid=120.0, step=1e-6, precision=6)
        assert r2["ok"], r2
        assert r2["tds"] == r2["notional"] * 0.01
        cost_half = half * pos.avg_cost
        assert abs(r2["realized_pnl"] - ((r2["notional"] - r2["fee"]) - cost_half)) < 1e-6
        assert "A0INR" in b.positions and b.positions["A0INR"].qty < r1["qty"]

        # full exit
        r3 = b.sell("A0INR", b.positions["A0INR"].qty, bid=110.0,
                    step=1e-6, precision=6)
        assert r3["ok"] and "A0INR" not in b.positions
        tds_total = r2["tds"] + r3["tds"]
        # cash conservation: cash delta == realized pnl minus the TDS skimmed
        assert abs((b.cash - 100000.0) - (b.realized_pnl - tds_total)) < 1e-6
        assert b.realized_pnl > 0, "selling 100@->120/110 must realize a profit"

        # rejections
        bad = b.buy("A1INR", 50.0, ask=100.0, step=1e-6, precision=6,
                    min_notional=100.0)
        assert not bad["ok"] and "min notional" in bad["reason"], bad
        broke = b.buy("A1INR", 10_000_000.0, ask=100.0, step=1e-6, precision=6)
        assert not broke["ok"] and "insufficient cash" in broke["reason"], broke
        ghost = b.sell("NOPE", 1.0, bid=100.0, step=1e-6, precision=6)
        assert not ghost["ok"] and "no such position" in ghost["reason"], ghost

        # persistence round-trip
        b2 = cb.PaperBroker(state_dir=Path(td), cash=0.0, fee_rate=0.001,
                            slippage_bps=5, tds_rate=0.01, simulate_tds=True)
        assert b2.cash == b.cash and b2.realized_pnl == b.realized_pnl
        assert b2.positions == {} and len(b2.read_trades()) == 3
    print("  paper broker: buy/partial-sell/exit accounting exact, "
          "rejections + persistence ok")


if __name__ == "__main__":
    tests = [test_build_market_cache, test_rate_limiter_global_pacing,
             test_fetch_history, test_discovery_caps, test_config_caps_flow,
             test_dipped_scan, test_adaptive_backoff, test_dead_market_skipped,
             test_progress_lines, test_run_cycle_smoke, test_paper_broker_roundtrip]
    for t in tests:
        print(f"* {t.__name__}")
        t()
    print("\nALL TESTS PASSED")
