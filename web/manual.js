/* CrytpTrde — Strategy Lab (manual.html)
 * Self-contained: theme/mobile-nav, live market data from CoinDCX public API
 * (with an offline demo fallback), RSI, and a paper manual-trading sandbox.
 * No backend, no orders — everything runs in the browser + localStorage.
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- constants
  var FEE_RATE = 0.001;        // 0.10% taker fee (mirrors config/fee_rate)
  var SLIPPAGE_BPS = 5;        // simulated slippage (mirrors config/slippage_bps)
  var TDS_RATE = 0.01;         // 1% TDS on sells (mirrors config/tds_rate)
  var START_CASH = 50000;      // starting paper cash (mirrors config/initial_cash_inr)
  var STORE_KEY = "crytptrde_manual_sandbox_v2";

  // Default "dip" strategy used by the signal helper banner.
  var STRAT = { entry_rsi: 30, exit_rsi: 72, tp: 4, sl: 2, rsi_period: 14 };

  var TICKERS_URL = "https://api.coindcx.com/api/v1/market_data/tickers";
  function candlesUrl(pair) {
    return "https://api.coindcx.com/api/v1/charts/candles?pair=" +
      encodeURIComponent(pair) + "&interval=60&limit=200";
  }

  // ------------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }
  function fmtINR(x) {
    if (x == null || isNaN(x)) return "—";
    return "₹" + Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  }
  function fmtQty(x) {
    if (x == null || isNaN(x)) return "—";
    return Number(x).toLocaleString("en-IN", { maximumFractionDigits: 8 });
  }
  function fmtPct(x) {
    if (x == null || isNaN(x)) return "—";
    return (x >= 0 ? "+" : "") + x.toFixed(2) + "%";
  }
  function floor8(x) { return Math.floor(x * 1e8) / 1e8; }
  function nowIso() {
    return new Date().toISOString().slice(0, 19).replace("T", " ");
  }
  function toast(msg, kind) {
    var c = $("toast-container");
    if (!c) return;
    var t = document.createElement("div");
    t.className = "toast" + (kind ? " toast-" + kind : "");
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(function () { t.classList.add("show"); }, 10);
    setTimeout(function () {
      t.classList.remove("show");
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
    }, 2600);
  }

  // -------------------------------------------------------------- sandbox state
  var state = loadState();
  var lastSide = "buy";       // which order button was last pressed (for % chips)
  var market = null;          // { source, pairs:[{name,pair,price,change,closes}] }
  var selected = null;        // currently selected pair name

  function loadState() {
    try {
      var raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        var s = JSON.parse(raw);
        if (s && s.version === 2) return s;
      }
    } catch (e) { /* ignore */ }
    return { version: 2, cash: START_CASH, positions: {}, trades: [] };
  }
  function saveState() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) {}
  }

  // ---------------------------------------------------------------- theme + nav
  function initChrome() {
    var saved = localStorage.getItem("crytptrde_theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
    var btn = $("theme-toggle-btn");
    if (btn) {
      btn.innerHTML = document.documentElement.getAttribute("data-theme") === "dark"
        ? "<span>☀️ Light</span>" : "<span>🌙 Dark</span>";
      btn.addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        localStorage.setItem("crytptrde_theme", next);
        btn.innerHTML = next === "dark" ? "<span>☀️ Light</span>" : "<span>🌙 Dark</span>";
      });
    }
    var menuBtn = $("mobile-menu-btn");
    var drawer = $("mobile-drawer-backdrop");
    var closeBtn = $("mobile-drawer-close");
    if (menuBtn && drawer) menuBtn.addEventListener("click", function () { drawer.classList.add("open"); });
    if (closeBtn && drawer) closeBtn.addEventListener("click", function () { drawer.classList.remove("open"); });
    if (drawer) drawer.addEventListener("click", function (e) { if (e.target === drawer) drawer.classList.remove("open"); });
  }

  // ----------------------------------------------------------------- live data
  function pairName(pair) {
    if (pair.indexOf("I-") === 0) return pair.slice(2).replace("_INR", "") + "INR";
    return pair;
  }

  function fetchJson(url) {
    return fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  }

  function loadTickers() {
    return fetchJson(TICKERS_URL).then(function (rows) {
      if (!Array.isArray(rows)) throw new Error("bad tickers");
      var pairs = [];
      rows.forEach(function (r) {
        var pair = r.pair || "";
        if (pair.indexOf("I-") !== 0 || pair.indexOf("_INR") === -1) return;
        var price = parseFloat(r.last_price);
        if (!isFinite(price) || price <= 0) return;
        pairs.push({
          name: pairName(pair),
          pair: pair,
          price: price,
          change: (r.change != null ? parseFloat(r.change) : null),
          closes: null
        });
      });
      if (!pairs.length) throw new Error("no INR pairs");
      return { source: "live", pairs: pairs };
    });
  }

  function loadCandles(pair) {
    return fetchJson(candlesUrl(pair)).then(function (rows) {
      if (!Array.isArray(rows) || !rows.length) throw new Error("no candles");
      var closes = rows.map(function (c) { return parseFloat(c.close); })
                       .filter(function (v) { return isFinite(v) && v > 0; });
      if (closes.length < STRAT.rsi_period + 1) throw new Error("short candles");
      return closes;
    });
  }

  // ---- offline demo fallback (deterministic-ish random walk) ----------------
  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  var DEMO_DEFS = [
    { name: "BTCINR", pair: "I-BTC_INR", base: 6200000, seed: 11 },
    { name: "ETHINR", pair: "I-ETH_INR", base: 320000, seed: 23 },
    { name: "SOLINR", pair: "I-SOL_INR", base: 16000, seed: 37 },
    { name: "MATICINR", pair: "I-MATIC_INR", base: 72, seed: 41 },
    { name: "DOGEINR", pair: "I-DOGE_INR", base: 11, seed: 53 },
    { name: "XRPINR", pair: "I-XRP_INR", base: 55, seed: 67 },
    { name: "ADAIPNR", pair: "I-ADA_INR", base: 40, seed: 71 },
    { name: "SHIBINR", pair: "I-SHIB_INR", base: 0.0022, seed: 83 }
  ];
  function buildDemo() {
    var pairs = DEMO_DEFS.map(function (d) {
      var rnd = mulberry32(d.seed);
      var n = 200, closes = [], p = d.base;
      for (var i = 0; i < n; i++) {
        var drift = (rnd() - 0.48) * 0.012;          // gentle random walk
        p = Math.max(p * (1 + drift), d.base * 0.2);
        closes.push(p);
      }
      var first = closes[0], last = closes[closes.length - 1];
      return {
        name: d.name, pair: d.pair,
        price: last,
        change: (last / first - 1) * 100,
        closes: closes
      };
    });
    return { source: "demo", pairs: pairs };
  }

  // --------------------------------------------------------------------- RSI
  function rsi(closes, period) {
    period = period || STRAT.rsi_period;
    if (!closes || closes.length <= period) return null;
    var gain = 0, loss = 0;
    for (var i = 1; i <= period; i++) {
      var d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    var ag = gain / period, al = loss / period;
    for (var j = period + 1; j < closes.length; j++) {
      var dd = closes[j] - closes[j - 1];
      var g = Math.max(dd, 0), l = Math.max(-dd, 0);
      ag = (ag * (period - 1) + g) / period;
      al = (al * (period - 1) + l) / period;
    }
    if (al === 0) return 100;
    var rs = ag / al;
    return 100 - 100 / (1 + rs);
  }

  // ------------------------------------------------------------- market render
  function currentPair() {
    if (!market) return null;
    for (var i = 0; i < market.pairs.length; i++) {
      if (market.pairs[i].name === selected) return market.pairs[i];
    }
    return null;
  }

  function populateSelect() {
    var sel = $("asset-select");
    sel.innerHTML = "";
    market.pairs.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.name; o.textContent = p.name;
      sel.appendChild(o);
    });
    if (!selected && market.pairs.length) selected = market.pairs[0].name;
    sel.value = selected;
  }

  function ensureCloses(pair, cb) {
    var p = currentPair();
    if (!p) return;
    if (p.closes) { cb(); return; }
    if (market.source === "live") {
      loadCandles(p.pair).then(function (closes) {
        p.closes = closes; cb();
      }).catch(function () { p.closes = buildDemo().pairs[0].closes; cb(); });
    } else {
      cb();
    }
  }

  function renderMarket() {
    var p = currentPair();
    if (!p) return;
    $("m-price").textContent = fmtINR(p.price);
    var ch = (p.change != null) ? p.change : null;
    var chEl = $("m-change");
    chEl.textContent = (ch == null) ? "—" : fmtPct(ch);
    chEl.style.color = (ch == null) ? "" : (ch >= 0 ? "var(--success)" : "var(--danger)");

    var r = rsi(p.closes);
    $("m-rsi").textContent = (r == null) ? "—" : r.toFixed(1);
    $("live-dot").textContent = (market.source === "live") ? "● live" : "● demo";
    $("live-dot").style.color = (market.source === "live") ? "var(--success)" : "var(--warning)";

    renderSignal(p, r);
    renderChart(p);
    updatePreview();
  }

  function renderSignal(p, r) {
    var banner = $("signal-banner");
    var pos = state.positions[p.name];
    var cls = "signal-hold", txt;
    if (!pos && r != null && r <= STRAT.entry_rsi) {
      cls = "signal-buy";
      txt = "🤖 Dip-bot would BUY — RSI " + r.toFixed(1) + " ≤ " + STRAT.entry_rsi + " (oversold) and no open position.";
    } else if (pos) {
      var tpPx = pos.avg_cost * (1 + STRAT.tp / 100);
      var slPx = pos.avg_cost * (1 - STRAT.sl / 100);
      if (p.price >= tpPx) { cls = "signal-sell"; txt = "🤖 Dip-bot would SELL — take-profit hit (price ≥ avg +" + STRAT.tp + "%)."; }
      else if (p.price <= slPx) { cls = "signal-sell"; txt = "🤖 Dip-bot would SELL — stop-loss hit (price ≤ avg −" + STRAT.sl + "%)."; }
      else if (r != null && r >= STRAT.exit_rsi) { cls = "signal-sell"; txt = "🤖 Dip-bot would SELL — RSI " + r.toFixed(1) + " ≥ " + STRAT.exit_rsi + " (overbought)."; }
      else { txt = "🤖 Dip-bot would HOLD — in profit range, RSI " + (r == null ? "n/a" : r.toFixed(1)) + "."; }
    } else {
      txt = "🤖 Dip-bot would HOLD — RSI " + (r == null ? "n/a" : r.toFixed(1)) + " > " + STRAT.entry_rsi + " (not oversold).";
    }
    banner.className = "signal-banner " + cls;
    banner.textContent = txt;
  }

  // --------------------------------------------------------------------- chart
  var chart = null;
  function renderChart(p) {
    if (typeof Chart === "undefined" || !$("price-chart")) return;
    var labels = (p.closes || []).map(function (_, i) { return i; });
    var priceData = (p.closes || []).map(function (v) { return v; });
    var rsiData = (p.closes || []).map(function (_, i) { return rsi(p.closes.slice(0, i + 1)); });
    var ctx = $("price-chart").getContext("2d");
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "Price (₹)", data: priceData, yAxisID: "y", borderColor: "#6ea8fe",
            backgroundColor: "rgba(110,168,254,.12)", borderWidth: 1.5, pointRadius: 0, tension: 0.15 },
          { label: "RSI(14)", data: rsiData, yAxisID: "y1", borderColor: "#f59e0b",
            borderWidth: 1.2, pointRadius: 0, borderDash: [4, 3], spanGaps: true }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: "#9aa0aa", boxWidth: 12, font: { size: 10 } } } },
        scales: {
          x: { display: false },
          y: { position: "left", ticks: { color: "#9aa0aa", font: { size: 9 } }, grid: { color: "rgba(150,150,160,.08)" } },
          y1: { position: "right", min: 0, max: 100, ticks: { color: "#f59e0b", font: { size: 9 }, stepSize: 50 }, grid: { drawOnChartArea: false } }
        }
      }
    });
  }

  // ------------------------------------------------------------- order preview
  function updatePreview() {
    var p = currentPair();
    var msg = $("order-msg");
    if (!p) { $("o-qty").textContent = "—"; $("o-fee").textContent = "—"; $("o-tds").textContent = "—"; $("o-total").textContent = "—"; return; }
    var amt = parseFloat($("amount-input").value);
    if (!isFinite(amt) || amt <= 0) {
      $("o-qty").textContent = "—"; $("o-fee").textContent = "—"; $("o-tds").textContent = "—"; $("o-total").textContent = "—";
      return;
    }
    var slip = SLIPPAGE_BPS / 10000;
    if (lastSide === "buy") {
      var buyPx = p.price * (1 + slip);
      var qty = floor8(amt / buyPx);
      var notional = qty * buyPx;
      var fee = notional * FEE_RATE;
      $("o-qty").textContent = fmtQty(qty);
      $("o-fee").textContent = fmtINR(fee);
      $("o-tds").textContent = fmtINR(0);
      $("o-total").textContent = fmtINR(notional + fee) + " spent";
    } else {
      var sellPx = p.price * (1 - slip);
      var pos = state.positions[p.name];
      var maxNotional = pos ? pos.qty * sellPx : 0;
      var sellNotional = Math.min(amt, maxNotional);
      var qty2 = floor8(sellNotional / sellPx);
      var notional2 = qty2 * sellPx;
      var fee2 = notional2 * FEE_RATE;
      var tds = notional2 * TDS_RATE;
      $("o-qty").textContent = fmtQty(qty2);
      $("o-fee").textContent = fmtINR(fee2);
      $("o-tds").textContent = fmtINR(tds);
      $("o-total").textContent = fmtINR(notional2 - fee2 - tds) + " received";
      if (!pos) $("order-msg").textContent = "You hold no " + p.name + " to sell.";
    }
  }

  // --------------------------------------------------------------- trade logic
  function doBuy() {
    lastSide = "buy";
    var p = currentPair();
    var msg = $("order-msg");
    msg.textContent = "";
    if (!p) return;
    var amt = parseFloat($("amount-input").value);
    if (!isFinite(amt) || amt <= 0) { toast("Enter an amount in ₹", "warn"); return; }
    var slip = SLIPPAGE_BPS / 10000;
    var buyPx = p.price * (1 + slip);
    var qty = floor8(amt / buyPx);
    if (qty <= 0) { toast("Amount too small", "warn"); return; }
    var notional = qty * buyPx;
    var fee = notional * FEE_RATE;
    var total = notional + fee;
    if (total > state.cash + 1e-9) { toast("Not enough cash", "error"); msg.textContent = "Not enough cash for this buy."; return; }
    state.cash -= total;
    var pos = state.positions[p.name] || { qty: 0, avg_cost: 0 };
    var newQty = pos.qty + qty;
    pos.avg_cost = (pos.avg_cost * pos.qty + notional + fee) / newQty;
    pos.qty = newQty;
    state.positions[p.name] = pos;
    state.trades.unshift({ ts: nowIso(), asset: p.name, side: "BUY", price: buyPx, qty: qty, notional: notional, fee: fee, tds: 0, realized: null });
    saveState(); renderAll();
    toast("Bought " + fmtQty(qty) + " " + p.name, "ok");
  }

  function doSell() {
    lastSide = "sell";
    var p = currentPair();
    var msg = $("order-msg");
    msg.textContent = "";
    if (!p) return;
    var pos = state.positions[p.name];
    if (!pos || pos.qty <= 0) { toast("No " + p.name + " position to sell", "warn"); msg.textContent = "You hold no " + p.name + " to sell."; return; }
    var amt = parseFloat($("amount-input").value);
    if (!isFinite(amt) || amt <= 0) { toast("Enter an amount in ₹", "warn"); return; }
    var slip = SLIPPAGE_BPS / 10000;
    var sellPx = p.price * (1 - slip);
    var maxNotional = pos.qty * sellPx;
    var sellNotional = Math.min(amt, maxNotional);
    var qty = floor8(sellNotional / sellPx);
    if (qty <= 0) { toast("Amount too small", "warn"); return; }
    var notional = qty * sellPx;
    var fee = notional * FEE_RATE;
    var tds = notional * TDS_RATE;
    var proceeds = notional - fee - tds;
    var realized = proceeds - qty * pos.avg_cost;
    state.cash += proceeds;
    pos.qty -= qty;
    if (pos.qty <= 1e-10) delete state.positions[p.name];
    state.trades.unshift({ ts: nowIso(), asset: p.name, side: "SELL", price: sellPx, qty: qty, notional: notional, fee: fee, tds: tds, realized: realized });
    saveState(); renderAll();
    toast("Sold " + fmtQty(qty) + " " + p.name + " (P&L " + fmtINR(realized) + ")", realized >= 0 ? "ok" : "error");
  }

  function sellAll(name) {
    lastSide = "sell";
    var p = currentPair();
    if (p && p.name === name) {
      var holdVal = (state.positions[name] ? state.positions[name].qty * p.price : 0);
      $("amount-input").value = Math.max(0, Math.floor(holdVal * 100) / 100).toFixed(2);
      updatePreview(); doSell();
    }
  }

  function resetSandbox() {
    if (!confirm("Reset the sandbox? This clears your paper cash, positions and trade log (browser only).")) return;
    state = { version: 2, cash: START_CASH, positions: {}, trades: [] };
    saveState(); renderAll();
    toast("Sandbox reset to ₹" + START_CASH.toLocaleString("en-IN"), "ok");
  }

  // --------------------------------------------------------------- portfolio UI
  function renderMetrics(p) {
    var grid = $("metrics-cards-grid");
    var holdingsValue = 0;
    Object.keys(state.positions).forEach(function (k) {
      var pos = state.positions[k];
      var px = (p && p.name === k) ? p.price : null;
      if (px == null && market) {
        for (var i = 0; i < market.pairs.length; i++) if (market.pairs[i].name === k) { px = market.pairs[i].price; break; }
      }
      if (px != null) holdingsValue += pos.qty * px;
    });
    var total = state.cash + holdingsValue;
    var realized = 0, fees = 0, tds = 0;
    state.trades.forEach(function (t) {
      fees += t.fee || 0; tds += t.tds || 0;
      if (t.side === "SELL" && t.realized != null) realized += t.realized;
    });
    var cards = [
      { h: "Cash", v: fmtINR(state.cash), icon: "💰", cls: "highlight-info" },
      { h: "Holdings", v: fmtINR(holdingsValue), icon: "📦", cls: "highlight-purple" },
      { h: "Total", v: fmtINR(total), icon: "🧮", cls: "highlight-success" },
      { h: "Realized P&L", v: fmtINR(realized), icon: realized >= 0 ? "📈" : "📉", cls: realized >= 0 ? "highlight-success" : "highlight-danger" },
      { h: "Fees paid", v: fmtINR(fees), icon: "💸", cls: "highlight-warning" },
      { h: "TDS withheld", v: fmtINR(tds), icon: "🧾", cls: "highlight-warning" }
    ];
    grid.innerHTML = cards.map(function (c) {
      return '<div class="metric-card ' + c.cls + '">' +
        '<div class="metric-header"><span>' + c.h + '</span><span>' + c.icon + '</span></div>' +
        '<div class="metric-value-row"><span class="metric-value">' + c.v + '</span></div></div>';
    }).join("");
  }

  function renderPositions(p) {
    var wrap = $("positions-wrap");
    var names = Object.keys(state.positions);
    if (!names.length) { wrap.innerHTML = '<p class="hint">No open positions yet. Place a BUY to start.</p>'; return; }
    var rows = names.map(function (k) {
      var pos = state.positions[k];
      var px = (p && p.name === k) ? p.price : null;
      if (px == null && market) for (var i = 0; i < market.pairs.length; i++) if (market.pairs[i].name === k) { px = market.pairs[i].price; break; }
      if (px == null) px = pos.avg_cost;
      var value = pos.qty * px;
      var pnl = value - pos.qty * pos.avg_cost;
      var pnlPct = (value / (pos.qty * pos.avg_cost) - 1) * 100;
      var up = pnl >= 0;
      return "<tr><td>" + k + "</td>" +
        "<td>" + fmtQty(pos.qty) + "</td>" +
        "<td>" + fmtINR(pos.avg_cost) + "</td>" +
        "<td>" + fmtINR(px) + "</td>" +
        "<td>" + fmtINR(value) + "</td>" +
        '<td><span class="pill ' + (up ? "up" : "down") + '">' + fmtINR(pnl) + "</span></td>" +
        '<td><span class="pill ' + (up ? "up" : "down") + '">' + fmtPct(pnlPct) + "</span></td>" +
        '<td><button class="btn btn-sm btn-danger" data-sellall="' + k + '">Sell all</button></td></tr>';
    }).join("");
    wrap.innerHTML = '<table class="pos-table"><thead><tr>' +
      "<th>Asset</th><th>Qty</th><th>Avg cost</th><th>Price</th><th>Value</th><th>P&L</th><th>P&L%</th><th></th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>";
  }

  function renderLog() {
    var wrap = $("log-wrap");
    if (!state.trades.length) { wrap.innerHTML = '<p class="hint">No trades yet.</p>'; return; }
    var rows = state.trades.slice(0, 200).map(function (t) {
      var sideCls = t.side === "BUY" ? "up" : "down";
      var realized = (t.side === "SELL" && t.realized != null)
        ? '<span class="pill ' + (t.realized >= 0 ? "up" : "down") + '">' + fmtINR(t.realized) + "</span>" : "—";
      return "<tr><td>" + t.ts + "</td><td><span class=\"pill " + sideCls + "\">" + t.side + "</span></td>" +
        "<td>" + t.asset + "</td><td>" + fmtINR(t.price) + "</td><td>" + fmtQty(t.qty) + "</td>" +
        "<td>" + fmtINR(t.notional) + "</td><td>" + fmtINR(t.fee) + "</td><td>" + fmtINR(t.tds) + "</td>" +
        "<td>" + realized + "</td></tr>";
    }).join("");
    wrap.innerHTML = '<table class="pos-table"><thead><tr>' +
      "<th>Time</th><th>Side</th><th>Asset</th><th>Price</th><th>Qty</th><th>Notional</th><th>Fee</th><th>TDS</th><th>Realized</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>";
  }

  function renderAll() {
    var p = currentPair();
    renderMetrics(p);
    renderPositions(p);
    renderLog();
    renderMarket();
  }

  // ------------------------------------------------------------------- wiring
  function wire() {
    $("asset-select").addEventListener("change", function (e) {
      selected = e.target.value;
      ensureCloses(selected, renderMarket);
    });
    $("btn-buy").addEventListener("click", doBuy);
    $("btn-sell").addEventListener("click", doSell);
    $("btn-reset-sandbox").addEventListener("click", resetSandbox);
    $("btn-refresh").addEventListener("click", function () { loadMarket(true); });
    $("amount-input").addEventListener("input", updatePreview);
    Array.prototype.forEach.call(document.querySelectorAll(".pct-chip"), function (b) {
      b.addEventListener("click", function () {
        var pct = parseFloat(b.getAttribute("data-pct"));
        var p = currentPair();
        var base = (lastSide === "buy")
          ? state.cash
          : (p && state.positions[p.name] ? state.positions[p.name].qty * p.price : 0);
        var amt = Math.floor(base * pct * 100) / 100;
        $("amount-input").value = amt > 0 ? amt.toFixed(2) : "";
        updatePreview();
      });
    });
    // Delegate "Sell all" buttons inside the positions table.
    $("positions-wrap").addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.getAttribute && t.getAttribute("data-sellall")) sellAll(t.getAttribute("data-sellall"));
    });
  }

  // ----------------------------------------------------------------- load flow
  function loadMarket(force) {
    loadTickers().then(function (m) {
      market = m; finishLoad();
    }).catch(function () {
      market = buildDemo();
      toast("Live prices unavailable — using demo data", "warn");
      finishLoad();
    });
  }
  function finishLoad() {
    if (!selected) selected = market.pairs[0].name;
    populateSelect();
    ensureCloses(selected, renderAll);
  }

  // --------------------------------------------------------------------- boot
  function boot() {
    initChrome();
    wire();
    if (state.trades.length || Object.keys(state.positions).length) renderAll();
    loadMarket(false);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
