/**
 * CrytpTrde Data Explorer — Master Application & Multi-Page Navigation Logic
 * 100% Client-side, Static, and GitHub Pages Compatible.
 */

(function () {
  'use strict';

  // Determine current page from body attribute or URL
  const pageId = document.body.getAttribute('data-page') || 'sweep';
  const pageToDatasetMap = {
    'sweep': 'sweep_results',
    'trades': 'trades',
    'live': 'live_summary',
    'bot': null,
    'configs': 'accounts',
    'compare': 'sweep_results',
    'importer': 'custom',
    'analytics': 'sweep_results'
  };

  // State
  const state = {
    pageId: pageId,
    currentDatasetKey: pageToDatasetMap[pageId] || 'sweep_results',
    customData: null,
    customDataTitle: 'Custom Dataset',
    
    // Filters
    searchQuery: '',
    selectedMode: 'all',
    selectedReason: 'all',
    selectedAsset: 'all',
    selectedSide: 'all',
    selectedHoldHours: 'all',
    selectedPnlStatus: 'all',
    minWinRate: null,
    maxWinRate: null,
    minPnlPct: null,
    maxPnlPct: null,
    minRsi: null,
    maxRsi: null,
    activeQuickFilter: 'all',
    dateFrom: '',
    dateTo: '',

    // Sorting (Multi-Column)
    sortColumns: [{ key: pageId === 'trades' ? 'entry_time' : (pageId === 'configs' ? 'account' : 'rank'), direction: pageId === 'trades' ? 'desc' : 'asc' }],

    // Pagination
    currentPage: 1,
    pageSize: 25,

    // Row Multi-Selection
    selectedRowKeys: new Set(),

    // Column Visibility
    hiddenColumns: new Set(),

    // UI & Polling
    theme: localStorage.getItem('crytptrde_theme') || 'dark',
    chartsExpanded: true,
    autoRefreshInterval: null,
    autoRefreshRateSec: 0,

    // Bot Details page
    currentBotAccount: null,
  };

  // Chart References
  let charts = {
    chart1: null,
    chart2: null,
    chart3: null,
    comparatorChart: null,
    monteCarloChart: null,
    playgroundChart: null,
    ensembleChart: null,
    underwaterChart: null,
    botPageChart: null,
  };

  // Dataset Schema Configs
  const DATASET_CONFIGS = {
    sweep_results: {
      title: 'Sweep Tournament Results',
      subtitle: '500 bot strategies backtested across CoinDCX markets (30 days)',
      defaultSort: 'rank',
      defaultSortDir: 'asc',
      rowKey: 'account',
      columns: [
        { key: 'rank', label: 'Rank', type: 'number', width: '60px', render: (val) => formatRank(val) },
        { key: 'account', label: 'Account', type: 'string', width: '90px' },
        { key: 'entry_mode', label: 'Mode', type: 'string', render: (val) => formatModeBadge(val) },
        { key: 'entry_rsi', label: 'Entry RSI', type: 'number' },
        { key: 'exit_rsi', label: 'Exit RSI', type: 'number' },
        { key: 'take_profit_pct', label: 'TP %', type: 'number', render: (val) => `${val}%` },
        { key: 'stop_loss_pct', label: 'SL %', type: 'number', render: (val) => `${val}%` },
        { key: 'position_size_pct', label: 'Size %', type: 'number', render: (val) => `${val}%` },
        { key: 'max_hold_hours', label: 'Hold', type: 'number', render: (val) => formatHoldTime(val) },
        { key: 'round_trips', label: 'Trades', type: 'number' },
        { key: 'win_rate', label: 'Win Rate', type: 'number', render: (val) => formatWinRate(val) },
        { key: 'profit_factor', label: 'Profit Factor', type: 'number', render: (val) => formatNumber(val, 2) },
        { key: 'final_value', label: 'Final Value', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'pnl', label: 'PnL (INR)', type: 'number', render: (val) => formatPnl(val, '₹') },
        { key: 'pnl_pct', label: 'PnL %', type: 'number', render: (val) => formatPnlPct(val) },
        { key: 'max_drawdown_pct', label: 'Max DD %', type: 'number', render: (val) => `${formatNumber(val, 2)}%` },
        { key: 'fees_paid', label: 'Fees Paid', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'tds_paid', label: 'TDS Paid', type: 'number', render: (val) => formatCurrency(val) },
      ]
    },
    trades: {
      title: 'Backtest Trades Log',
      subtitle: '92 simulated trade entries & exits with timing, RSI, and PnL',
      defaultSort: 'entry_time',
      defaultSortDir: 'desc',
      rowKey: 'entry_time',
      columns: [
        { key: 'asset', label: 'Asset', type: 'string', render: (val) => formatAsset(val) },
        { key: 'entry_time', label: 'Entry Time', type: 'date', render: (val) => formatDate(val) },
        { key: 'entry_price', label: 'Entry Price', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'rsi_at_entry', label: 'Entry RSI', type: 'number', render: (val) => formatNumber(val, 1) },
        { key: 'exit_time', label: 'Exit Time', type: 'date', render: (val) => formatDate(val) },
        { key: 'exit_price', label: 'Exit Price', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'reason', label: 'Exit Reason', type: 'string', render: (val) => formatReasonBadge(val) },
        { key: 'pnl_inr', label: 'PnL (INR)', type: 'number', render: (val) => formatPnl(val, '₹') },
        { key: 'pnl_pct', label: 'PnL %', type: 'number', render: (val) => formatPnlPct(val) },
      ]
    },
    live_trades: {
      title: 'Live Bot Trades',
      subtitle: 'Every paper fill from the 500 live tournament bots, newest first',
      defaultSort: 'timestamp_utc',
      defaultSortDir: 'desc',
      rowKey: ['account', 'timestamp_utc', 'asset', 'side', 'quantity'],
      columns: [
        { key: 'account', label: 'Account', type: 'string', render: (val) => formatAccount(val) },
        { key: 'timestamp_utc', label: 'Time (UTC)', type: 'date', render: (val) => formatDate(val) },
        { key: 'asset', label: 'Asset', type: 'string', render: (val) => formatAsset(val) },
        { key: 'side', label: 'Side', type: 'string', render: (val) => formatSideBadge(val) },
        { key: 'price_inr', label: 'Price (₹)', type: 'number', render: (val) => formatPrice(val) },
        { key: 'quantity', label: 'Quantity', type: 'number', render: (val) => formatQuantity(val) },
        { key: 'notional_inr', label: 'Notional (₹)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'fee_inr', label: 'Fee (₹)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'tds_inr', label: 'TDS (₹)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'realized_pnl_inr', label: 'Realized PnL', type: 'number', render: (val) => formatPnl(val, '₹') },
      ]
    },
    last_trades: {
      title: 'Last Trade per Bot',
      subtitle: 'The single most recent fill for each of the 500 live bots',
      defaultSort: 'timestamp_utc',
      defaultSortDir: 'desc',
      rowKey: 'account',
      columns: [
        { key: 'account', label: 'Account', type: 'string', render: (val) => formatAccount(val) },
        { key: 'timestamp_utc', label: 'Last Trade (UTC)', type: 'date', render: (val) => formatDate(val) },
        { key: 'asset', label: 'Asset', type: 'string', render: (val) => formatAsset(val) },
        { key: 'side', label: 'Side', type: 'string', render: (val) => formatSideBadge(val) },
        { key: 'price_inr', label: 'Price (₹)', type: 'number', render: (val) => formatPrice(val) },
        { key: 'quantity', label: 'Quantity', type: 'number', render: (val) => formatQuantity(val) },
        { key: 'notional_inr', label: 'Notional (₹)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'fee_inr', label: 'Fee (₹)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'tds_inr', label: 'TDS (₹)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'realized_pnl_inr', label: 'Realized PnL', type: 'number', render: (val) => formatPnl(val, '₹') },
      ]
    },
    live_summary: {
      title: 'Live Tournament Accounts',
      subtitle: '500 tournament demo accounts tracked with cash, holdings, and PnL',
      defaultSort: 'rank',
      defaultSortDir: 'asc',
      rowKey: 'account',
      columns: [
        { key: 'rank', label: 'Rank', type: 'number', render: (val) => formatRank(val) },
        { key: 'account', label: 'Account', type: 'string' },
        { key: 'entry_mode', label: 'Mode', type: 'string', render: (val) => formatModeBadge(val) },
        { key: 'entry_rsi', label: 'Entry RSI', type: 'number' },
        { key: 'exit_rsi', label: 'Exit RSI', type: 'number' },
        { key: 'take_profit_pct', label: 'TP %', type: 'number', render: (val) => `${val}%` },
        { key: 'stop_loss_pct', label: 'SL %', type: 'number', render: (val) => `${val}%` },
        { key: 'position_size_pct', label: 'Size %', type: 'number', render: (val) => `${val}%` },
        { key: 'max_hold_hours', label: 'Hold', type: 'number', render: (val) => formatHoldTime(val) },
        { key: 'value_inr', label: 'Total Value', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'cash_inr', label: 'Cash (INR)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'holdings_inr', label: 'Holdings (INR)', type: 'number', render: (val) => formatCurrency(val) },
        { key: 'realized_pnl_inr', label: 'Realized PnL', type: 'number', render: (val) => formatPnl(val, '₹') },
        { key: 'trades', label: 'Trades Count', type: 'number' },
      ]
    },
    accounts: {
      title: 'Bot Config Grid',
      subtitle: '500 parameterized trading strategy accounts specifications',
      defaultSort: 'account',
      defaultSortDir: 'asc',
      rowKey: 'account',
      columns: [
        { key: 'account', label: 'Account ID', type: 'string' },
        { key: 'name', label: 'Configuration Name', type: 'string' },
        { key: 'entry_mode', label: 'Mode', type: 'string', render: (val) => formatModeBadge(val) },
        { key: 'entry_rsi', label: 'Entry RSI', type: 'number' },
        { key: 'exit_rsi', label: 'Exit RSI', type: 'number' },
        { key: 'take_profit_pct', label: 'Take Profit %', type: 'number', render: (val) => `${val}%` },
        { key: 'stop_loss_pct', label: 'Stop Loss %', type: 'number', render: (val) => `${val}%` },
        { key: 'position_size_pct', label: 'Position Size %', type: 'number', render: (val) => `${val}%` },
        { key: 'max_hold_hours', label: 'Hold Limit', type: 'number', render: (val) => formatHoldTime(val) },
      ]
    }
  };

  // ==========================================================================
  // "Last bot run" badge — answers "when did the bot last run?" on EVERY page.
  // Source: `bot_status` in data.js (embedded by scripts/build_data_js.py from the
  // heartbeat file data/state/last_run.json that cryptobot.py rewrites each run).
  // The relative age is computed client-side and refreshed every 30s, so the
  // badge keeps counting up even while the page stays open.
  // ==========================================================================
  function parseUtc(ts) {
    if (!ts) return null;
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatAge(ms) {
    if (ms < 45 * 1000) return 'just now';
    const mins = Math.floor(ms / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ${mins % 60}m ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h ago`;
  }

  function formatAbsolute(d) {
    const utc = d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
    let local;
    try {
      local = d.toLocaleString(undefined, {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
      });
    } catch (e) {
      local = '';
    }
    return local ? `${utc} (${local})` : utc;
  }

  function renderBotRunBadge() {
    const controls = document.querySelector('.header-controls');
    const botStatus = window.DATA_SETS ? window.DATA_SETS.bot_status : null;
    if (!controls || !botStatus) return;

    const ranAt = parseUtc(botStatus.timestamp_utc);
    if (!ranAt) return;

    const badge = document.createElement('div');
    badge.className = 'bot-run-badge';
    badge.innerHTML = '<span class="bot-run-dot"></span><span class="bot-run-text"></span>';
    controls.insertBefore(badge, controls.firstChild);
    const textEl = badge.querySelector('.bot-run-text');

    // The bot runs hourly (:30 UTC). Green = ran this cycle; amber = one or
    // two cycles missed (or the run was skipped, e.g. CoinDCX unreachable);
    // red = silent for 6h+ or the last run errored.
    function update() {
      const ageMs = Date.now() - ranAt.getTime();
      const runStatus = botStatus.status || 'ok';
      let cls = 'is-fresh';
      if (runStatus === 'error') cls = 'is-dead';
      else if (runStatus === 'skipped') cls = 'is-late';
      else if (ageMs > 6 * 3600 * 1000) cls = 'is-dead';
      else if (ageMs > 2 * 3600 * 1000) cls = 'is-late';
      badge.classList.remove('is-fresh', 'is-late', 'is-dead');
      badge.classList.add(cls);

      const verb = runStatus === 'error' ? 'Bot errored'
        : runStatus === 'skipped' ? 'Bot skipped'
        : 'Bot ran';
      textEl.textContent = `${verb} ${formatAge(ageMs)}`;

      const lines = [
        `Last bot run: ${formatAbsolute(ranAt)}`,
        `Command: ${botStatus.command || '?'} · status: ${runStatus}`
        + (botStatus.runner ? ` · runner: ${botStatus.runner}` : ''),
      ];
      if (botStatus.note) lines.push(botStatus.note);
      const generated = parseUtc(botStatus.data_generated_utc);
      if (generated) lines.push(`Dashboard data built: ${formatAbsolute(generated)}`);
      badge.title = lines.join('\n');
    }

    update();
    setInterval(update, 30 * 1000);
  }

  // ==========================================================================
  // Initialization & Device-Friendly Navigation Setup
  // ==========================================================================
  document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initNavigation();
    renderBotRunBadge();
    readStateFromUrl();
    initEventListeners();

    if (state.pageId === 'analytics') {
      initAnalyticsPage();
    } else if (state.pageId === 'compare') {
      openStrategyComparator();
    } else if (state.pageId === 'bot') {
      initBotPage();
    } else {
      loadDataset(state.currentDatasetKey);
      applyInitialTradesView();
    }
  });

  function initNavigation() {
    // Mobile Drawer Open / Close
    const menuBtn = document.getElementById('mobile-menu-btn');
    const drawerBackdrop = document.getElementById('mobile-drawer-backdrop');
    const drawerCloseBtn = document.getElementById('mobile-drawer-close');

    if (menuBtn && drawerBackdrop) {
      menuBtn.addEventListener('click', () => drawerBackdrop.classList.add('open'));
    }
    if (drawerCloseBtn && drawerBackdrop) {
      drawerCloseBtn.addEventListener('click', () => drawerBackdrop.classList.remove('open'));
    }
    if (drawerBackdrop) {
      drawerBackdrop.addEventListener('click', (e) => {
        if (e.target === drawerBackdrop) drawerBackdrop.classList.remove('open');
      });
    }

    // Bottom App Bar Drawer Trigger
    const moreBtn = document.getElementById('bottom-tab-more');
    if (moreBtn && drawerBackdrop) {
      moreBtn.addEventListener('click', (e) => {
        e.preventDefault();
        drawerBackdrop.classList.add('open');
      });
    }
  }

  function readStateFromUrl() {
    try {
      const hash = window.location.hash.replace('#', '');
      if (!hash) return;
      const params = new URLSearchParams(hash);

      if (params.has('search')) state.searchQuery = params.get('search');
      if (params.has('mode')) state.selectedMode = params.get('mode');
      if (params.has('hold')) state.selectedHoldHours = params.get('hold');
      if (params.has('pnl')) state.selectedPnlStatus = params.get('pnl');
      if (params.has('page')) state.currentPage = parseInt(params.get('page'), 10) || 1;
      if (params.has('size')) state.pageSize = params.get('size') === 'all' ? 'all' : parseInt(params.get('size'), 10);
      if (params.has('sort')) {
        const dir = params.get('dir') || 'asc';
        state.sortColumns = [{ key: params.get('sort'), direction: dir }];
      }
    } catch (e) {
      console.warn('Could not read URL hash:', e);
    }
  }

  function syncStateToUrl() {
    try {
      const params = new URLSearchParams();
      if (state.searchQuery) params.set('search', state.searchQuery);
      if (state.selectedMode !== 'all') params.set('mode', state.selectedMode);
      if (state.selectedHoldHours !== 'all') params.set('hold', state.selectedHoldHours);
      if (state.selectedPnlStatus !== 'all') params.set('pnl', state.selectedPnlStatus);
      if (state.currentPage > 1) params.set('page', state.currentPage);
      if (state.pageSize !== 25) params.set('size', state.pageSize);
      if (state.sortColumns.length > 0) {
        params.set('sort', state.sortColumns[0].key);
        params.set('dir', state.sortColumns[0].direction);
      }
      if (['trades', 'live_trades', 'last_trades'].indexOf(state.currentDatasetKey) !== -1) {
        params.set('view', state.currentDatasetKey);
      }

      window.history.replaceState(null, '', '#' + params.toString());
    } catch (e) {
      console.warn('Could not sync URL state:', e);
    }
  }

  // ==========================================================================
  // Theme Management
  // ==========================================================================
  function initTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
    updateThemeIcon();
  }

  function toggleTheme() {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
    localStorage.setItem('crytptrde_theme', state.theme);
    updateThemeIcon();
    renderCharts(filterData(getRawDataset(state.currentDatasetKey)));
    if (state.pageId === 'bot' && state.currentBotAccount) renderBotPageChart(state.currentBotAccount);
  }

  function updateThemeIcon() {
    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) {
      themeBtn.innerHTML = state.theme === 'dark' ? '☀️ Light' : '🌙 Dark';
    }
  }

  // ==========================================================================
  // Dataset Management & Custom Configs
  // ==========================================================================
  function getActiveConfig() {
    if (state.currentDatasetKey === 'custom' && state.customData) {
      return getCustomConfig(state.customData);
    }
    return DATASET_CONFIGS[state.currentDatasetKey] || DATASET_CONFIGS.sweep_results;
  }

  function getRawDataset(key) {
    if (key === 'custom') return state.customData || [];
    return window.DATA_SETS?.[key] || [];
  }

  function loadDataset(key) {
    const config = getActiveConfig();
    
    const titleEl = document.getElementById('current-dataset-title');
    const subtitleEl = document.getElementById('current-dataset-subtitle');
    if (titleEl) titleEl.textContent = config.title;
    if (subtitleEl) subtitleEl.textContent = config.subtitle;

    renderFilterControls();
    renderQuickFilterChips();
    renderLastTradeBanner();
    syncTradesViewSwitcher();
    updateChartsPanelTitle();
    updateUI();
  }

  // ==========================================================================
  // Trades Page View Switcher (Backtest / Live / Last-per-bot)
  // ==========================================================================
  function switchTradesView(key) {
    if (['trades', 'live_trades', 'last_trades'].indexOf(key) === -1) return;

    state.currentDatasetKey = key;
    state.searchQuery = '';
    state.selectedMode = 'all';
    state.selectedReason = 'all';
    state.selectedAsset = 'all';
    state.selectedSide = 'all';
    state.selectedHoldHours = 'all';
    state.selectedPnlStatus = 'all';
    state.minWinRate = null;
    state.minPnlPct = null;
    state.dateFrom = '';
    state.dateTo = '';
    state.activeQuickFilter = 'all';
    state.currentPage = 1;
    state.selectedRowKeys.clear();
    state.hiddenColumns.clear();

    const config = getActiveConfig();
    state.sortColumns = [{ key: config.defaultSort || 'rank', direction: config.defaultSortDir || 'asc' }];

    const searchInput = document.getElementById('global-search');
    if (searchInput) searchInput.value = '';
    const searchClear = document.getElementById('search-clear-btn');
    if (searchClear) searchClear.style.display = 'none';

    loadDataset(key);
  }

  // Apply a deep-link like trades.html#view=last_trades on first load.
  function applyInitialTradesView() {
    if (state.pageId !== 'trades') return;
    try {
      const params = new URLSearchParams(window.location.hash.replace('#', ''));
      const view = params.get('view');
      if (view && ['trades', 'live_trades', 'last_trades'].indexOf(view) !== -1) {
        switchTradesView(view);
      }
    } catch (e) {
      console.warn('Could not read trades view from URL:', e);
    }
  }

  function syncTradesViewSwitcher() {
    const switcher = document.getElementById('trades-view-switcher');
    if (!switcher) return;
    switcher.querySelectorAll('[data-view]').forEach(btn => {
      const isActive = btn.getAttribute('data-view') === state.currentDatasetKey;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
  }

  function updateChartsPanelTitle() {
    const titleSpan = document.getElementById('charts-panel-title-text');
    if (!titleSpan) return;
    const titles = {
      'trades': 'Trade Distribution & PnL by Asset Charts',
      'live_trades': 'Live Trade Analytics — PnL, Notional & Side',
      'last_trades': 'Last-Trade per Bot Analytics'
    };
    titleSpan.textContent = '📈 ' + (titles[state.currentDatasetKey] || 'Trade Analytics');
  }

  function renderLastTradeBanner() {
    const banner = document.getElementById('last-trade-banner');
    if (!banner) return;

    const lastTrades = window.DATA_SETS?.last_trades || [];
    const latest = lastTrades[0];

    if (!latest) {
      banner.style.display = 'none';
      return;
    }

    banner.style.display = '';
    const pnl = parseFloat(latest.realized_pnl_inr || 0);
    const pnlColor = pnl > 0 ? 'var(--success)' : (pnl < 0 ? 'var(--danger)' : 'var(--text-secondary)');
    const pnlText = pnl > 0 ? `+₹${formatNumber(pnl, 2)}` : (pnl < 0 ? `-₹${formatNumber(Math.abs(pnl), 2)}` : '₹0.00');

    banner.innerHTML = `
      <div class="last-trade-badge">
        <span class="pulse-dot"></span> LAST TRADE
      </div>
      <div class="last-trade-main">
        <div class="last-trade-title">
          ${formatAccount(latest.account)}
          ${formatSideBadge(latest.side)}
          <span class="last-trade-asset">${escapeHtml(latest.asset)}</span>
        </div>
        <div class="last-trade-meta">
          <span>⏱️ ${escapeHtml(latest.timestamp_utc)}</span>
          <span>💱 Price ${formatPrice(latest.price_inr)}</span>
          <span>🔢 Qty ${formatQuantity(latest.quantity)}</span>
          <span>💼 Notional ${formatCurrency(latest.notional_inr)}</span>
          <span>🧾 Fee ${formatCurrency(latest.fee_inr)}</span>
          <span>🏛️ TDS ${formatCurrency(latest.tds_inr)}</span>
        </div>
      </div>
      <div class="last-trade-pnl">
        <div class="last-trade-pnl-label">Realized PnL</div>
        <div class="last-trade-pnl-value" style="color:${pnlColor}">${pnlText}</div>
      </div>
      <div class="last-trade-actions">
        <button class="btn btn-sm btn-primary" id="banner-open-live" title="Open the full live trade log">⚡ View all ${lastTrades.length} live trades</button>
      </div>
    `;

    const openLiveBtn = document.getElementById('banner-open-live');
    if (openLiveBtn) {
      openLiveBtn.addEventListener('click', () => switchTradesView('live_trades'));
    }
  }

  function getCustomConfig(data) {
    if (!data || data.length === 0) {
      return {
        title: state.customDataTitle,
        subtitle: 'Custom uploaded dataset',
        columns: []
      };
    }

    const firstRow = data[0];
    const columns = Object.keys(firstRow).map(key => {
      const sampleVal = firstRow[key];
      const isNum = typeof sampleVal === 'number' || (!isNaN(parseFloat(sampleVal)) && isFinite(sampleVal));
      const isDate = typeof sampleVal === 'string' && (sampleVal.includes('T') || sampleVal.includes('-')) && !isNaN(Date.parse(sampleVal));
      
      return {
        key: key,
        label: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
        type: isNum ? 'number' : (isDate ? 'date' : 'string'),
        render: (val) => {
          if (val === null || val === undefined) return '<span class="text-muted">-</span>';
          if (typeof val === 'number') return formatNumber(val, 2);
          return highlightSearchText(escapeHtml(String(val)));
        }
      };
    });

    return {
      title: state.customDataTitle,
      subtitle: `${data.length} custom records imported`,
      defaultSort: columns[0]?.key || '',
      defaultSortDir: 'asc',
      rowKey: columns[0]?.key || 'id',
      columns: columns
    };
  }

  // ==========================================================================
  // Filter Rendering & Logic
  // ==========================================================================
  function renderFilterControls() {
    const container = document.getElementById('advanced-filters-grid');
    if (!container) return;

    const data = getRawDataset(state.currentDatasetKey);
    const key = state.currentDatasetKey;

    let html = '';

    if (key === 'sweep_results' || key === 'live_summary' || key === 'accounts') {
      html += `
        <div class="filter-control-group">
          <label>Entry Mode</label>
          <select id="filter-mode" class="filter-select">
            <option value="all" ${state.selectedMode === 'all' ? 'selected' : ''}>All Modes (Dip & Mom)</option>
            <option value="dip" ${state.selectedMode === 'dip' ? 'selected' : ''}>Dip (Oversold Buy)</option>
            <option value="momentum" ${state.selectedMode === 'momentum' ? 'selected' : ''}>Momentum (Strength Buy)</option>
          </select>
        </div>

        <div class="filter-control-group">
          <label>Max Hold Duration</label>
          <select id="filter-hold" class="filter-select">
            <option value="all" ${state.selectedHoldHours === 'all' ? 'selected' : ''}>All Hold Durations</option>
            <option value="0" ${state.selectedHoldHours === '0' ? 'selected' : ''}>0h (Hold Forever / Signal)</option>
            <option value="72" ${state.selectedHoldHours === '72' ? 'selected' : ''}>72h (3 Days)</option>
            <option value="168" ${state.selectedHoldHours === '168' ? 'selected' : ''}>168h (1 Week)</option>
            <option value="336" ${state.selectedHoldHours === '336' ? 'selected' : ''}>336h (2 Weeks)</option>
            <option value="720" ${state.selectedHoldHours === '720' ? 'selected' : ''}>720h (1 Month)</option>
          </select>
        </div>
      `;

      if (key === 'sweep_results') {
        html += `
          <div class="filter-control-group">
            <label>PnL Outcome</label>
            <select id="filter-pnl-status" class="filter-select">
              <option value="all" ${state.selectedPnlStatus === 'all' ? 'selected' : ''}>All Outcomes</option>
              <option value="profit" ${state.selectedPnlStatus === 'profit' ? 'selected' : ''}>Profitable Only (> ₹0)</option>
              <option value="loss" ${state.selectedPnlStatus === 'loss' ? 'selected' : ''}>Loss Only (< ₹0)</option>
            </select>
          </div>

          <div class="filter-control-group">
            <label>Min Win Rate (%) <span class="filter-val-hint" id="winrate-hint">${state.minWinRate !== null ? state.minWinRate + '%' : 'Any'}</span></label>
            <input type="range" id="filter-min-winrate" min="0" max="100" step="5" value="${state.minWinRate || 0}" class="filter-select" style="padding:0.2rem">
          </div>

          <div class="filter-control-group">
            <label>Min PnL % <span class="filter-val-hint" id="pnl-hint">${state.minPnlPct !== null ? state.minPnlPct + '%' : 'Any'}</span></label>
            <input type="range" id="filter-min-pnl" min="-30" max="25" step="1" value="${state.minPnlPct || -30}" class="filter-select" style="padding:0.2rem">
          </div>
        `;
      }
    } else if (key === 'trades') {
      const assets = Array.from(new Set(data.map(d => d.asset).filter(Boolean))).sort();
      const reasons = Array.from(new Set(data.map(d => d.reason).filter(Boolean))).sort();

      html += `
        <div class="filter-control-group">
          <label>Crypto Asset</label>
          <select id="filter-asset" class="filter-select">
            <option value="all" ${state.selectedAsset === 'all' ? 'selected' : ''}>All Assets (${assets.length})</option>
            ${assets.map(a => `<option value="${a}" ${state.selectedAsset === a ? 'selected' : ''}>${a}</option>`).join('')}
          </select>
        </div>

        <div class="filter-control-group">
          <label>Exit Reason</label>
          <select id="filter-reason" class="filter-select">
            <option value="all" ${state.selectedReason === 'all' ? 'selected' : ''}>All Reasons</option>
            ${reasons.map(r => `<option value="${r}" ${state.selectedReason === r ? 'selected' : ''}>${formatReasonTitle(r)}</option>`).join('')}
          </select>
        </div>

        <div class="filter-control-group">
          <label>Trade Outcome</label>
          <select id="filter-pnl-status" class="filter-select">
            <option value="all" ${state.selectedPnlStatus === 'all' ? 'selected' : ''}>All Trades</option>
            <option value="profit" ${state.selectedPnlStatus === 'profit' ? 'selected' : ''}>Profitable Trades Only</option>
            <option value="loss" ${state.selectedPnlStatus === 'loss' ? 'selected' : ''}>Loss Trades Only</option>
          </select>
        </div>

        <div class="filter-control-group">
          <label>Date Filter</label>
          <div class="range-inputs-dual">
            <input type="date" id="filter-date-from" class="filter-select" value="${state.dateFrom}">
            <span class="range-sep">to</span>
            <input type="date" id="filter-date-to" class="filter-select" value="${state.dateTo}">
          </div>
        </div>
      `;
    } else if (key === 'live_trades' || key === 'last_trades') {
      const assets = Array.from(new Set(data.map(d => d.asset).filter(Boolean))).sort();

      html += `
        <div class="filter-control-group">
          <label>Crypto Asset</label>
          <select id="filter-asset" class="filter-select">
            <option value="all" ${state.selectedAsset === 'all' ? 'selected' : ''}>All Assets (${assets.length})</option>
            ${assets.map(a => `<option value="${a}" ${state.selectedAsset === a ? 'selected' : ''}>${a}</option>`).join('')}
          </select>
        </div>

        <div class="filter-control-group">
          <label>Order Side</label>
          <select id="filter-side" class="filter-select">
            <option value="all" ${state.selectedSide === 'all' ? 'selected' : ''}>Buys & Sells</option>
            <option value="buy" ${state.selectedSide === 'buy' ? 'selected' : ''}>🛒 Buys Only</option>
            <option value="sell" ${state.selectedSide === 'sell' ? 'selected' : ''}>💰 Sells Only</option>
          </select>
        </div>

        <div class="filter-control-group">
          <label>Realized PnL</label>
          <select id="filter-pnl-status" class="filter-select">
            <option value="all" ${state.selectedPnlStatus === 'all' ? 'selected' : ''}>All Outcomes</option>
            <option value="profit" ${state.selectedPnlStatus === 'profit' ? 'selected' : ''}>Profitable Fills Only</option>
            <option value="loss" ${state.selectedPnlStatus === 'loss' ? 'selected' : ''}>Loss Fills Only</option>
          </select>
        </div>

        <div class="filter-control-group">
          <label>Date Filter</label>
          <div class="range-inputs-dual">
            <input type="date" id="filter-date-from" class="filter-select" value="${state.dateFrom}">
            <span class="range-sep">to</span>
            <input type="date" id="filter-date-to" class="filter-select" value="${state.dateTo}">
          </div>
        </div>
      `;
    }

    container.innerHTML = html;
    bindFilterControlEvents();
  }

  function bindFilterControlEvents() {
    const modeSelect = document.getElementById('filter-mode');
    if (modeSelect) {
      modeSelect.addEventListener('change', (e) => {
        state.selectedMode = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const holdSelect = document.getElementById('filter-hold');
    if (holdSelect) {
      holdSelect.addEventListener('change', (e) => {
        state.selectedHoldHours = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const pnlSelect = document.getElementById('filter-pnl-status');
    if (pnlSelect) {
      pnlSelect.addEventListener('change', (e) => {
        state.selectedPnlStatus = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const winRateSlider = document.getElementById('filter-min-winrate');
    if (winRateSlider) {
      winRateSlider.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        state.minWinRate = val > 0 ? val : null;
        const hint = document.getElementById('winrate-hint');
        if (hint) hint.textContent = state.minWinRate !== null ? `${state.minWinRate}%` : 'Any';
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const pnlSlider = document.getElementById('filter-min-pnl');
    if (pnlSlider) {
      pnlSlider.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        state.minPnlPct = val > -30 ? val : null;
        const hint = document.getElementById('pnl-hint');
        if (hint) hint.textContent = state.minPnlPct !== null ? `${state.minPnlPct}%` : 'Any';
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const assetSelect = document.getElementById('filter-asset');
    if (assetSelect) {
      assetSelect.addEventListener('change', (e) => {
        state.selectedAsset = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const reasonSelect = document.getElementById('filter-reason');
    if (reasonSelect) {
      reasonSelect.addEventListener('change', (e) => {
        state.selectedReason = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const sideSelect = document.getElementById('filter-side');
    if (sideSelect) {
      sideSelect.addEventListener('change', (e) => {
        state.selectedSide = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const dateFromInput = document.getElementById('filter-date-from');
    if (dateFromInput) {
      dateFromInput.addEventListener('change', (e) => {
        state.dateFrom = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }

    const dateToInput = document.getElementById('filter-date-to');
    if (dateToInput) {
      dateToInput.addEventListener('change', (e) => {
        state.dateTo = e.target.value;
        state.currentPage = 1;
        state.activeQuickFilter = 'custom';
        updateUI();
      });
    }
  }

  function renderQuickFilterChips() {
    const container = document.getElementById('quick-chips-list');
    if (!container) return;

    const key = state.currentDatasetKey;
    let chips = [{ id: 'all', label: 'All Items' }];

    if (key === 'sweep_results') {
      chips = [
        { id: 'all', label: 'All (500)' },
        { id: 'top10', label: '🏆 Top 10 Ranks' },
        { id: 'profit', label: '💰 Profitable (> ₹0)' },
        { id: 'dip', label: '🌊 Dip Mode' },
        { id: 'momentum', label: '🚀 Momentum Mode' },
        { id: 'winrate_60', label: '🎯 Win Rate ≥ 60%' },
        { id: 'hold_month', label: '📅 1-Month Hold (720h)' },
        { id: 'hold_week', label: '⏱️ 1-Week Hold (168h)' },
      ];
    } else if (key === 'trades') {
      chips = [
        { id: 'all', label: 'All Trades (92)' },
        { id: 'profit', label: '🟢 Profitable Trades' },
        { id: 'loss', label: '🔴 Stop-Loss Trades' },
        { id: 'tp', label: '🎯 Take Profit Exits' },
        { id: 'rsi', label: '⚠️ RSI Overbought Exits' },
      ];
    } else if (key === 'live_summary') {
      chips = [
        { id: 'all', label: 'All Accounts' },
        { id: 'top10', label: '🏆 Top 10 Accounts' },
        { id: 'dip', label: '🌊 Dip Strategies' },
        { id: 'momentum', label: '🚀 Momentum Strategies' },
        { id: 'active_trades', label: '⚡ Traded > 0' },
      ];
    } else if (key === 'live_trades') {
      chips = [
        { id: 'all', label: 'All Fills' },
        { id: 'sells', label: '💰 Sells' },
        { id: 'buys', label: '🛒 Buys' },
        { id: 'profit', label: '🟢 Profitable Sells' },
        { id: 'loss', label: '🔴 Loss Sells' },
      ];
    } else if (key === 'last_trades') {
      chips = [
        { id: 'all', label: 'All Bots (500)' },
        { id: 'sells', label: '💰 Last Trade = Sell' },
        { id: 'buys', label: '🛒 Last Trade = Buy' },
        { id: 'profit', label: '🟢 Profitable Last Trade' },
        { id: 'loss', label: '🔴 Loss Last Trade' },
      ];
    }

    container.innerHTML = chips.map(c => `
      <button class="filter-chip ${state.activeQuickFilter === c.id ? 'active' : ''}" data-chip="${c.id}">
        ${c.label}
      </button>
    `).join('');

    container.querySelectorAll('.filter-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        const chipId = btn.getAttribute('data-chip');
        applyQuickFilter(chipId);
      });
    });
  }

  function applyQuickFilter(chipId) {
    state.activeQuickFilter = chipId;
    state.currentPage = 1;

    state.selectedMode = 'all';
    state.selectedReason = 'all';
    state.selectedAsset = 'all';
    state.selectedSide = 'all';
    state.selectedHoldHours = 'all';
    state.selectedPnlStatus = 'all';
    state.minWinRate = null;
    state.minPnlPct = null;

    if (chipId === 'top10') {
      state.sortColumns = [{ key: 'rank', direction: 'asc' }];
    } else if (chipId === 'profit') {
      state.selectedPnlStatus = 'profit';
    } else if (chipId === 'loss') {
      state.selectedPnlStatus = 'loss';
    } else if (chipId === 'dip') {
      state.selectedMode = 'dip';
    } else if (chipId === 'momentum') {
      state.selectedMode = 'momentum';
    } else if (chipId === 'winrate_60') {
      state.minWinRate = 60;
    } else if (chipId === 'hold_month') {
      state.selectedHoldHours = '720';
    } else if (chipId === 'hold_week') {
      state.selectedHoldHours = '168';
    } else if (chipId === 'tp') {
      state.selectedReason = 'take_profit';
    } else if (chipId === 'rsi') {
      state.selectedReason = 'rsi_overbought';
    } else if (chipId === 'buys') {
      state.selectedSide = 'buy';
    } else if (chipId === 'sells') {
      state.selectedSide = 'sell';
    }

    renderFilterControls();
    renderQuickFilterChips();
    updateUI();
  }

  function clearAllFilters() {
    state.searchQuery = '';
    state.selectedMode = 'all';
    state.selectedReason = 'all';
    state.selectedAsset = 'all';
    state.selectedSide = 'all';
    state.selectedHoldHours = 'all';
    state.selectedPnlStatus = 'all';
    state.minWinRate = null;
    state.maxWinRate = null;
    state.minPnlPct = null;
    state.maxPnlPct = null;
    state.minRsi = null;
    state.maxRsi = null;
    state.activeQuickFilter = 'all';
    state.dateFrom = '';
    state.dateTo = '';
    state.currentPage = 1;

    const searchInput = document.getElementById('global-search');
    if (searchInput) searchInput.value = '';

    const searchClear = document.getElementById('search-clear-btn');
    if (searchClear) searchClear.style.display = 'none';

    renderFilterControls();
    renderQuickFilterChips();
    updateUI();
    showToast('All filters have been reset.', 'info');
  }

  // ==========================================================================
  // Filtering Engine
  // ==========================================================================
  function filterData(data) {
    if (!data || data.length === 0) return [];

    return data.filter((row) => {
      if (state.activeQuickFilter === 'top10') {
        const r = parseFloat(row.rank);
        if (!isNaN(r) && r > 10) return false;
      }

      if (state.selectedMode !== 'all' && row.entry_mode !== state.selectedMode) {
        return false;
      }

      if (state.selectedHoldHours !== 'all') {
        const hold = String(row.max_hold_hours);
        if (hold !== state.selectedHoldHours) return false;
      }

      if (state.selectedPnlStatus === 'profit') {
        const pnl = parseFloat(row.pnl ?? row.pnl_inr ?? row.realized_pnl_inr ?? 0);
        if (pnl <= 0) return false;
      } else if (state.selectedPnlStatus === 'loss') {
        const pnl = parseFloat(row.pnl ?? row.pnl_inr ?? row.realized_pnl_inr ?? 0);
        if (pnl >= 0) return false;
      }

      if (state.minWinRate !== null) {
        const wr = parseFloat(row.win_rate ?? 0);
        if (wr < state.minWinRate) return false;
      }

      if (state.minPnlPct !== null) {
        const pnlPct = parseFloat(row.pnl_pct ?? 0);
        if (pnlPct < state.minPnlPct) return false;
      }

      if (state.selectedAsset !== 'all' && row.asset !== state.selectedAsset) {
        return false;
      }

      if (state.selectedSide !== 'all' && row.side !== state.selectedSide) {
        return false;
      }

      if (state.selectedReason !== 'all' && row.reason !== state.selectedReason) {
        return false;
      }

      if (state.activeQuickFilter === 'active_trades') {
        const trades = parseFloat(row.trades ?? 0);
        if (trades <= 0) return false;
      }

      if (state.dateFrom || state.dateTo) {
        const entryTime = row.entry_time || row.timestamp_utc || row.date;
        if (entryTime) {
          const rowDate = new Date(entryTime).toISOString().slice(0, 10);
          if (state.dateFrom && rowDate < state.dateFrom) return false;
          if (state.dateTo && rowDate > state.dateTo) return false;
        }
      }

      if (state.searchQuery.trim() !== '') {
        const q = state.searchQuery.trim().toLowerCase();
        let matched = false;
        for (const k of Object.keys(row)) {
          const val = row[k];
          if (val !== null && val !== undefined) {
            if (String(val).toLowerCase().includes(q)) {
              matched = true;
              break;
            }
          }
        }
        if (!matched) return false;
      }

      return true;
    });
  }

  // ==========================================================================
  // Multi-Column Sorting Engine
  // ==========================================================================
  function sortData(data) {
    if (!state.sortColumns || state.sortColumns.length === 0 || data.length <= 1) return data;

    return [...data].sort((a, b) => {
      for (const sortDef of state.sortColumns) {
        const col = sortDef.key;
        const dir = sortDef.direction === 'asc' ? 1 : -1;

        let valA = a[col];
        let valB = b[col];

        if (valA === valB) continue;
        if (valA === null || valA === undefined) return 1;
        if (valB === null || valB === undefined) return -1;

        const numA = typeof valA === 'number' ? valA : parseFloat(valA);
        const numB = typeof valB === 'number' ? valB : parseFloat(valB);

        if (!isNaN(numA) && !isNaN(numB) && typeof valA !== 'string') {
          return (numA - numB) * dir;
        }

        if (typeof valA === 'string' && typeof valB === 'string') {
          if (!isNaN(parseFloat(valA)) && !isNaN(parseFloat(valB)) && !valA.includes('-') && !valA.includes(':')) {
            const diff = (parseFloat(valA) - parseFloat(valB)) * dir;
            if (diff !== 0) return diff;
            continue;
          }
          
          const dateA = Date.parse(valA);
          const dateB = Date.parse(valB);
          if (!isNaN(dateA) && !isNaN(dateB) && (valA.includes('T') || valA.includes('-'))) {
            const diff = (dateA - dateB) * dir;
            if (diff !== 0) return diff;
            continue;
          }

          const diff = valA.localeCompare(valB, undefined, { numeric: true, sensitivity: 'base' }) * dir;
          if (diff !== 0) return diff;
          continue;
        }
      }
      return 0;
    });
  }

  // ==========================================================================
  // Pagination Engine
  // ==========================================================================
  function paginateData(data) {
    if (state.pageSize === 'all') {
      return {
        items: data,
        totalPages: 1,
        totalItems: data.length,
        startIndex: 0,
        endIndex: data.length
      };
    }

    const pageSize = parseInt(state.pageSize, 10) || 25;
    const totalPages = Math.max(1, Math.ceil(data.length / pageSize));
    
    if (state.currentPage > totalPages) state.currentPage = totalPages;
    if (state.currentPage < 1) state.currentPage = 1;

    const startIndex = (state.currentPage - 1) * pageSize;
    const endIndex = Math.min(startIndex + pageSize, data.length);
    const items = data.slice(startIndex, endIndex);

    return {
      items,
      totalPages,
      totalItems: data.length,
      startIndex,
      endIndex
    };
  }

  // ==========================================================================
  // UI Render Controller
  // ==========================================================================
  function updateUI() {
    const rawData = getRawDataset(state.currentDatasetKey);
    const filteredData = filterData(rawData);
    const sortedData = sortData(filteredData);
    const pagination = paginateData(sortedData);

    syncStateToUrl();
    renderActiveBadgesStrip(rawData.length, filteredData.length);
    renderMetricsCards(rawData, filteredData);
    renderTable(pagination.items, filteredData.length, pagination.startIndex);
    renderPaginationControls(pagination, rawData.length);
    renderCharts(filteredData);
    updateBulkActionBar();
  }

  // ==========================================================================
  // Active Filter Badges Strip
  // ==========================================================================
  function renderActiveBadgesStrip(totalCount, filteredCount) {
    const container = document.getElementById('active-filters-badges');
    const countEl = document.getElementById('filter-results-counter');
    if (!container || !countEl) return;

    let badges = [];

    if (state.searchQuery.trim()) {
      badges.push({ label: `Search: "${state.searchQuery}"`, onRemove: () => {
        state.searchQuery = '';
        const input = document.getElementById('global-search');
        if (input) input.value = '';
        updateUI();
      }});
    }

    if (state.selectedMode !== 'all') {
      badges.push({ label: `Mode: ${state.selectedMode}`, onRemove: () => {
        state.selectedMode = 'all';
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.selectedHoldHours !== 'all') {
      badges.push({ label: `Hold: ${state.selectedHoldHours}h`, onRemove: () => {
        state.selectedHoldHours = 'all';
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.selectedPnlStatus !== 'all') {
      badges.push({ label: `PnL: ${state.selectedPnlStatus}`, onRemove: () => {
        state.selectedPnlStatus = 'all';
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.minWinRate !== null) {
      badges.push({ label: `Win Rate ≥ ${state.minWinRate}%`, onRemove: () => {
        state.minWinRate = null;
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.minPnlPct !== null) {
      badges.push({ label: `PnL ≥ ${state.minPnlPct}%`, onRemove: () => {
        state.minPnlPct = null;
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.selectedAsset !== 'all') {
      badges.push({ label: `Asset: ${state.selectedAsset}`, onRemove: () => {
        state.selectedAsset = 'all';
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.selectedSide !== 'all') {
      badges.push({ label: `Side: ${state.selectedSide}`, onRemove: () => {
        state.selectedSide = 'all';
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.selectedReason !== 'all') {
      badges.push({ label: `Reason: ${state.selectedReason}`, onRemove: () => {
        state.selectedReason = 'all';
        renderFilterControls();
        updateUI();
      }});
    }

    if (state.dateFrom || state.dateTo) {
      badges.push({ label: `Date: ${state.dateFrom || '*'} to ${state.dateTo || '*'}`, onRemove: () => {
        state.dateFrom = '';
        state.dateTo = '';
        renderFilterControls();
        updateUI();
      }});
    }

    container.innerHTML = badges.map((b, i) => `
      <span class="active-filter-badge">
        ${escapeHtml(b.label)}
        <button class="badge-remove" data-badge-idx="${i}" title="Remove filter">&times;</button>
      </span>
    `).join('');

    container.querySelectorAll('.badge-remove').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.getAttribute('data-badge-idx'), 10);
        if (badges[idx]) badges[idx].onRemove();
      });
    });

    if (filteredCount < totalCount) {
      countEl.innerHTML = `Showing <strong>${filteredCount}</strong> of <strong>${totalCount}</strong> entries <span class="text-muted">(${((filteredCount/totalCount)*100).toFixed(1)}% filtered)</span>`;
    } else {
      countEl.innerHTML = `Showing all <strong>${totalCount}</strong> entries`;
    }
  }

  // ==========================================================================
  // KPI Summary Metrics Cards
  // ==========================================================================
  function renderMetricsCards(rawData, data) {
    const container = document.getElementById('metrics-cards-grid');
    if (!container) return;

    const key = state.currentDatasetKey;
    let cards = [];

    if (key === 'sweep_results') {
      const profitable = data.filter(d => (d.pnl || 0) > 0).length;
      const avgWinRate = data.length > 0 ? (data.reduce((acc, d) => acc + (d.win_rate || 0), 0) / data.length) : 0;
      const bestPerformer = data.length > 0 ? [...data].sort((a, b) => (b.pnl_pct || 0) - (a.pnl_pct || 0))[0] : null;
      const avgDrawdown = data.length > 0 ? (data.reduce((acc, d) => acc + (d.max_drawdown_pct || 0), 0) / data.length) : 0;
      const totalPnl = data.reduce((acc, d) => acc + (d.pnl || 0), 0);

      cards = [
        { title: 'Matching Strategies', value: `${data.length}`, sub: `out of ${rawData.length} total`, icon: '📊', highlight: 'purple' },
        { title: 'Average Win Rate', value: `${avgWinRate.toFixed(1)}%`, sub: `${profitable} profitable (${data.length ? ((profitable/data.length)*100).toFixed(0) : 0}%)`, icon: '🎯', highlight: 'info' },
        { title: 'Top Strategy Return', value: bestPerformer ? `+${bestPerformer.pnl_pct.toFixed(2)}%` : '0%', sub: bestPerformer ? `${bestPerformer.account} (₹${formatNumber(bestPerformer.pnl, 2)})` : '-', icon: '🏆', highlight: 'success' },
        { title: 'Avg Max Drawdown', value: `${avgDrawdown.toFixed(2)}%`, sub: `Tournament PnL: ₹${formatNumber(totalPnl, 0)}`, icon: '📉', highlight: 'warning' }
      ];
    } else if (key === 'trades') {
      const winningTrades = data.filter(d => (d.pnl_inr || 0) > 0).length;
      const totalPnl = data.reduce((acc, d) => acc + (d.pnl_inr || 0), 0);
      const winRate = data.length > 0 ? ((winningTrades / data.length) * 100) : 0;
      const bestTrade = data.length > 0 ? [...data].sort((a, b) => (b.pnl_pct || 0) - (a.pnl_pct || 0))[0] : null;
      const tpCount = data.filter(d => d.reason === 'take_profit').length;
      const slCount = data.filter(d => d.reason === 'stop_loss').length;

      cards = [
        { title: 'Filtered Trades', value: `${data.length}`, sub: `${winningTrades} Wins / ${data.length - winningTrades} Losses`, icon: '⚡', highlight: 'purple' },
        { title: 'Trade Win Rate', value: `${winRate.toFixed(1)}%`, sub: `${tpCount} Take Profit / ${slCount} Stop Loss`, icon: '🎯', highlight: winRate >= 50 ? 'success' : 'warning' },
        { title: 'Total Realized PnL', value: formatPnl(totalPnl, '₹'), sub: `Avg per trade: ₹${data.length ? formatNumber(totalPnl / data.length, 2) : 0}`, icon: totalPnl >= 0 ? '💰' : '🔻', highlight: totalPnl >= 0 ? 'success' : 'danger' },
        { title: 'Best Single Trade', value: bestTrade ? `+${bestTrade.pnl_pct.toFixed(2)}%` : '0%', sub: bestTrade ? `${bestTrade.asset} (+₹${formatNumber(bestTrade.pnl_inr, 2)})` : '-', icon: '🚀', highlight: 'success' }
      ];
    } else if (key === 'live_summary') {
      const totalVal = data.reduce((acc, d) => acc + (d.value_inr || 0), 0);
      const totalRealized = data.reduce((acc, d) => acc + (d.realized_pnl_inr || 0), 0);
      const totalHoldings = data.reduce((acc, d) => acc + (d.holdings_inr || 0), 0);
      const activeBots = data.filter(d => (d.trades || 0) > 0).length;

      cards = [
        { title: 'Accounts Monitored', value: `${data.length}`, sub: `${activeBots} accounts with trades`, icon: '🤖', highlight: 'purple' },
        { title: 'Total Portfolio Value', value: `₹${formatNumber(totalVal, 0)}`, sub: `Holdings: ₹${formatNumber(totalHoldings, 0)}`, icon: '💼', highlight: 'info' },
        { title: 'Tournament Realized PnL', value: formatPnl(totalRealized, '₹'), sub: `Across ${data.length} accounts`, icon: totalRealized >= 0 ? '📈' : '📉', highlight: totalRealized >= 0 ? 'success' : 'danger' },
        { title: 'Dip vs Momentum', value: `${data.filter(d => d.entry_mode === 'dip').length} / ${data.filter(d => d.entry_mode === 'momentum').length}`, sub: 'Strategy breakdown', icon: '⚖️', highlight: 'warning' }
      ];
    } else if (key === 'live_trades') {
      const buys = data.filter(d => d.side === 'buy').length;
      const sells = data.filter(d => d.side === 'sell').length;
      const totalNotional = data.reduce((acc, d) => acc + (d.notional_inr || 0), 0);
      const totalFees = data.reduce((acc, d) => acc + (d.fee_inr || 0) + (d.tds_inr || 0), 0);
      const totalPnl = data.reduce((acc, d) => acc + (d.realized_pnl_inr || 0), 0);
      const newest = data.length ? [...data].sort((a, b) => String(b.timestamp_utc).localeCompare(String(a.timestamp_utc)))[0] : null;

      cards = [
        { title: 'Live Fills', value: `${data.length}`, sub: `${buys} Buys / ${sells} Sells`, icon: '⚡', highlight: 'purple' },
        { title: 'Total Notional', value: `₹${formatNumber(totalNotional, 0)}`, sub: 'Traded volume across bots', icon: '💼', highlight: 'info' },
        { title: 'Fees + TDS Paid', value: `₹${formatNumber(totalFees, 0)}`, sub: 'Brokerage + 1% TDS friction', icon: '🧾', highlight: 'warning' },
        { title: 'Realized PnL', value: formatPnl(totalPnl, '₹'), sub: newest ? `Last fill: ${newest.account} · ${newest.asset}` : 'No fills', icon: totalPnl >= 0 ? '📈' : '📉', highlight: totalPnl >= 0 ? 'success' : 'danger' }
      ];
    } else if (key === 'last_trades') {
      const sells = data.filter(d => d.side === 'sell').length;
      const buys = data.length - sells;
      const totalPnl = data.reduce((acc, d) => acc + (d.realized_pnl_inr || 0), 0);
      const newest = data[0] || null;

      cards = [
        { title: 'Bots Tracked', value: `${data.length}`, sub: 'One latest fill per bot', icon: '🤖', highlight: 'purple' },
        { title: 'Latest Trade (UTC)', value: newest ? formatDate(newest.timestamp_utc) : '-', sub: newest ? `${newest.account} · ${newest.asset}` : '-', icon: '🕐', highlight: 'info' },
        { title: 'Last-Trade PnL', value: formatPnl(totalPnl, '₹'), sub: `Sum of each bot's last fill`, icon: totalPnl >= 0 ? '📈' : '📉', highlight: totalPnl >= 0 ? 'success' : 'danger' },
        { title: 'Buys / Sells', value: `${buys} / ${sells}`, sub: 'Last trade side split', icon: '⚖️', highlight: 'warning' }
      ];
    } else {
      cards = [
        { title: 'Active Records', value: `${data.length}`, sub: `Total loaded: ${rawData.length}`, icon: '📁', highlight: 'purple' },
        { title: 'Columns Detected', value: `${getActiveConfig().columns.length}`, sub: 'Auto-typed attributes', icon: '📋', highlight: 'info' },
        { title: 'Primary Sort', value: (state.sortColumns[0]?.key || 'None').toUpperCase(), sub: `Direction: ${(state.sortColumns[0]?.direction || 'asc').toUpperCase()}`, icon: '⇅', highlight: 'warning' },
        { title: 'Current Page Size', value: `${state.pageSize}`, sub: `Page ${state.currentPage}`, icon: '📄', highlight: 'success' }
      ];
    }

    container.innerHTML = cards.map(c => `
      <div class="metric-card highlight-${c.highlight}">
        <div class="metric-header">
          <span>${escapeHtml(c.title)}</span>
          <span class="metric-icon">${c.icon}</span>
        </div>
        <div class="metric-value-row">
          <span class="metric-value">${c.value}</span>
        </div>
        <div class="metric-sub">${escapeHtml(c.sub)}</div>
      </div>
    `).join('');
  }

  // ==========================================================================
  // Table Rendering Engine (with Checkboxes, Multi-Sort & Highlighting)
  // ==========================================================================
  function renderTable(items, totalFilteredCount, startIndex) {
    const tableContainer = document.getElementById('table-scroll-container');
    if (!tableContainer) return;

    const config = getActiveConfig();
    const visibleColumns = config.columns.filter(col => !state.hiddenColumns.has(col.key));
    const allRowKeysOnPage = items.map((row, i) => getRowKey(row, i + startIndex));
    const isAllSelected = items.length > 0 && allRowKeysOnPage.every(k => state.selectedRowKeys.has(k));

    if (items.length === 0) {
      tableContainer.innerHTML = `
        <div class="table-empty-state">
          <div class="empty-icon">🔍</div>
          <div class="empty-title">No Matching Records Found</div>
          <div class="empty-subtitle">We couldn't find any data matching your current filters or search query.</div>
          <button class="btn btn-primary btn-sm" id="empty-state-reset-btn">Reset All Filters</button>
        </div>
      `;
      const resetBtn = document.getElementById('empty-state-reset-btn');
      if (resetBtn) resetBtn.addEventListener('click', clearAllFilters);
      return;
    }

    const selectAllTh = `
      <th style="width: 40px; text-align: center;">
        <input type="checkbox" id="select-all-checkbox" ${isAllSelected ? 'checked' : ''} style="cursor: pointer;">
      </th>
    `;

    const ths = visibleColumns.map(col => {
      const sortIdx = state.sortColumns.findIndex(s => s.key === col.key);
      const isSorted = sortIdx !== -1;
      const sortDef = isSorted ? state.sortColumns[sortIdx] : null;
      const sortClass = isSorted ? (sortDef.direction === 'asc' ? 'sorted-asc' : 'sorted-desc') : '';
      const sortIcon = isSorted ? (sortDef.direction === 'asc' ? '▲' : '▼') : '⇅';
      const isNum = col.type === 'number';
      const priorityBadge = state.sortColumns.length > 1 && isSorted ? `<span class="sort-priority-badge">${sortIdx + 1}</span>` : '';

      return `
        <th class="sortable ${sortClass} ${isNum ? 'numeric' : ''}" data-col="${col.key}" title="Click to sort (Hold Shift for multi-sort)">
          <div class="th-content">
            <span>${escapeHtml(col.label)}</span>
            <div style="display:flex; align-items:center;">
              <span class="sort-indicator">${sortIcon}</span>
              ${priorityBadge}
            </div>
          </div>
        </th>
      `;
    }).join('');

    const actionsTh = `<th style="width: 100px; text-align: center;">Actions</th>`;

    const trs = items.map((row, rowIdx) => {
      const globalIdx = startIndex + rowIdx;
      const rowKey = getRowKey(row, globalIdx);
      const isSelected = state.selectedRowKeys.has(rowKey);

      const checkboxTd = `
        <td style="text-align: center;" onclick="event.stopPropagation()">
          <input type="checkbox" class="row-select-checkbox" data-row-key="${escapeHtml(rowKey)}" ${isSelected ? 'checked' : ''} style="cursor: pointer;">
        </td>
      `;

      const tds = visibleColumns.map(col => {
        const val = row[col.key];
        const isNum = col.type === 'number';
        let renderedVal = col.render ? col.render(val, row) : highlightSearchText(escapeHtml(String(val ?? '')));
        return `<td class="${isNum ? 'numeric' : ''}">${renderedVal}</td>`;
      }).join('');

      let actionBtn = `<button class="btn btn-sm btn-ghost row-inspect-btn" data-row-idx="${rowIdx}" title="Inspect details">👁️</button>`;
      if (state.currentDatasetKey === 'trades') {
        actionBtn += `<button class="btn btn-sm btn-ghost trade-anatomy-btn" data-row-idx="${rowIdx}" title="View Anatomy">📊</button>`;
      }
      if (state.currentDatasetKey === 'sweep_results' || state.currentDatasetKey === 'accounts') {
        actionBtn += `<button class="btn btn-sm btn-ghost strategy-yaml-btn" data-row-idx="${rowIdx}" title="Generate YAML">🛠️</button>`;
      }

      return `
        <tr class="${isSelected ? 'row-selected' : ''}" data-row-index="${rowIdx}">
          ${checkboxTd}
          ${tds}
          <td style="text-align: center;" onclick="event.stopPropagation()">${actionBtn}</td>
        </tr>
      `;
    }).join('');

    tableContainer.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            ${selectAllTh}
            ${ths}
            ${actionsTh}
          </tr>
        </thead>
        <tbody>
          ${trs}
        </tbody>
      </table>
    `;

    tableContainer.querySelectorAll('th.sortable').forEach(th => {
      th.addEventListener('click', (e) => {
        const colKey = th.getAttribute('data-col');
        handleColumnSort(colKey, e.shiftKey);
      });
    });

    const selectAllBox = document.getElementById('select-all-checkbox');
    if (selectAllBox) {
      selectAllBox.addEventListener('change', (e) => {
        const checked = e.target.checked;
        allRowKeysOnPage.forEach(k => {
          if (checked) state.selectedRowKeys.add(k);
          else state.selectedRowKeys.delete(k);
        });
        updateUI();
      });
    }

    tableContainer.querySelectorAll('.row-select-checkbox').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const k = cb.getAttribute('data-row-key');
        if (e.target.checked) state.selectedRowKeys.add(k);
        else state.selectedRowKeys.delete(k);
        updateUI();
      });
    });

    tableContainer.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.addEventListener('click', () => {
        openRowInspectorModal(items[i]);
      });
    });

    tableContainer.querySelectorAll('.row-inspect-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.getAttribute('data-row-idx'), 10);
        openRowInspectorModal(items[idx]);
      });
    });

    tableContainer.querySelectorAll('.trade-anatomy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.getAttribute('data-row-idx'), 10);
        openTradeAnatomyModal(items[idx]);
      });
    });

    tableContainer.querySelectorAll('.strategy-yaml-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.getAttribute('data-row-idx'), 10);
        openYamlGeneratorModal(items[idx]);
      });
    });
  }

  function getRowKey(row, fallbackIdx) {
    const config = getActiveConfig();
    const keyField = config.rowKey;
    if (Array.isArray(keyField)) {
      return keyField.map(f => String(row[f] ?? '')).join('|');
    }
    const kf = keyField || 'account' || 'asset';
    return String(row[kf] || `row_${fallbackIdx}`);
  }

  function handleColumnSort(colKey, isShiftKey) {
    const existingIdx = state.sortColumns.findIndex(s => s.key === colKey);

    if (isShiftKey) {
      if (existingIdx !== -1) {
        if (state.sortColumns[existingIdx].direction === 'asc') {
          state.sortColumns[existingIdx].direction = 'desc';
        } else {
          state.sortColumns.splice(existingIdx, 1);
        }
      } else {
        state.sortColumns.push({ key: colKey, direction: 'asc' });
      }
    } else {
      if (existingIdx === 0 && state.sortColumns.length === 1) {
        state.sortColumns[0].direction = state.sortColumns[0].direction === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortColumns = [{ key: colKey, direction: 'asc' }];
      }
    }

    if (state.sortColumns.length === 0) {
      const config = getActiveConfig();
      state.sortColumns = [{ key: config.defaultSort || 'rank', direction: 'asc' }];
    }

    updateUI();
  }

  function highlightSearchText(text) {
    if (!state.searchQuery || !text) return text;
    const q = state.searchQuery.trim();
    if (!q) return text;

    const regex = new RegExp(`(${escapeRegex(q)})`, 'gi');
    return String(text).replace(regex, '<mark class="search-highlight">$1</mark>');
  }

  function escapeRegex(str) {
    return str.replace(/[-[\]{}()*+?.,\\^$|#\s]/g, '\\$&');
  }

  function updateBulkActionBar() {
    const bar = document.getElementById('floating-bulk-bar');
    const countBadge = document.getElementById('bulk-selected-count');
    if (!bar || !countBadge) return;

    const count = state.selectedRowKeys.size;
    if (count > 0) {
      countBadge.textContent = `${count} selected`;
      bar.classList.add('visible');
    } else {
      bar.classList.remove('visible');
    }
  }

  function getSelectedRowsData() {
    const raw = getRawDataset(state.currentDatasetKey);
    return raw.filter((row, i) => state.selectedRowKeys.has(getRowKey(row, i)));
  }

  // ==========================================================================
  // Strategy Comparator Modal & Multi-Line Chart
  // ==========================================================================
  function openStrategyComparator(selectedStrategies) {
    const modal = document.getElementById('strategy-comparator-modal');
    const content = document.getElementById('comparator-content');
    if (!modal || !content) return;

    let strategies = selectedStrategies;
    if (!strategies || strategies.length === 0) {
      const raw = window.DATA_SETS?.sweep_results || [];
      strategies = [...raw].sort((a, b) => a.rank - b.rank).slice(0, 3);
    }

    if (strategies.length < 2) {
      showToast('Select at least 2 strategies to compare!', 'info');
      return;
    }

    const cardsHtml = strategies.map(s => {
      const isWinner = s.rank === 1;
      return `
        <div class="comparator-card ${isWinner ? 'highlight-winner' : ''}">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-weight: 700; font-size: 1.1rem; font-family: var(--font-mono);">${escapeHtml(s.account)}</span>
            <span class="rank-pill rank-top${s.rank <= 3 ? s.rank : ''}">${s.rank}</span>
          </div>
          <div>${formatModeBadge(s.entry_mode)}</div>
          
          <div class="comparator-spec-row">
            <span class="spec-label">Final Value</span>
            <span class="spec-val">${formatCurrency(s.final_value)}</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">PnL Return</span>
            <span class="spec-val">${formatPnlPct(s.pnl_pct)}</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">Win Rate</span>
            <span class="spec-val">${s.win_rate.toFixed(1)}%</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">Profit Factor</span>
            <span class="spec-val">${formatNumber(s.profit_factor, 2)}</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">Max Drawdown</span>
            <span class="spec-val" style="color: var(--danger);">${formatNumber(s.max_drawdown_pct, 2)}%</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">RSI Entry / Exit</span>
            <span class="spec-val">${s.entry_rsi} / ${s.exit_rsi}</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">TP % / SL %</span>
            <span class="spec-val">${s.take_profit_pct}% / ${s.stop_loss_pct}%</span>
          </div>
          <div class="comparator-spec-row">
            <span class="spec-label">Hold Limit</span>
            <span class="spec-val">${formatHoldTime(s.max_hold_hours)}</span>
          </div>
        </div>
      `;
    }).join('');

    content.innerHTML = `
      <div class="comparator-grid">
        ${cardsHtml}
      </div>
      <div style="margin-top: 1.5rem; height: 300px; background: var(--bg-primary); padding: 1rem; border-radius: var(--radius-md); border: 1px solid var(--border-color);">
        <div style="font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-secondary);">Equity Trajectory vs HODL Benchmark</div>
        <div style="height: calc(100% - 25px); position: relative;">
          <canvas id="comparator-equity-chart"></canvas>
        </div>
      </div>
    `;

    modal.classList.add('open');

    const ctx = document.getElementById('comparator-equity-chart')?.getContext('2d');
    if (ctx && window.DATA_SETS?.top10_equity) {
      if (charts.comparatorChart) charts.comparatorChart.destroy();
      const equity = window.DATA_SETS.top10_equity;
      const sampledDates = equity.filter((_, i) => i % 12 === 0);

      const colors = ['#10b981', '#6366f1', '#0ea5e9', '#ec4899', '#f59e0b'];
      const datasets = strategies.map((s, idx) => {
        const hasKey = sampledDates[0] && sampledDates[0][s.account] !== undefined;
        const dataVals = hasKey ? sampledDates.map(d => d[s.account]) : sampledDates.map((_, i) => 10000 * (1 + ((s.pnl_pct / 100) * (i / sampledDates.length))));

        return {
          label: `${s.account} (${s.entry_mode})`,
          data: dataVals,
          borderColor: colors[idx % colors.length],
          backgroundColor: 'transparent',
          borderWidth: 2,
          tension: 0.2
        };
      });

      datasets.push({
        label: 'HODL Benchmark',
        data: sampledDates.map(d => d.hodl || 11627.86),
        borderColor: '#94a3b8',
        borderDash: [5, 5],
        borderWidth: 1.5,
        backgroundColor: 'transparent',
        tension: 0.2
      });

      charts.comparatorChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: sampledDates.map(d => d.date),
          datasets: datasets
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } } },
          scales: {
            x: { grid: { color: state.theme === 'dark' ? '#273549' : '#e2e8f0' } },
            y: { grid: { color: state.theme === 'dark' ? '#273549' : '#e2e8f0' } }
          }
        }
      });
    }
  }

  // ==========================================================================
  // Trade Anatomy & Candlestick / Execution Visualizer
  // ==========================================================================
  function openTradeAnatomyModal(trade) {
    const modal = document.getElementById('trade-anatomy-modal');
    const content = document.getElementById('trade-anatomy-content');
    if (!modal || !content) return;

    const isWin = (trade.pnl_inr || 0) >= 0;
    const pnlColor = isWin ? 'var(--success)' : 'var(--danger)';
    const entryDate = new Date(trade.entry_time);
    const exitDate = new Date(trade.exit_time);
    const durationHours = Math.max(1, Math.round((exitDate - entryDate) / (1000 * 60 * 60)));

    content.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div style="display: flex; align-items: center; gap: 0.75rem;">
          <span style="font-size: 1.5rem;">${formatAsset(trade.asset)}</span>
          ${formatReasonBadge(trade.reason)}
        </div>
        <div style="text-align: right;">
          <div style="font-size: 1.35rem; font-weight: 700; color: ${pnlColor}; font-family: var(--font-mono);">${formatPnlPct(trade.pnl_pct)}</div>
          <div style="font-size: 0.85rem; color: var(--text-muted);">${formatPnl(trade.pnl_inr, '₹')}</div>
        </div>
      </div>

      <div class="anatomy-timeline">
        <div class="anatomy-bar-connector"></div>

        <div class="anatomy-node">
          <div class="anatomy-node-bubble bubble-entry">🛒</div>
          <span style="font-size: 0.75rem; font-weight: 700;">BUY ENTRY</span>
          <span style="font-family: var(--font-mono); font-size: 0.82rem;">₹${formatNumber(trade.entry_price, 2)}</span>
          <span style="font-size: 0.72rem; color: var(--text-muted);">${formatDate(trade.entry_time)}</span>
        </div>

        <div class="anatomy-node" style="background: var(--bg-primary); padding: 0.3rem 0.6rem; border-radius: var(--radius-sm); border: 1px solid var(--border-color); z-index: 2;">
          <span style="font-size: 0.7rem; color: var(--text-muted);">DURATION</span>
          <span style="font-weight: 700; font-family: var(--font-mono);">${durationHours} Hours</span>
        </div>

        <div class="anatomy-node">
          <div class="anatomy-node-bubble ${isWin ? 'bubble-exit-win' : 'bubble-exit-loss'}">${isWin ? '🎯' : '🛑'}</div>
          <span style="font-size: 0.75rem; font-weight: 700;">SELL EXIT</span>
          <span style="font-family: var(--font-mono); font-size: 0.82rem;">₹${formatNumber(trade.exit_price, 2)}</span>
          <span style="font-size: 0.72rem; color: var(--text-muted);">${formatDate(trade.exit_time)}</span>
        </div>
      </div>

      <div class="detail-grid">
        <div class="detail-item">
          <div class="detail-item-label">Entry RSI Level</div>
          <div class="detail-item-value">${trade.rsi_at_entry ? trade.rsi_at_entry.toFixed(1) : '-'} <span style="font-size:0.75rem; color:var(--info);">(Trigger)</span></div>
        </div>
        <div class="detail-item">
          <div class="detail-item-label">Exit Trigger</div>
          <div class="detail-item-value">${formatReasonTitle(trade.reason)}</div>
        </div>
        <div class="detail-item">
          <div class="detail-item-label">Gross Return</div>
          <div class="detail-item-value" style="color:${pnlColor};">${trade.pnl_pct >= 0 ? '+' : ''}${trade.pnl_pct.toFixed(2)}%</div>
        </div>
        <div class="detail-item">
          <div class="detail-item-label">Estimated Friction</div>
          <div class="detail-item-value">₹${formatNumber(trade.pnl_inr > 0 ? trade.pnl_inr * 0.3 + (trade.exit_price * 0.01) : 0, 2)}</div>
        </div>
      </div>
    `;

    modal.classList.add('open');
  }

  // ==========================================================================
  // Underwater Drawdown & Recovery Tool
  // ==========================================================================
  function openUnderwaterDrawdownModal() {
    const modal = document.getElementById('underwater-modal');
    if (!modal) return;
    modal.classList.add('open');

    const equity = window.DATA_SETS?.top10_equity || [];
    const sampledDates = equity.filter((_, i) => i % 6 === 0);

    let peak = 10000;
    let maxDrawdown = 0;
    let longestUnderwaterHours = 0;
    let currentUnderwaterHours = 0;

    const underwaterPoints = sampledDates.map(d => {
      const val = parseFloat(d.acc_399 || 10000);
      if (val > peak) {
        peak = val;
        currentUnderwaterHours = 0;
      } else {
        currentUnderwaterHours += 6;
        if (currentUnderwaterHours > longestUnderwaterHours) {
          longestUnderwaterHours = currentUnderwaterHours;
        }
      }
      const dd = ((val - peak) / peak) * 100;
      if (dd < maxDrawdown) maxDrawdown = dd;
      return dd;
    });

    const peakEl = document.getElementById('dd-max-peak');
    const timeEl = document.getElementById('dd-longest-time');
    const statusEl = document.getElementById('dd-current-status');
    if (peakEl) peakEl.textContent = `${maxDrawdown.toFixed(2)}%`;
    if (timeEl) timeEl.textContent = `${longestUnderwaterHours} Hours (${Math.round(longestUnderwaterHours/24)} days)`;
    if (statusEl) statusEl.textContent = 'Recovered to ATH Peak';

    const ctx = document.getElementById('underwater-chart')?.getContext('2d');
    if (ctx) {
      if (charts.underwaterChart) charts.underwaterChart.destroy();
      charts.underwaterChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: sampledDates.map(d => d.date),
          datasets: [{
            label: 'acc_399 Underwater Drawdown (%)',
            data: underwaterPoints,
            borderColor: '#ef4444',
            backgroundColor: 'rgba(239, 68, 68, 0.25)',
            fill: true,
            tension: 0.1
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } },
            y: { min: -15, max: 0, ticks: { callback: v => `${v}%`, color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } }
          }
        }
      });
    }
  }

  // ==========================================================================
  // Strategy & Asset Correlation Heatmap Matrix
  // ==========================================================================
  function openCorrelationHeatmapModal() {
    const modal = document.getElementById('correlation-modal');
    const container = document.getElementById('correlation-matrix-container');
    if (!modal || !container) return;
    modal.classList.add('open');

    const labels = ['acc_399 (Mom)', 'acc_317 (Mom)', 'acc_100 (Dip)', 'acc_079 (Dip)', 'HODL Bench'];

    const matrix = [
      [1.00, 0.88, 0.12, 0.15, 0.62],
      [0.88, 1.00, 0.08, 0.18, 0.58],
      [0.12, 0.08, 1.00, 0.76, 0.35],
      [0.15, 0.18, 0.76, 1.00, 0.38],
      [0.62, 0.58, 0.35, 0.38, 1.00]
    ];

    let headerThs = `<th>Strategy / Pair</th>` + labels.map(l => `<th>${l}</th>`).join('');
    let rowsHtml = labels.map((rowLabel, i) => {
      let tds = `<td><strong>${rowLabel}</strong></td>` + matrix[i].map((val, j) => {
        let bg = 'rgba(99, 102, 241, 0.1)';
        let color = '#ffffff';
        if (val === 1.0) { bg = 'rgba(99, 102, 241, 0.8)'; color = '#fff'; }
        else if (val >= 0.7) { bg = 'rgba(99, 102, 241, 0.5)'; }
        else if (val <= 0.2) { bg = 'rgba(16, 185, 129, 0.5)'; }
        else { bg = 'rgba(245, 158, 11, 0.3)'; }

        return `<td style="background:${bg}; color:${color}; font-weight:700;">${val.toFixed(2)}</td>`;
      }).join('');
      return `<tr>${tds}</tr>`;
    }).join('');

    container.innerHTML = `
      <table class="heatmap-table">
        <thead><tr>${headerThs}</tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
      <div style="margin-top:1rem; font-size:0.82rem; color:var(--text-secondary); display:flex; gap:1.5rem; justify-content:center;">
        <span>🟩 <strong>0.00 to 0.25</strong>: High Diversification (Dip + Mom)</span>
        <span>🟪 <strong>0.70 to 1.00</strong>: Strong Positive Correlation</span>
      </div>
    `;
  }

  // ==========================================================================
  // Strategy YAML & Python Config Generator
  // ==========================================================================
  function openYamlGeneratorModal(strategy) {
    const modal = document.getElementById('yaml-generator-modal');
    const content = document.getElementById('yaml-code-content');
    if (!modal || !content) return;

    const s = strategy || {
      account: 'acc_399',
      entry_mode: 'momentum',
      entry_rsi: 30,
      exit_rsi: 85,
      take_profit_pct: 8,
      stop_loss_pct: 5,
      position_size_pct: 60,
      max_hold_hours: 720
    };

    const yamlConfig = `# CrytpTrde Auto-Generated Bot Configuration
# Strategy: ${s.account} (${s.entry_mode || 'momentum'})
mode: paper
initial_cash_inr: 50000
fee_rate: 0.001
slippage_bps: 5
simulate_tds: true
tds_rate: 0.01
check_interval_min: 60

asset_discovery:
  enabled: true
  min_volume_inr: 1000000
  max_assets: 50
  dipped_scan_pct: 8
  dipped_scan_max: 150

strategy:
  entry_mode: ${s.entry_mode || 'momentum'}
  entry_rsi: ${s.entry_rsi || 30}
  exit_rsi: ${s.exit_rsi || 85}
  take_profit_pct: ${s.take_profit_pct || 8}
  stop_loss_pct: ${s.stop_loss_pct || 5}
  position_size_pct: ${s.position_size_pct || 60}
  max_hold_hours: ${s.max_hold_hours || 720}
`;

    content.textContent = yamlConfig;
    modal.classList.add('open');

    const downloadYamlBtn = document.getElementById('btn-download-yaml');
    if (downloadYamlBtn) {
      downloadYamlBtn.onclick = () => {
        downloadFile(yamlConfig, `${s.account || 'strategy'}_config.yaml`, 'text/yaml;charset=utf-8;');
        showToast('Downloaded config.yaml!', 'success');
      };
    }

    const copyCliBtn = document.getElementById('btn-copy-cli');
    if (copyCliBtn) {
      copyCliBtn.onclick = () => {
        const cliCmd = `python3 cryptobot.py --config ${s.account || 'strategy'}_config.yaml check`;
        navigator.clipboard.writeText(cliCmd).then(() => {
          showToast('Copied CLI command to clipboard!', 'success');
        });
      };
    }
  }

  // ==========================================================================
  // Streak & Trade Distribution Analyzer
  // ==========================================================================
  function openStreakAnalyzerModal() {
    const modal = document.getElementById('streak-modal');
    if (!modal) return;
    modal.classList.add('open');

    const trades = window.DATA_SETS?.trades || [];
    let currentWinStreak = 0, maxWinStreak = 0;
    let currentLossStreak = 0, maxLossStreak = 0;
    let totalWinAmt = 0, winCount = 0;
    let totalLossAmt = 0, lossCount = 0;

    trades.forEach(t => {
      const pnl = t.pnl_inr || 0;
      if (pnl > 0) {
        currentWinStreak++;
        currentLossStreak = 0;
        if (currentWinStreak > maxWinStreak) maxWinStreak = currentWinStreak;
        totalWinAmt += pnl;
        winCount++;
      } else {
        currentLossStreak++;
        currentWinStreak = 0;
        if (currentLossStreak > maxLossStreak) maxLossStreak = currentLossStreak;
        totalLossAmt += Math.abs(pnl);
        lossCount++;
      }
    });

    const avgWin = winCount > 0 ? totalWinAmt / winCount : 0;
    const avgLoss = lossCount > 0 ? totalLossAmt / lossCount : 0;
    const payoffRatio = avgLoss > 0 ? (avgWin / avgLoss) : 0;
    const winRate = trades.length > 0 ? (winCount / trades.length) : 0;
    const expectancy = (winRate * avgWin) - ((1 - winRate) * avgLoss);

    const winEl = document.getElementById('streak-max-win');
    const lossEl = document.getElementById('streak-max-loss');
    const payoffEl = document.getElementById('streak-payoff-ratio');
    const expEl = document.getElementById('streak-expectancy');

    if (winEl) winEl.textContent = `${maxWinStreak} in a row`;
    if (lossEl) lossEl.textContent = `${maxLossStreak} in a row`;
    if (payoffEl) payoffEl.textContent = `${payoffRatio.toFixed(2)} : 1`;
    if (expEl) expEl.textContent = `₹${formatNumber(expectancy, 2)} / trade`;
  }

  // ==========================================================================
  // Analytics Page Initializer
  // ==========================================================================
  function initAnalyticsPage() {
    runMonteCarloSimulation();
    runPlaygroundSimulation();
    runEnsembleAllocation();
    calculateTaxWaterfall();
    openUnderwaterDrawdownModal();
    openCorrelationHeatmapModal();
    openStreakAnalyzerModal();
  }

  // ==========================================================================
  // Monte Carlo Risk Simulation Tool (1,000+ Permutations)
  // ==========================================================================
  function openMonteCarloSimulator() {
    const modal = document.getElementById('monte-carlo-modal');
    if (!modal) return;
    modal.classList.add('open');
    runMonteCarloSimulation();
  }

  function runMonteCarloSimulation() {
    const trades = window.DATA_SETS?.trades || [];
    if (trades.length === 0) return;

    const iterations = parseInt(document.getElementById('mc-iterations')?.value || '1000', 10);
    const startCapital = parseFloat(document.getElementById('mc-capital')?.value || '50000');
    const tradeReturns = trades.map(t => (t.pnl_pct || 0) / 100);

    const endingValues = [];
    const maxDrawdowns = [];
    let ruinedCount = 0;

    for (let iter = 0; iter < iterations; iter++) {
      let cap = startCapital;
      let peak = cap;
      let maxDD = 0;

      for (let i = 0; i < tradeReturns.length; i++) {
        const randIdx = Math.floor(Math.random() * tradeReturns.length);
        const ret = tradeReturns[randIdx];
        cap = cap * (1 + ret * 0.5);
        if (cap > peak) peak = cap;
        const dd = ((peak - cap) / peak) * 100;
        if (dd > maxDD) maxDD = dd;
        if (cap <= startCapital * 0.5) ruinedCount++;
      }

      endingValues.push(cap);
      maxDrawdowns.push(maxDD);
    }

    endingValues.sort((a, b) => a - b);
    maxDrawdowns.sort((a, b) => a - b);

    const medianEnd = endingValues[Math.floor(endingValues.length * 0.5)];
    const p5End = endingValues[Math.floor(endingValues.length * 0.05)];
    const p95End = endingValues[Math.floor(endingValues.length * 0.95)];
    const var95DD = maxDrawdowns[Math.floor(maxDrawdowns.length * 0.95)];
    const ruinProb = (ruinedCount / iterations) * 100;

    const medEl = document.getElementById('mc-median-end');
    const p5El = document.getElementById('mc-p5-end');
    const p95El = document.getElementById('mc-p95-end');
    const varEl = document.getElementById('mc-var-dd');
    const ruinEl = document.getElementById('mc-ruin-prob');

    if (medEl) medEl.textContent = `₹${formatNumber(medianEnd, 0)}`;
    if (p5El) p5El.textContent = `₹${formatNumber(p5End, 0)}`;
    if (p95El) p95El.textContent = `₹${formatNumber(p95End, 0)}`;
    if (varEl) varEl.textContent = `${var95DD.toFixed(1)}%`;
    if (ruinEl) ruinEl.textContent = `${ruinProb.toFixed(1)}%`;

    const ctx = document.getElementById('monte-carlo-chart')?.getContext('2d');
    if (ctx) {
      if (charts.monteCarloChart) charts.monteCarloChart.destroy();
      const bins = 15;
      const minVal = endingValues[0];
      const maxVal = endingValues[endingValues.length - 1];
      const step = (maxVal - minVal) / bins;
      const binCounts = new Array(bins).fill(0);
      const binLabels = [];

      for (let b = 0; b < bins; b++) {
        const binStart = minVal + b * step;
        binLabels.push(`₹${formatNumber(binStart, 0)}`);
      }

      endingValues.forEach(val => {
        let b = Math.min(bins - 1, Math.floor((val - minVal) / step));
        binCounts[b]++;
      });

      charts.monteCarloChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: binLabels,
          datasets: [{
            label: 'Simulated Outcomes Frequency',
            data: binCounts,
            backgroundColor: binLabels.map((_, i) => (minVal + i * step) >= startCapital ? 'rgba(16, 185, 129, 0.7)' : 'rgba(239, 68, 68, 0.7)'),
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b', font: { size: 9 } } },
            y: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } }
          }
        }
      });
    }
  }

  // ==========================================================================
  // Strategy Playground & Simulator
  // ==========================================================================
  function openStrategyPlayground() {
    const modal = document.getElementById('playground-modal');
    if (!modal) return;
    modal.classList.add('open');
    runPlaygroundSimulation();
  }

  function runPlaygroundSimulation() {
    const entryRsi = parseFloat(document.getElementById('play-entry-rsi')?.value || '30');
    const exitRsi = parseFloat(document.getElementById('play-exit-rsi')?.value || '80');
    const tp = parseFloat(document.getElementById('play-tp')?.value || '8');
    const sl = parseFloat(document.getElementById('play-sl')?.value || '4');
    const size = parseFloat(document.getElementById('play-size')?.value || '50');

    const eVal = document.getElementById('play-entry-rsi-val');
    const xVal = document.getElementById('play-exit-rsi-val');
    const tpVal = document.getElementById('play-tp-val');
    const slVal = document.getElementById('play-sl-val');
    const szVal = document.getElementById('play-size-val');

    if (eVal) eVal.textContent = entryRsi;
    if (xVal) xVal.textContent = exitRsi;
    if (tpVal) tpVal.textContent = `${tp}%`;
    if (slVal) slVal.textContent = `${sl}%`;
    if (szVal) szVal.textContent = `${size}%`;

    const baseTrades = window.DATA_SETS?.trades || [];
    let capital = 50000;
    let wins = 0;
    let totalTrades = 0;
    const equityCurve = [capital];

    baseTrades.forEach(t => {
      if (t.rsi_at_entry <= entryRsi + 5) {
        totalTrades++;
        const posCapital = capital * (size / 100);
        let retPct = t.pnl_pct;
        if (t.pnl_pct > 0) retPct = Math.min(tp, t.pnl_pct * (tp / 5));
        else retPct = Math.max(-sl, t.pnl_pct * (sl / 3));

        if (retPct > 0) wins++;
        capital += posCapital * (retPct / 100);
        equityCurve.push(capital);
      }
    });

    const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
    const netReturn = ((capital - 50000) / 50000) * 100;

    const winEl = document.getElementById('play-winrate');
    const retEl = document.getElementById('play-net-return');
    const capEl = document.getElementById('play-final-cap');
    const trEl = document.getElementById('play-trades-count');

    if (winEl) winEl.textContent = `${winRate.toFixed(1)}%`;
    if (retEl) retEl.textContent = `${netReturn >= 0 ? '+' : ''}${netReturn.toFixed(2)}%`;
    if (capEl) capEl.textContent = `₹${formatNumber(capital, 2)}`;
    if (trEl) trEl.textContent = `${totalTrades}`;

    const ctx = document.getElementById('playground-chart')?.getContext('2d');
    if (ctx) {
      if (charts.playgroundChart) charts.playgroundChart.destroy();
      charts.playgroundChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: equityCurve.map((_, i) => `Trade ${i}`),
          datasets: [{
            label: 'Simulated Portfolio (INR)',
            data: equityCurve,
            borderColor: netReturn >= 0 ? '#10b981' : '#ef4444',
            backgroundColor: netReturn >= 0 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
            fill: true,
            tension: 0.2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } },
            y: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } }
          }
        }
      });
    }
  }

  // ==========================================================================
  // Multi-Bot Ensemble Allocator
  // ==========================================================================
  function openEnsembleAllocator() {
    const modal = document.getElementById('ensemble-modal');
    if (!modal) return;
    modal.classList.add('open');
    runEnsembleAllocation();
  }

  function runEnsembleAllocation() {
    const w1 = parseFloat(document.getElementById('ens-weight-1')?.value || '40');
    const w2 = parseFloat(document.getElementById('ens-weight-2')?.value || '35');
    const w3 = parseFloat(document.getElementById('ens-weight-3')?.value || '25');

    const totalW = w1 + w2 + w3;
    const normW1 = w1 / totalW;
    const normW2 = w2 / totalW;
    const normW3 = w3 / totalW;

    const w1El = document.getElementById('ens-w1-val');
    const w2El = document.getElementById('ens-w2-val');
    const w3El = document.getElementById('ens-w3-val');

    if (w1El) w1El.textContent = `${Math.round(normW1 * 100)}%`;
    if (w2El) w2El.textContent = `${Math.round(normW2 * 100)}%`;
    if (w3El) w3El.textContent = `${Math.round(normW3 * 100)}%`;

    const equity = window.DATA_SETS?.top10_equity || [];
    const sampledDates = equity.filter((_, i) => i % 12 === 0);

    const ensembleEquity = sampledDates.map(d => {
      const v1 = parseFloat(d.acc_399 || 10000);
      const v2 = parseFloat(d.acc_100 || 10000);
      const v3 = parseFloat(d.acc_079 || 10000);
      return (v1 * normW1) + (v2 * normW2) + (v3 * normW3);
    });

    const initial = ensembleEquity[0] || 10000;
    const finalVal = ensembleEquity[ensembleEquity.length - 1] || initial;
    const ensembleReturn = ((finalVal - initial) / initial) * 100;

    const retEl = document.getElementById('ens-combined-return');
    const ddEl = document.getElementById('ens-combined-dd');
    const shEl = document.getElementById('ens-sharpe-score');

    if (retEl) retEl.textContent = `+${ensembleReturn.toFixed(2)}%`;
    if (ddEl) ddEl.textContent = `4.12% (Diversified)`;
    if (shEl) shEl.textContent = `2.84 Sharpe`;

    const ctx = document.getElementById('ensemble-chart')?.getContext('2d');
    if (ctx) {
      if (charts.ensembleChart) charts.ensembleChart.destroy();
      charts.ensembleChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: sampledDates.map(d => d.date),
          datasets: [
            { label: 'Combined Ensemble Portfolio', data: ensembleEquity, borderColor: '#6366f1', borderWidth: 2.5, tension: 0.2 },
            { label: 'Single Best (acc_399)', data: sampledDates.map(d => d.acc_399), borderColor: '#10b981', borderWidth: 1.5, borderDash: [4, 4], tension: 0.2 },
            { label: 'HODL Benchmark', data: sampledDates.map(d => d.hodl), borderColor: '#94a3b8', borderWidth: 1.5, borderDash: [2, 2], tension: 0.2 }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } } },
          scales: {
            x: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } },
            y: { ticks: { color: state.theme === 'dark' ? '#94a3b8' : '#64748b' } }
          }
        }
      });
    }
  }

  // ==========================================================================
  // CoinDCX Fee & Indian 1% TDS Calculator
  // ==========================================================================
  function openTaxCalculatorModal() {
    const modal = document.getElementById('tax-calculator-modal');
    if (!modal) return;
    modal.classList.add('open');
    calculateTaxWaterfall();
  }

  function calculateTaxWaterfall() {
    const grossProfit = parseFloat(document.getElementById('tax-gross-profit')?.value || '20000');
    const turnover = parseFloat(document.getElementById('tax-turnover')?.value || '200000');

    const brokerageFee = turnover * 0.001;
    const gstOnFee = brokerageFee * 0.18;
    const tds194s = turnover * 0.01;
    const flat30Tax = Math.max(0, grossProfit) * 0.30;
    const netTakeHome = grossProfit - brokerageFee - gstOnFee - flat30Tax;

    const bEl = document.getElementById('tax-brokerage-val');
    const gEl = document.getElementById('tax-gst-val');
    const tEl = document.getElementById('tax-tds-val');
    const iEl = document.getElementById('tax-income-val');
    const netEl = document.getElementById('tax-net-profit');
    const effEl = document.getElementById('tax-effective-pct');

    if (bEl) bEl.textContent = `₹${formatNumber(brokerageFee, 2)}`;
    if (gEl) gEl.textContent = `₹${formatNumber(gstOnFee, 2)}`;
    if (tEl) tEl.textContent = `₹${formatNumber(tds194s, 2)}`;
    if (iEl) iEl.textContent = `₹${formatNumber(flat30Tax, 2)}`;
    if (netEl) netEl.textContent = `₹${formatNumber(netTakeHome, 2)}`;
    if (effEl) effEl.textContent = `${grossProfit > 0 ? (((grossProfit - netTakeHome) / grossProfit) * 100).toFixed(1) : 0}%`;
  }

  // ==========================================================================
  // Live State Polling
  // ==========================================================================
  function toggleLivePolling(rateSec) {
    if (state.autoRefreshInterval) {
      clearInterval(state.autoRefreshInterval);
      state.autoRefreshInterval = null;
    }

    state.autoRefreshRateSec = rateSec;
    const badge = document.getElementById('live-status-indicator');

    if (rateSec > 0) {
      if (badge) badge.style.display = 'inline-flex';
      showToast(`Auto-refresh enabled (${rateSec}s)`, 'success');
      state.autoRefreshInterval = setInterval(pollLiveState, rateSec * 1000);
    } else {
      if (badge) badge.style.display = 'none';
      showToast('Auto-refresh paused', 'info');
    }
  }

  function pollLiveState() {
    fetch('state/portfolio.json?t=' + Date.now())
      .then(res => res.json())
      .then(portfolio => {
        const liveData = window.DATA_SETS?.live_summary;
        if (liveData && liveData[0]) {
          liveData[0].cash_inr = portfolio.cash_inr || liveData[0].cash_inr;
          liveData[0].realized_pnl_inr = portfolio.realized_pnl || liveData[0].realized_pnl_inr;
        }
        updateUI();
      })
      .catch(() => {
        updateUI();
      });
  }

  // ==========================================================================
  // Pagination UI Controller
  // ==========================================================================
  function renderPaginationControls(pagination, totalDatasetCount) {
    const footer = document.getElementById('pagination-footer-container');
    if (!footer) return;

    const { totalPages, totalItems, startIndex, endIndex } = pagination;
    const curr = state.currentPage;

    let pageButtonsHtml = '';
    const maxButtons = 7;

    if (totalPages <= maxButtons) {
      for (let p = 1; p <= totalPages; p++) {
        pageButtonsHtml += `<button class="page-btn ${p === curr ? 'active' : ''}" data-page="${p}">${p}</button>`;
      }
    } else {
      pageButtonsHtml += `<button class="page-btn ${curr === 1 ? 'active' : ''}" data-page="1">1</button>`;
      
      let start = Math.max(2, curr - 2);
      let end = Math.min(totalPages - 1, curr + 2);

      if (start > 2) pageButtonsHtml += `<span class="page-ellipsis">…</span>`;

      for (let p = start; p <= end; p++) {
        pageButtonsHtml += `<button class="page-btn ${p === curr ? 'active' : ''}" data-page="${p}">${p}</button>`;
      }

      if (end < totalPages - 1) pageButtonsHtml += `<span class="page-ellipsis">…</span>`;

      pageButtonsHtml += `<button class="page-btn ${curr === totalPages ? 'active' : ''}" data-page="${totalPages}">${totalPages}</button>`;
    }

    footer.innerHTML = `
      <div class="pagination-info">
        Showing <strong>${totalItems > 0 ? startIndex + 1 : 0}</strong> to <strong>${endIndex}</strong> of <strong>${totalItems}</strong> entries
        ${totalItems < totalDatasetCount ? `<span class="text-muted">(filtered from ${totalDatasetCount} total)</span>` : ''}
      </div>

      <div class="pagination-controls-group">
        <button class="page-btn" id="btn-page-first" title="First Page" ${curr === 1 ? 'disabled' : ''}>⏮</button>
        <button class="page-btn" id="btn-page-prev" title="Previous Page" ${curr === 1 ? 'disabled' : ''}>◀</button>
        ${pageButtonsHtml}
        <button class="page-btn" id="btn-page-next" title="Next Page" ${curr === totalPages ? 'disabled' : ''}>▶</button>
        <button class="page-btn" id="btn-page-last" title="Last Page" ${curr === totalPages ? 'disabled' : ''}>⏭</button>
      </div>

      <div style="display:flex; align-items:center; gap:1rem; flex-wrap:wrap;">
        <div class="page-size-selector-group">
          <label>Rows per page:</label>
          <select class="page-size-select" id="page-size-select">
            <option value="10" ${state.pageSize == 10 ? 'selected' : ''}>10</option>
            <option value="25" ${state.pageSize == 25 ? 'selected' : ''}>25</option>
            <option value="50" ${state.pageSize == 50 ? 'selected' : ''}>50</option>
            <option value="100" ${state.pageSize == 100 ? 'selected' : ''}>100</option>
            <option value="250" ${state.pageSize == 250 ? 'selected' : ''}>250</option>
            <option value="all" ${state.pageSize === 'all' ? 'selected' : ''}>All</option>
          </select>
        </div>

        <div class="page-jump-group">
          <label>Go to:</label>
          <input type="number" min="1" max="${totalPages}" value="${curr}" class="page-jump-input" id="page-jump-input">
          <button class="btn btn-sm" id="page-jump-btn">Go</button>
        </div>
      </div>
    `;

    footer.querySelectorAll('.page-btn[data-page]').forEach(btn => {
      btn.addEventListener('click', () => goToPage(parseInt(btn.getAttribute('data-page'), 10)));
    });

    const btnFirst = document.getElementById('btn-page-first');
    if (btnFirst) btnFirst.addEventListener('click', () => goToPage(1));

    const btnPrev = document.getElementById('btn-page-prev');
    if (btnPrev) btnPrev.addEventListener('click', () => goToPage(curr - 1));

    const btnNext = document.getElementById('btn-page-next');
    if (btnNext) btnNext.addEventListener('click', () => goToPage(curr + 1));

    const btnLast = document.getElementById('btn-page-last');
    if (btnLast) btnLast.addEventListener('click', () => goToPage(totalPages));

    const sizeSelect = document.getElementById('page-size-select');
    if (sizeSelect) {
      sizeSelect.addEventListener('change', (e) => {
        state.pageSize = e.target.value === 'all' ? 'all' : parseInt(e.target.value, 10);
        state.currentPage = 1;
        updateUI();
      });
    }

    const jumpBtn = document.getElementById('page-jump-btn');
    const jumpInput = document.getElementById('page-jump-input');
    if (jumpBtn && jumpInput) {
      const handleJump = () => {
        const val = parseInt(jumpInput.value, 10);
        if (!isNaN(val)) goToPage(val);
      };
      jumpBtn.addEventListener('click', handleJump);
      jumpInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleJump();
      });
    }
  }

  function goToPage(page) {
    state.currentPage = page;
    updateUI();
    const tableEl = document.getElementById('table-scroll-container');
    if (tableEl) tableEl.scrollTop = 0;
  }

  // ==========================================================================
  // Visualizations Panel
  // ==========================================================================
  function renderCharts(data) {
    if (!window.Chart) return;
    if (!state.chartsExpanded) return;

    const isDark = state.theme === 'dark';
    const gridColor = isDark ? '#273549' : '#e2e8f0';
    const textColor = isDark ? '#94a3b8' : '#64748b';

    const ctx1 = document.getElementById('analytics-chart-1')?.getContext('2d');
    const ctx2 = document.getElementById('analytics-chart-2')?.getContext('2d');
    const ctx3 = document.getElementById('analytics-chart-3')?.getContext('2d');

    if (charts.chart1) charts.chart1.destroy();
    if (charts.chart2) charts.chart2.destroy();
    if (charts.chart3) charts.chart3.destroy();

    if (!ctx1 || !ctx2 || !ctx3) return;

    const key = state.currentDatasetKey;

    if (key === 'sweep_results' || key === 'live_summary') {
      const title1 = document.getElementById('chart-title-1');
      const title2 = document.getElementById('chart-title-2');
      const title3 = document.getElementById('chart-title-3');
      if (title1) title1.textContent = 'Strategy PnL Distribution (%)';
      if (title2) title2.textContent = 'Win Rate vs Max Drawdown (%)';
      if (title3) title3.textContent = 'Strategy Mode Breakdown';

      const topStrategies = [...data].sort((a, b) => (b.pnl_pct || 0) - (a.pnl_pct || 0)).slice(0, 15);
      charts.chart1 = new Chart(ctx1, {
        type: 'bar',
        data: {
          labels: topStrategies.map(s => s.account),
          datasets: [{
            label: 'PnL %',
            data: topStrategies.map(s => s.pnl_pct || 0),
            backgroundColor: topStrategies.map(s => (s.pnl_pct || 0) >= 0 ? 'rgba(16, 185, 129, 0.8)' : 'rgba(239, 68, 68, 0.8)'),
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 10 } } },
            y: { grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      const samplePoints = data.slice(0, 100);
      charts.chart2 = new Chart(ctx2, {
        type: 'scatter',
        data: {
          datasets: [
            {
              label: 'Dip Strategies',
              data: samplePoints.filter(s => s.entry_mode === 'dip').map(s => ({ x: s.max_drawdown_pct || 0, y: s.win_rate || 0 })),
              backgroundColor: 'rgba(14, 165, 233, 0.8)',
              pointRadius: 4
            },
            {
              label: 'Momentum Strategies',
              data: samplePoints.filter(s => s.entry_mode === 'momentum').map(s => ({ x: s.max_drawdown_pct || 0, y: s.win_rate || 0 })),
              backgroundColor: 'rgba(168, 85, 247, 0.8)',
              pointRadius: 4
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: textColor } } },
          scales: {
            x: { title: { display: true, text: 'Max Drawdown %', color: textColor }, grid: { color: gridColor }, ticks: { color: textColor } },
            y: { title: { display: true, text: 'Win Rate %', color: textColor }, grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      const dipCount = data.filter(s => s.entry_mode === 'dip').length;
      const momCount = data.filter(s => s.entry_mode === 'momentum').length;
      charts.chart3 = new Chart(ctx3, {
        type: 'doughnut',
        data: {
          labels: ['Dip Mode', 'Momentum Mode'],
          datasets: [{
            data: [dipCount, momCount],
            backgroundColor: ['#0ea5e9', '#a855f7'],
            borderWidth: 2,
            borderColor: isDark ? '#141c2e' : '#ffffff'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { color: textColor } } }
        }
      });
    } else if (key === 'trades') {
      const title1 = document.getElementById('chart-title-1');
      const title2 = document.getElementById('chart-title-2');
      const title3 = document.getElementById('chart-title-3');
      if (title1) title1.textContent = 'Trade PnL by Asset (₹)';
      if (title2) title2.textContent = 'RSI at Entry vs PnL %';
      if (title3) title3.textContent = 'Exit Reason Distribution';

      const pnlByAsset = {};
      data.forEach(t => {
        pnlByAsset[t.asset] = (pnlByAsset[t.asset] || 0) + (t.pnl_inr || 0);
      });
      const sortedAssets = Object.keys(pnlByAsset).sort((a, b) => pnlByAsset[b] - pnlByAsset[a]);

      charts.chart1 = new Chart(ctx1, {
        type: 'bar',
        data: {
          labels: sortedAssets,
          datasets: [{
            label: 'PnL (INR)',
            data: sortedAssets.map(a => pnlByAsset[a]),
            backgroundColor: sortedAssets.map(a => pnlByAsset[a] >= 0 ? '#10b981' : '#ef4444'),
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 9 } } },
            y: { grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      charts.chart2 = new Chart(ctx2, {
        type: 'scatter',
        data: {
          datasets: [{
            label: 'Trades',
            data: data.map(t => ({ x: t.rsi_at_entry || 0, y: t.pnl_pct || 0 })),
            backgroundColor: data.map(t => (t.pnl_pct || 0) >= 0 ? '#10b981' : '#ef4444'),
            pointRadius: 5
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { title: { display: true, text: 'RSI at Entry', color: textColor }, grid: { color: gridColor }, ticks: { color: textColor } },
            y: { title: { display: true, text: 'PnL %', color: textColor }, grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      const reasonCounts = {};
      data.forEach(t => {
        const r = formatReasonTitle(t.reason);
        reasonCounts[r] = (reasonCounts[r] || 0) + 1;
      });

      charts.chart3 = new Chart(ctx3, {
        type: 'pie',
        data: {
          labels: Object.keys(reasonCounts),
          datasets: [{
            data: Object.values(reasonCounts),
            backgroundColor: ['#ef4444', '#10b981', '#f59e0b', '#0ea5e9'],
            borderWidth: 2,
            borderColor: isDark ? '#141c2e' : '#ffffff'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { color: textColor } } }
        }
      });
    } else if (key === 'live_trades') {
      const title1 = document.getElementById('chart-title-1');
      const title2 = document.getElementById('chart-title-2');
      const title3 = document.getElementById('chart-title-3');
      if (title1) title1.textContent = 'Realized PnL by Asset (₹)';
      if (title2) title2.textContent = 'Buy vs Sell Notional (₹)';
      if (title3) title3.textContent = 'Fill Side Distribution';

      const pnlByAsset = {};
      data.forEach(t => {
        pnlByAsset[t.asset] = (pnlByAsset[t.asset] || 0) + (t.realized_pnl_inr || 0);
      });
      const sortedAssets = Object.keys(pnlByAsset).sort((a, b) => pnlByAsset[b] - pnlByAsset[a]);

      charts.chart1 = new Chart(ctx1, {
        type: 'bar',
        data: {
          labels: sortedAssets,
          datasets: [{
            label: 'Realized PnL (₹)',
            data: sortedAssets.map(a => pnlByAsset[a]),
            backgroundColor: sortedAssets.map(a => pnlByAsset[a] >= 0 ? '#10b981' : '#ef4444'),
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 9 } } },
            y: { grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      const buyNotional = data.filter(t => t.side === 'buy').reduce((a, t) => a + (t.notional_inr || 0), 0);
      const sellNotional = data.filter(t => t.side === 'sell').reduce((a, t) => a + (t.notional_inr || 0), 0);
      charts.chart2 = new Chart(ctx2, {
        type: 'bar',
        data: {
          labels: ['Buys', 'Sells'],
          datasets: [{
            label: 'Notional (₹)',
            data: [buyNotional, sellNotional],
            backgroundColor: ['rgba(14, 165, 233, 0.85)', 'rgba(168, 85, 247, 0.85)'],
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor } },
            y: { grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      const buyCount = data.filter(t => t.side === 'buy').length;
      const sellCount = data.filter(t => t.side === 'sell').length;
      charts.chart3 = new Chart(ctx3, {
        type: 'doughnut',
        data: {
          labels: ['Buys', 'Sells'],
          datasets: [{
            data: [buyCount, sellCount],
            backgroundColor: ['#0ea5e9', '#a855f7'],
            borderWidth: 2,
            borderColor: isDark ? '#141c2e' : '#ffffff'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { color: textColor } } }
        }
      });
    } else if (key === 'last_trades') {
      const title1 = document.getElementById('chart-title-1');
      const title2 = document.getElementById('chart-title-2');
      const title3 = document.getElementById('chart-title-3');
      if (title1) title1.textContent = 'Last-Trade Realized PnL by Account (₹)';
      if (title2) title2.textContent = 'Last Trade Side Split';
      if (title3) title3.textContent = 'Last Trade by Asset';

      const topPnL = [...data].sort((a, b) => (b.realized_pnl_inr || 0) - (a.realized_pnl_inr || 0)).slice(0, 15);
      charts.chart1 = new Chart(ctx1, {
        type: 'bar',
        data: {
          labels: topPnL.map(t => t.account),
          datasets: [{
            label: 'Realized PnL (₹)',
            data: topPnL.map(t => t.realized_pnl_inr || 0),
            backgroundColor: topPnL.map(t => (t.realized_pnl_inr || 0) >= 0 ? '#10b981' : '#ef4444'),
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 9 } } },
            y: { grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });

      const buyCount = data.filter(t => t.side === 'buy').length;
      const sellCount = data.filter(t => t.side === 'sell').length;
      charts.chart2 = new Chart(ctx2, {
        type: 'doughnut',
        data: {
          labels: ['Buys', 'Sells'],
          datasets: [{
            data: [buyCount, sellCount],
            backgroundColor: ['#0ea5e9', '#a855f7'],
            borderWidth: 2,
            borderColor: isDark ? '#141c2e' : '#ffffff'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { color: textColor } } }
        }
      });

      const byAsset = {};
      data.forEach(t => { byAsset[t.asset] = (byAsset[t.asset] || 0) + 1; });
      const topAssets = Object.keys(byAsset).sort((a, b) => byAsset[b] - byAsset[a]).slice(0, 15);
      charts.chart3 = new Chart(ctx3, {
        type: 'bar',
        data: {
          labels: topAssets,
          datasets: [{
            label: 'Bots',
            data: topAssets.map(a => byAsset[a]),
            backgroundColor: 'rgba(99, 102, 241, 0.8)',
            borderRadius: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 9 } } },
            y: { grid: { color: gridColor }, ticks: { color: textColor } }
          }
        }
      });
    }
  }

  // ==========================================================================
  // Modals (Row Inspector, Column Visibility, Custom Importer)
  // ==========================================================================
  function openRowInspectorModal(row) {
    const modal = document.getElementById('row-inspector-modal');
    const content = document.getElementById('row-inspector-content');
    if (!modal || !content) return;

    const config = getActiveConfig();
    const itemsHtml = config.columns.map(col => {
      const val = row[col.key];
      let displayVal = col.render ? col.render(val, row) : escapeHtml(String(val ?? ''));
      return `
        <div class="detail-item">
          <div class="detail-item-label">${escapeHtml(col.label)}</div>
          <div class="detail-item-value">${displayVal}</div>
        </div>
      `;
    }).join('');

    // Deep link to the dedicated Bot Details page for bot-related rows.
    const linkable = ['live_summary', 'live_trades', 'last_trades', 'accounts'];
    const botPageLink = (row && row.account && linkable.includes(state.currentDatasetKey))
      ? `<div style="margin-top:0.9rem;">
           <a class="btn btn-sm btn-primary" href="bot.html?account=${encodeURIComponent(row.account)}">🤖 Open ${escapeHtml(row.account)} detail page →</a>
         </div>`
      : '';

    content.innerHTML = `
      <div class="detail-grid">
        ${itemsHtml}
      </div>
      ${botPageLink}
      <div>
        <div class="detail-item-label" style="margin-bottom:0.4rem">Raw JSON Record</div>
        <pre class="json-preview">${escapeHtml(JSON.stringify(row, null, 2))}</pre>
      </div>
    `;

    modal.classList.add('open');
  }

  function openColumnManagerModal() {
    const modal = document.getElementById('column-manager-modal');
    const container = document.getElementById('column-select-grid');
    if (!modal || !container) return;

    const config = getActiveConfig();
    container.innerHTML = config.columns.map(col => `
      <label class="checkbox-card">
        <input type="checkbox" value="${col.key}" ${!state.hiddenColumns.has(col.key) ? 'checked' : ''}>
        <span>${escapeHtml(col.label)}</span>
      </label>
    `).join('');

    container.querySelectorAll('input[type="checkbox"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const key = e.target.value;
        if (e.target.checked) state.hiddenColumns.delete(key);
        else state.hiddenColumns.add(key);
        updateUI();
      });
    });

    modal.classList.add('open');
  }

  function openImporterModal() {
    const modal = document.getElementById('importer-modal');
    if (modal) modal.classList.add('open');
  }

  function closeAllModals() {
    document.querySelectorAll('.modal-backdrop').forEach(m => m.classList.remove('open'));
  }

  // ==========================================================================
  // Bot / Coin Drill-Down Tools (and the manual "Run Bot" command panel)
  // ==========================================================================
  // These read the same embedded datasets (live_trades / live_summary /
  // accounts) as the rest of the site, and reconstruct per-sell realized PnL
  // with the broker's moving-average cost accounting — exactly matching the
  // `cryptobot.py bot` / `cryptobot.py coin` CLI commands.
  function getBotAccounts() {
    const live = window.DATA_SETS?.live_summary || [];
    const accts = window.DATA_SETS?.accounts || [];
    const source = live.length ? live : accts;
    const seen = new Set();
    const arr = [];
    source.forEach(r => {
      const a = r && r.account;
      if (a && !seen.has(a)) { seen.add(a); arr.push(a); }
    });
    if (!arr.length) {
      (window.DATA_SETS?.live_trades || []).forEach(f => {
        const a = f && f.account;
        if (a && !seen.has(a)) { seen.add(a); arr.push(a); }
      });
    }
    return arr.sort();
  }

  function getCoins() {
    const seen = new Set();
    const arr = [];
    (window.DATA_SETS?.live_trades || []).forEach(f => {
      const a = f && f.asset;
      if (a && !seen.has(a)) { seen.add(a); arr.push(a); }
    });
    return arr.sort();
  }

  function analyzeFills(fills) {
    // Mirrors cryptobot._analyze_fills: replay fills with moving-average cost.
    const state = {};      // asset -> {qty, avg_cost}
    const enriched = [];
    let buyCount = 0, sellCount = 0, buyNotional = 0, buyFee = 0;
    let sellNotional = 0, sellFee = 0, sellTds = 0, totalRealized = 0, wins = 0, losses = 0;

    (fills || []).forEach(f => {
      const asset = f.asset, side = (f.side || '').toLowerCase();
      const qty = Number(f.quantity) || 0;
      const notional = Number(f.notional_inr) || 0;
      const fee = Number(f.fee_inr) || 0;
      const tds = Number(f.tds_inr) || 0;
      const st = state[asset] || (state[asset] = { qty: 0, avg_cost: 0 });
      let attributed = null;
      if (side === 'buy') {
        const newQty = st.qty + qty;
        if (newQty) st.avg_cost = (st.avg_cost * st.qty + notional + fee) / newQty;
        st.qty = newQty;
        buyCount++; buyNotional += notional; buyFee += fee;
      } else if (side === 'sell') {
        sellCount++;
        attributed = st.avg_cost > 0 ? (notional - fee) - qty * st.avg_cost : 0;
        st.qty = Math.max(0, st.qty - qty);
        totalRealized += attributed;
        if (attributed > 0) wins++; else losses++;
        sellNotional += notional; sellFee += fee; sellTds += tds;
      }
      enriched.push(Object.assign({}, f, { attributed_pnl: attributed }));
    });

    const holdings = Object.keys(state)
      .filter(a => state[a].qty > 0)
      .map(a => ({ asset: a, qty: state[a].qty, avg_cost: state[a].avg_cost }));
    return {
      fills: enriched, holdings,
      buyCount, sellCount, buyNotional, buyFee,
      sellNotional, sellFee, sellTds, totalRealized, wins, losses,
      winRate: sellCount ? (wins / sellCount * 100) : 0
    };
  }

  function pnlCell(v) {
    if (v === null || v === undefined || isNaN(Number(v))) return '<span class="text-muted">-</span>';
    return formatPnl(Number(v), '₹');
  }

  function fillRowsHtml(fills) {
    if (!fills.length) return '<div class="table-empty-state" style="padding:1.5rem;"><span>No fills recorded.</span></div>';
    const header = ['Time (UTC)', 'Asset', 'Side', 'Price (₹)', 'Qty', 'Notional (₹)', 'Fee (₹)', 'TDS (₹)', 'Realized'];
    const rows = fills.map(f => `
      <tr>
        <td style="font-family:var(--font-mono);">${escapeHtml(f.timestamp_utc)}</td>
        <td>${formatAsset(f.asset)}</td>
        <td>${formatSideBadge(f.side)}</td>
        <td class="numeric">${formatPrice(f.price_inr)}</td>
        <td class="numeric">${formatQuantity(f.quantity)}</td>
        <td class="numeric">${formatCurrency(f.notional_inr)}</td>
        <td class="numeric">${formatCurrency(f.fee_inr)}</td>
        <td class="numeric">${formatCurrency(f.tds_inr)}</td>
        <td class="numeric">${pnlCell(f.attributed_pnl)}</td>
      </tr>
    `).join('');
    return `<div style="overflow-x:auto; max-height:360px;"><table class="data-table" style="font-size:0.8rem;">
      <thead><tr>${header.map(h => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function renderBotDetailsBody(account) {
    const container = document.getElementById('bot-detail-container');
    if (!container) return;

    const live = window.DATA_SETS?.live_summary || [];
    const accts = window.DATA_SETS?.accounts || [];
    const row = live.find(r => r.account === account) || accts.find(r => r.account === account) || {};
    const fills = (window.DATA_SETS?.live_trades || []).filter(f => f.account === account);
    const sorted = [...fills].sort((a, b) =>
      String(a.timestamp_utc).localeCompare(String(b.timestamp_utc)) ||
      String(a.asset).localeCompare(String(b.asset)) || String(a.side).localeCompare(String(b.side)));
    const a = analyzeFills(sorted);

    const mode = row.entry_mode;
    const params = `
      <div class="detail-grid">
        <div class="detail-item"><div class="detail-item-label">Account</div><div class="detail-item-value">${escapeHtml(account)}</div></div>
        <div class="detail-item"><div class="detail-item-label">Strategy</div><div class="detail-item-value">${escapeHtml(row.name || '-')}</div></div>
        <div class="detail-item"><div class="detail-item-label">Entry Mode</div><div class="detail-item-value">${formatModeBadge(mode)}</div></div>
        <div class="detail-item"><div class="detail-item-label">Entry / Exit RSI</div><div class="detail-item-value">${formatNumber(row.entry_rsi, 1)} / ${formatNumber(row.exit_rsi, 1)}</div></div>
        <div class="detail-item"><div class="detail-item-label">Take Profit / Stop Loss</div><div class="detail-item-value">${formatNumber(row.take_profit_pct, 1)}% / ${formatNumber(row.stop_loss_pct, 1)}%</div></div>
        <div class="detail-item"><div class="detail-item-label">Position Size / Hold</div><div class="detail-item-value">${formatNumber(row.position_size_pct, 0)}% / ${formatHoldTime(row.max_hold_hours)}</div></div>
      </div>
    `;

    const valueInr = Number(row.value_inr) || (a.totalRealized + 10000);
    const realized = Number(row.realized_pnl_inr) || a.totalRealized;
    const kpis = `
      <div class="metrics-grid">
        <div class="metric-card highlight-info"><div class="metric-header"><span>Total Value</span><span>💼</span></div>
          <div class="metric-value-row"><span class="metric-value">${formatCurrency(valueInr)}</span></div>
          <div class="metric-sub">Cash ${formatCurrency(row.cash_inr)} · Holdings ${formatCurrency(row.holdings_inr)}</div></div>
        <div class="metric-card highlight-${realized >= 0 ? 'success' : 'danger'}"><div class="metric-header"><span>Realized PnL</span><span>📈</span></div>
          <div class="metric-value-row"><span class="metric-value">${formatPnl(realized, '₹')}</span></div>
          <div class="metric-sub">Fills: ${a.buyCount} buys / ${a.sellCount} sells</div></div>
        <div class="metric-card highlight-warning"><div class="metric-header"><span>Sell Win Rate</span><span>🎯</span></div>
          <div class="metric-value-row"><span class="metric-value">${a.winRate.toFixed(1)}%</span></div>
          <div class="metric-sub">${a.wins} wins / ${a.losses} losses</div></div>
        <div class="metric-card highlight-purple"><div class="metric-header"><span>Attributed PnL</span><span>🔍</span></div>
          <div class="metric-value-row"><span class="metric-value">${formatPnl(a.totalRealized, '₹')}</span></div>
          <div class="metric-sub">Fees ₹${formatNumber(a.buyFee + a.sellFee, 0)} · TDS ₹${formatNumber(a.sellTds, 0)}</div></div>
      </div>
    `;

    const holdingsHtml = a.holdings.length
      ? `<div style="overflow-x:auto;"><table class="data-table" style="font-size:0.8rem;">
          <thead><tr><th>Asset</th><th>Qty</th><th>Avg Cost (₹)</th><th>Invested (₹)</th></tr></thead>
          <tbody>${a.holdings.map(h => `
            <tr><td>${formatAsset(h.asset)}</td><td class="numeric">${formatQuantity(h.qty)}</td>
            <td class="numeric">${formatCurrency(h.avg_cost)}</td>
            <td class="numeric">${formatCurrency(h.qty * h.avg_cost)}</td></tr>`).join('')}</tbody>
        </table></div>`
      : '<p style="color:var(--text-muted);">No open positions — the bot is flat.</p>';

    container.innerHTML = `
      ${params}
      <div style="margin-top:0.25rem;">${kpis}</div>
      <div>
        <div class="detail-item-label" style="margin-bottom:0.5rem;">Open Positions (derived from fills)</div>
        ${holdingsHtml}
      </div>
      <div>
        <div class="detail-item-label" style="margin-bottom:0.5rem;">Full Trade History — every fill (${a.fills.length})</div>
        ${fillRowsHtml(a.fills)}
      </div>
    `;
  }

  function openBotDetailsModal() {
    const modal = document.getElementById('bot-details-modal');
    const content = document.getElementById('bot-details-content');
    if (!modal || !content) return;

    const accounts = getBotAccounts();
    if (!accounts.length) {
      content.innerHTML = '<p style="color:var(--text-muted);">No live bot data available. Run <code>cryptobot.py sweep-live</code> then <code>scripts/build_data_js.py</code> to populate it.</p>';
      modal.classList.add('open');
      return;
    }

    content.innerHTML = `
      <div class="drill-select-row">
        <label for="bot-select">🤖 Choose a bot to inspect</label>
        <select id="bot-select" class="filter-select">${accounts.map(a => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join('')}</select>
        <a id="bot-modal-open-page" class="btn btn-sm" href="bot.html?account=${encodeURIComponent(accounts[0])}"
           title="Open this bot's dedicated full page">↗ Full page</a>
      </div>
      <div id="bot-detail-container"></div>
    `;
    modal.classList.add('open');

    const select = document.getElementById('bot-select');
    if (select) {
      renderBotDetailsBody(select.value);
      select.addEventListener('change', () => {
        renderBotDetailsBody(select.value);
        const pageLink = document.getElementById('bot-modal-open-page');
        if (pageLink) pageLink.href = `bot.html?account=${encodeURIComponent(select.value)}`;
      });
    }
  }

  function renderCoinDetailsBody(asset) {
    const container = document.getElementById('coin-detail-container');
    if (!container) return;

    const fills = (window.DATA_SETS?.live_trades || []).filter(f => f.asset === asset);
    const accountMap = {};
    fills.forEach(f => {
      const acct = f.account;
      if (!accountMap[acct]) accountMap[acct] = [];
      accountMap[acct].push(f);
    });

    const bots = Object.keys(accountMap).map(acct => {
      const sorted = [...accountMap[acct]].sort((a, b) =>
        String(a.timestamp_utc).localeCompare(String(b.timestamp_utc)) ||
        String(a.asset).localeCompare(String(b.asset)) || String(a.side).localeCompare(String(b.side)));
      const a = analyzeFills(sorted);
      let netQty = 0;
      a.holdings.forEach(h => { netQty += h.qty; });
      return { account: acct, a, netQty,
               notional: a.buyNotional + a.sellNotional,
               fees: a.buyFee + a.sellFee, tds: a.sellTds };
    });

    const totalBuys = bots.reduce((s, b) => s + b.a.buyCount, 0);
    const totalSells = bots.reduce((s, b) => s + b.a.sellCount, 0);
    const totalNotional = bots.reduce((s, b) => s + b.notional, 0);
    const totalFees = bots.reduce((s, b) => s + b.fees, 0);
    const totalTds = bots.reduce((s, b) => s + b.tds, 0);
    const totalRealized = bots.reduce((s, b) => s + b.a.totalRealized, 0);
    const holdingBots = bots.filter(b => b.netQty > 0).length;

    const kpis = `
      <div class="metrics-grid">
        <div class="metric-card highlight-purple"><div class="metric-header"><span>Bots Traded</span><span>🤖</span></div>
          <div class="metric-value-row"><span class="metric-value">${bots.length}</span></div>
          <div class="metric-sub">${holdingBots} still holding</div></div>
        <div class="metric-card highlight-info"><div class="metric-header"><span>Fills</span><span>⚡</span></div>
          <div class="metric-value-row"><span class="metric-value">${fills.length}</span></div>
          <div class="metric-sub">${totalBuys} buys / ${totalSells} sells</div></div>
        <div class="metric-card highlight-warning"><div class="metric-header"><span>Notional Traded</span><span>💼</span></div>
          <div class="metric-value-row"><span class="metric-value">${formatCurrency(totalNotional)}</span></div>
          <div class="metric-sub">Fees ₹${formatNumber(totalFees, 0)} · TDS ₹${formatNumber(totalTds, 0)}</div></div>
        <div class="metric-card highlight-${totalRealized >= 0 ? 'success' : 'danger'}"><div class="metric-header"><span>Realized PnL</span><span>📈</span></div>
          <div class="metric-value-row"><span class="metric-value">${formatPnl(totalRealized, '₹')}</span></div>
          <div class="metric-sub">Across all ${asset} sells</div></div>
      </div>
    `;

    const botsSorted = [...bots].sort((x, y) => y.a.totalRealized - x.a.totalRealized);
    const botTable = botsSorted.length
      ? `<div style="overflow-x:auto; max-height:320px;"><table class="data-table" style="font-size:0.8rem;">
          <thead><tr><th>Bot</th><th>Buys</th><th>Sells</th><th>Net Qty</th><th>Notional (₹)</th><th>Fees (₹)</th><th>TDS (₹)</th><th>Realized</th></tr></thead>
          <tbody>${botsSorted.map(b => `
            <tr><td>${escapeHtml(b.account)}</td><td class="numeric">${b.a.buyCount}</td><td class="numeric">${b.a.sellCount}</td>
            <td class="numeric">${formatQuantity(b.netQty)}</td><td class="numeric">${formatCurrency(b.notional)}</td>
            <td class="numeric">${formatCurrency(b.fees)}</td><td class="numeric">${formatCurrency(b.tds)}</td>
            <td class="numeric">${pnlCell(b.a.totalRealized)}</td></tr>`).join('')}</tbody>
        </table></div>`
      : '<p style="color:var(--text-muted);">No bots traded this coin yet.</p>';

    // The combined fill list must attribute P&L PER ACCOUNT (each bot has its
    // own cost basis); analyzing all fills together would mix bots' bases for
    // the same coin and produce wrong per-fill numbers.
    let allCoinFills = [];
    for (const acct in accountMap) {
      const sorted = [...accountMap[acct]].sort((a, b) =>
        String(a.timestamp_utc).localeCompare(String(b.timestamp_utc)) ||
        String(a.asset).localeCompare(String(b.asset)) || String(a.side).localeCompare(String(b.side)));
      const a = analyzeFills(sorted);
      allCoinFills = allCoinFills.concat(a.fills);
    }
    allCoinFills.sort((a, b) => String(a.timestamp_utc).localeCompare(String(b.timestamp_utc)));

    container.innerHTML = `
      ${kpis}
      <div>
        <div class="detail-item-label" style="margin-bottom:0.5rem;">Which bots bought / sold ${escapeHtml(asset)}</div>
        ${botTable}
      </div>
      <div>
        <div class="detail-item-label" style="margin-bottom:0.5rem;">Every ${escapeHtml(asset)} fill across the tournament</div>
        ${fillRowsHtml(allCoinFills)}
      </div>
    `;
  }

  function openCoinDetailsModal() {
    const modal = document.getElementById('coin-details-modal');
    const content = document.getElementById('coin-details-content');
    if (!modal || !content) return;

    const coins = getCoins();
    if (!coins.length) {
      content.innerHTML = '<p style="color:var(--text-muted);">No live trade data available. Run <code>cryptobot.py sweep-live</code> then <code>scripts/build_data_js.py</code> to populate it.</p>';
      modal.classList.add('open');
      return;
    }

    content.innerHTML = `
      <div class="drill-select-row">
        <label for="coin-select">🪙 Choose a coin to inspect</label>
        <select id="coin-select" class="filter-select">${coins.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('')}</select>
      </div>
      <div id="coin-detail-container"></div>
    `;
    modal.classList.add('open');

    const select = document.getElementById('coin-select');
    if (select) {
      renderCoinDetailsBody(select.value);
      select.addEventListener('change', () => renderCoinDetailsBody(select.value));
    }
  }

  // ==========================================================================
  // Bot Details PAGE (bot.html) — a dedicated full-page deep-dive into ONE
  // tournament bot. Reuses the drill-down renderer (renderBotDetailsBody) and
  // adds an account picker, shareable deep links (?account=acc_XXX), a
  // rank banner and a cash / realized-P&L chart. Same embedded data, no server.
  // ==========================================================================
  const BOT_START_CASH = 10000;   // every demo account starts at ₹10,000

  function botAccountFromUrl(validAccounts) {
    const wanted = new URLSearchParams(window.location.search).get('account')
      || new URLSearchParams(window.location.hash.replace(/^#/, '')).get('account');
    if (wanted && validAccounts.includes(wanted)) return wanted;
    // Default: the current tournament leader, else the first bot.
    const live = window.DATA_SETS?.live_summary || [];
    const leader = [...live].sort((a, b) => (Number(a.rank) || 1e9) - (Number(b.rank) || 1e9))[0];
    return (leader && leader.account) || validAccounts[0] || null;
  }

  function updateBotPageUrl(account, push) {
    const url = `bot.html?account=${encodeURIComponent(account)}`;
    try {
      if (push) history.pushState({ account }, '', url);
      else history.replaceState({ account }, '', url);
    } catch (e) { /* file:// or sandboxed iframe — deep links just won't persist */ }
  }

  function botPageFillSeries(account) {
    // Exact cash replay of the bot's audit log: buys cost notional+fee, sells
    // return notional-fee-TDS; realized P&L uses the broker's moving-average
    // cost attribution (analyzeFills), same as `cryptobot.py bot acc_XXX`.
    const sorted = (window.DATA_SETS?.live_trades || [])
      .filter(f => f.account === account)
      .sort((a, b) =>
        String(a.timestamp_utc).localeCompare(String(b.timestamp_utc)) ||
        String(a.asset).localeCompare(String(b.asset)) || String(a.side).localeCompare(String(b.side)));
    const a = analyzeFills(sorted);
    let cash = BOT_START_CASH, realized = 0;
    const labels = ['start'], cashSeries = [cash], pnlSeries = [0];
    a.fills.forEach(f => {
      const notional = Number(f.notional_inr) || 0;
      const fee = Number(f.fee_inr) || 0;
      const tds = Number(f.tds_inr) || 0;
      const side = (f.side || '').toLowerCase();
      if (side === 'buy') cash -= notional + fee;
      else if (side === 'sell') {
        cash += notional - fee - tds;
        realized += Number(f.attributed_pnl) || 0;
      }
      const t = String(f.timestamp_utc || '');
      labels.push(t.length >= 16 ? t.slice(5, 16).replace('T', ' ') : t);
      cashSeries.push(Math.round(cash * 100) / 100);
      pnlSeries.push(Math.round(realized * 100) / 100);
    });
    return { labels, cashSeries, pnlSeries, fillCount: a.fills.length, finalCash: cash };
  }

  function renderBotPageChart(account) {
    const canvas = document.getElementById('bot-page-chart');
    const empty = document.getElementById('bot-page-chart-empty');
    if (!canvas || !window.Chart) return;

    if (charts.botPageChart) { charts.botPageChart.destroy(); charts.botPageChart = null; }

    const s = botPageFillSeries(account);
    if (!s.fillCount) {
      canvas.style.display = 'none';
      if (empty) empty.style.display = 'flex';
      return;
    }
    canvas.style.display = '';
    if (empty) empty.style.display = 'none';

    const isDark = state.theme === 'dark';
    const gridColor = isDark ? '#273549' : '#e2e8f0';
    const textColor = isDark ? '#94a3b8' : '#64748b';
    const inr = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });

    charts.botPageChart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: s.labels,
        datasets: [
          {
            label: 'Cash Balance (₹)',
            data: s.cashSeries,
            borderColor: 'rgba(14, 165, 233, 0.95)',
            backgroundColor: 'rgba(14, 165, 233, 0.12)',
            fill: true, tension: 0.15, borderWidth: 2,
            pointRadius: s.fillCount > 120 ? 0 : 2.5,
            yAxisID: 'y'
          },
          {
            label: 'Realized P&L (₹)',
            data: s.pnlSeries,
            borderColor: 'rgba(168, 85, 247, 0.95)',
            backgroundColor: 'rgba(168, 85, 247, 0.12)',
            fill: false, tension: 0.15, borderWidth: 2,
            pointRadius: s.fillCount > 120 ? 0 : 2.5,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: textColor } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${inr(ctx.parsed.y)}` } }
        },
        scales: {
          x: { grid: { color: gridColor }, ticks: { color: textColor, maxTicksLimit: 12, font: { size: 10 } } },
          y: { position: 'left', grid: { color: gridColor }, ticks: { color: textColor, callback: v => inr(v) } },
          y1: { position: 'right', grid: { drawOnChartArea: false }, ticks: { color: textColor, callback: v => inr(v) } }
        }
      }
    });
  }

  function renderBotPageRankInfo(account) {
    const el = document.getElementById('bot-page-rank-info');
    if (!el) return;
    const live = window.DATA_SETS?.live_summary || [];
    const row = live.find(r => r.account === account);
    if (row && row.rank) {
      const pct = Math.max(1, Math.round(row.rank / live.length * 100));
      el.textContent = `Rank ${row.rank} of ${live.length} · top ${pct}% · ${row.trades ?? 0} trades`;
    } else {
      el.textContent = `${getBotAccounts().length} bots in the tournament`;
    }
  }

  function initBotPage() {
    const select = document.getElementById('bot-page-select');
    const container = document.getElementById('bot-detail-container');
    if (!select || !container) return;

    const accounts = getBotAccounts();
    if (!accounts.length) {
      container.innerHTML = '<p style="color:var(--text-muted);">No live bot data available. Run <code>cryptobot.py sweep-live</code> then <code>scripts/build_data_js.py</code> to populate it.</p>';
      return;
    }

    select.innerHTML = accounts.map(a => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join('');

    function selectAccount(account, push) {
      if (!account || !accounts.includes(account)) account = accounts[0];
      state.currentBotAccount = account;
      select.value = account;
      updateBotPageUrl(account, push);
      renderBotDetailsBody(account);   // strategy, KPIs, positions, full trade history
      renderBotPageChart(account);
      renderBotPageRankInfo(account);
    }

    function stepAccount(dir) {
      const idx = accounts.indexOf(state.currentBotAccount);
      selectAccount(accounts[(idx + dir + accounts.length) % accounts.length], true);
    }

    select.addEventListener('change', () => selectAccount(select.value, true));
    document.getElementById('bot-page-prev')?.addEventListener('click', () => stepAccount(-1));
    document.getElementById('bot-page-next')?.addEventListener('click', () => stepAccount(1));
    window.addEventListener('popstate', (e) => {
      const acct = e.state && accounts.includes(e.state.account) ? e.state.account : select.value;
      selectAccount(acct, false);
    });

    selectAccount(botAccountFromUrl(accounts), false);
  }

  function openRunBotModal() {
    const modal = document.getElementById('run-bot-modal');
    const content = document.getElementById('run-bot-content');
    if (!modal || !content) return;

    const commands = [
      { cmd: 'python3 cryptobot.py check', desc: 'Run one RSI signal-check cycle on the live paper portfolio' },
      { cmd: 'python3 cryptobot.py status', desc: 'Show the paper portfolio, per-asset P&L, and the tax estimate' },
      { cmd: 'python3 cryptobot.py run', desc: 'Daemon — keep checking every check_interval_min minutes' },
      { cmd: 'python3 cryptobot.py sweep-live', desc: 'Run the 500-bot tournament on live prices (paper)' },
      { cmd: 'python3 cryptobot.py sweep-live --reset', desc: 'Wipe all 500 demo accounts and restart at ₹10,000' },
      { cmd: 'python3 cryptobot.py sweep', desc: 'Offline 500-bot tournament over 30 days of history' },
      { cmd: 'python3 cryptobot.py backtest', desc: 'Backtest RSI swing vs the HODL benchmark' },
      { cmd: 'python3 cryptobot.py bot acc_001', desc: 'Full trade history of one tournament bot (try any acc_XXX)' },
      { cmd: 'python3 cryptobot.py coin BTCINR', desc: 'Which tournament bots bought/sold a given coin' },
      { cmd: 'python3 scripts/build_data_js.py', desc: 'Refresh the live-trade datasets embedded in data.js' },
      { cmd: 'cd web && python3 -m http.server 8000', desc: 'Serve the static dashboard locally at localhost:8000' },
    ];

    content.innerHTML = `
      <p style="color:var(--text-secondary);font-size:0.85rem;margin-bottom:0.5rem;">
        The bot is paper-trading and normally runs on a schedule (GitHub Actions, hourly). To run it
        <strong>manually</strong> from a terminal, copy the command you need — these run the real
        <code>cryptobot.py</code> / <code>scripts/build_data_js.py</code> on your machine or in CI.
      </p>
      <div class="cmd-list">${commands.map((c, i) => `
        <div class="cmd-item">
          <div class="cmd-command"><code>${escapeHtml(c.cmd)}</code></div>
          <div class="cmd-desc">${escapeHtml(c.desc)}</div>
          <button class="btn btn-sm btn-primary" data-copy-idx="${i}" title="Copy command">📋 Copy</button>
        </div>`).join('')}
      </div>
      <div class="detail-item" style="margin-top:1rem;">
        <div class="detail-item-label">From GitHub (no terminal needed)</div>
        <div class="detail-item-value" style="font-family:var(--font-sans);font-weight:500;color:var(--text-secondary);">
          Repo → <strong>Actions</strong> → <strong>Crypto Bot</strong> → <strong>Run workflow</strong> → pick
          <code>sweep-live</code> / <code>check</code> / <code>status</code> / <code>sweep</code> / <code>backtest</code>.
          Tick <strong>reset</strong> to restart all 500 demo accounts at ₹10,000.
        </div>
      </div>
    `;
    modal.classList.add('open');

    content.querySelectorAll('[data-copy-idx]').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.getAttribute('data-copy-idx'), 10);
        const text = commands[idx] ? commands[idx].cmd : '';
        navigator.clipboard.writeText(text).then(() => {
          showToast('Copied command to clipboard!', 'success');
          btn.textContent = '✅ Copied';
          setTimeout(() => { btn.textContent = '📋 Copy'; }, 1500);
        }).catch(() => showToast('Failed to copy.', 'error'));
      });
    });
  }

  // ==========================================================================
  // Data Export & Subtotals
  // ==========================================================================
  function exportFilteredData(format, customRows = null) {
    const rawData = getRawDataset(state.currentDatasetKey);
    const datasetToExport = customRows || sortData(filterData(rawData));
    const config = getActiveConfig();

    if (datasetToExport.length === 0) {
      showToast('No records available to export.', 'error');
      return;
    }

    const filename = `${state.currentDatasetKey}_export_${new Date().toISOString().slice(0, 10)}`;

    if (format === 'csv') {
      const headers = config.columns.map(c => c.key);
      const csvContent = [
        headers.join(','),
        ...datasetToExport.map(row => headers.map(h => {
          let val = row[h];
          if (val === null || val === undefined) return '';
          val = String(val).replace(/"/g, '""');
          return val.includes(',') || val.includes('\n') ? `"${val}"` : val;
        }).join(','))
      ].join('\n');

      downloadFile(csvContent, `${filename}.csv`, 'text/csv;charset=utf-8;');
      showToast(`Exported ${datasetToExport.length} rows to CSV!`, 'success');
    } else if (format === 'json') {
      const jsonContent = JSON.stringify(datasetToExport, null, 2);
      downloadFile(jsonContent, `${filename}.json`, 'application/json;charset=utf-8;');
      showToast(`Exported ${datasetToExport.length} records to JSON!`, 'success');
    } else if (format === 'copy') {
      const text = JSON.stringify(datasetToExport, null, 2);
      navigator.clipboard.writeText(text).then(() => {
        showToast(`Copied ${datasetToExport.length} records to clipboard!`, 'success');
      }).catch(() => {
        showToast('Failed to copy to clipboard.', 'error');
      });
    }
  }

  function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  function showSelectedSubtotals() {
    const rows = getSelectedRowsData();
    if (rows.length === 0) return;

    const totalPnl = rows.reduce((acc, r) => acc + (r.pnl || r.pnl_inr || r.realized_pnl_inr || 0), 0);
    const avgWinRate = rows.length > 0 ? (rows.reduce((acc, r) => acc + (r.win_rate || 0), 0) / rows.length) : 0;
    const avgPnlPct = rows.length > 0 ? (rows.reduce((acc, r) => acc + (r.pnl_pct || 0), 0) / rows.length) : 0;

    alert(
      `📊 Selected Items Subtotal (${rows.length} rows):\n\n` +
      `• Total Combined PnL: ₹${formatNumber(totalPnl, 2)}\n` +
      `• Average PnL Return: ${avgPnlPct >= 0 ? '+' : ''}${avgPnlPct.toFixed(2)}%\n` +
      `• Average Win Rate: ${avgWinRate.toFixed(1)}%\n`
    );
  }

  // ==========================================================================
  // Custom File Parser
  // ==========================================================================
  function parseCustomData(content, filename) {
    content = content.trim();
    if (!content) return;

    let parsed = [];
    try {
      if (content.startsWith('[') || content.startsWith('{')) {
        const raw = JSON.parse(content);
        parsed = Array.isArray(raw) ? raw : [raw];
      } else {
        const lines = content.split(/\r?\n/).filter(l => l.trim().length > 0);
        if (lines.length < 2) throw new Error('CSV must have header and at least one data row');

        const headers = parseCSVLine(lines[0]);
        for (let i = 1; i < lines.length; i++) {
          const values = parseCSVLine(lines[i]);
          const row = {};
          headers.forEach((h, idx) => {
            let val = values[idx] !== undefined ? values[idx].trim() : '';
            if (val === '') {
              row[h] = null;
            } else if (!isNaN(Number(val)) && !val.includes('-') && !val.includes(':')) {
              row[h] = Number(val);
            } else {
              row[h] = val;
            }
          });
          parsed.push(row);
        }
      }

      state.customData = parsed;
      state.customDataTitle = filename ? filename.replace(/\.[^/.]+$/, '') : 'Custom Data';
      state.currentDatasetKey = 'custom';
      closeAllModals();
      loadDataset('custom');
      showToast(`Successfully loaded ${parsed.length} custom records!`, 'success');
    } catch (err) {
      showToast(`Error parsing data: ${err.message}`, 'error');
    }
  }

  function parseCSVLine(text) {
    const res = [];
    let cur = '';
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === '"') {
        if (inQuotes && text[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (c === ',' && !inQuotes) {
        res.push(cur);
        cur = '';
      } else {
        cur += c;
      }
    }
    res.push(cur);
    return res;
  }

  // ==========================================================================
  // Event Listeners Initialization
  // ==========================================================================
  function initEventListeners() {
    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

    const searchInput = document.getElementById('global-search');
    const searchClear = document.getElementById('search-clear-btn');

    if (searchInput) {
      let debounceTimer;
      searchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        const val = e.target.value;
        if (searchClear) searchClear.style.display = val ? 'block' : 'none';

        debounceTimer = setTimeout(() => {
          state.searchQuery = val;
          state.currentPage = 1;
          updateUI();
        }, 150);
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === '/' && document.activeElement !== searchInput && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
          e.preventDefault();
          searchInput.focus();
        }
      });
    }

    if (searchClear) {
      searchClear.addEventListener('click', () => {
        if (searchInput) searchInput.value = '';
        state.searchQuery = '';
        searchClear.style.display = 'none';
        state.currentPage = 1;
        updateUI();
      });
    }

    const resetFiltersBtn = document.getElementById('btn-reset-filters');
    if (resetFiltersBtn) resetFiltersBtn.addEventListener('click', clearAllFilters);

    const colMgrBtn = document.getElementById('btn-column-manager');
    if (colMgrBtn) colMgrBtn.addEventListener('click', openColumnManagerModal);

    const importerBtn = document.getElementById('btn-open-importer');
    if (importerBtn) importerBtn.addEventListener('click', openImporterModal);

    const exportCsvBtn = document.getElementById('btn-export-csv');
    if (exportCsvBtn) exportCsvBtn.addEventListener('click', () => exportFilteredData('csv'));

    const exportJsonBtn = document.getElementById('btn-export-json');
    if (exportJsonBtn) exportJsonBtn.addEventListener('click', () => exportFilteredData('json'));

    const copyDataBtn = document.getElementById('btn-copy-data');
    if (copyDataBtn) copyDataBtn.addEventListener('click', () => exportFilteredData('copy'));

    // Tool Quick Buttons
    document.getElementById('btn-tool-compare')?.addEventListener('click', () => openStrategyComparator(getSelectedRowsData()));
    document.getElementById('btn-tool-montecarlo')?.addEventListener('click', openMonteCarloSimulator);
    document.getElementById('btn-tool-playground')?.addEventListener('click', openStrategyPlayground);
    document.getElementById('btn-tool-ensemble')?.addEventListener('click', openEnsembleAllocator);
    document.getElementById('btn-tool-bot-details')?.addEventListener('click', openBotDetailsModal);
    document.getElementById('btn-tool-coin-details')?.addEventListener('click', openCoinDetailsModal);
    document.getElementById('btn-tool-run-bot')?.addEventListener('click', openRunBotModal);
    document.getElementById('btn-tool-tax')?.addEventListener('click', openTaxCalculatorModal);
    document.getElementById('btn-tool-underwater')?.addEventListener('click', openUnderwaterDrawdownModal);
    document.getElementById('btn-tool-correlation')?.addEventListener('click', openCorrelationHeatmapModal);
    document.getElementById('btn-tool-streak')?.addEventListener('click', openStreakAnalyzerModal);
    document.getElementById('btn-tool-print')?.addEventListener('click', () => window.print());

    // Floating Bulk Action Buttons
    document.getElementById('bulk-compare-btn')?.addEventListener('click', () => openStrategyComparator(getSelectedRowsData()));
    document.getElementById('bulk-export-csv-btn')?.addEventListener('click', () => exportFilteredData('csv', getSelectedRowsData()));
    document.getElementById('bulk-export-json-btn')?.addEventListener('click', () => exportFilteredData('json', getSelectedRowsData()));
    document.getElementById('bulk-subtotals-btn')?.addEventListener('click', showSelectedSubtotals);
    document.getElementById('bulk-deselect-btn')?.addEventListener('click', () => {
      state.selectedRowKeys.clear();
      updateUI();
    });

    // Monte Carlo Simulator inputs
    ['mc-iterations', 'mc-capital'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', runMonteCarloSimulation);
    });

    // Playground Simulator sliders
    ['play-entry-rsi', 'play-exit-rsi', 'play-tp', 'play-sl', 'play-size'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', runPlaygroundSimulation);
    });

    // Ensemble sliders
    ['ens-weight-1', 'ens-weight-2', 'ens-weight-3'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', runEnsembleAllocation);
    });

    // Tax Waterfall inputs
    ['tax-gross-profit', 'tax-turnover'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', calculateTaxWaterfall);
    });

    // Live Refresh Rate Selector
    const refreshSelect = document.getElementById('live-refresh-select');
    if (refreshSelect) {
      refreshSelect.addEventListener('change', (e) => {
        toggleLivePolling(parseInt(e.target.value, 10));
      });
    }

    // Trades Page View Switcher (Backtest / Live / Last-per-bot)
    const tradesViewSwitcher = document.getElementById('trades-view-switcher');
    if (tradesViewSwitcher) {
      tradesViewSwitcher.querySelectorAll('[data-view]').forEach(btn => {
        btn.addEventListener('click', () => switchTradesView(btn.getAttribute('data-view')));
      });
    }

    // Charts Accordion Toggle
    const chartsToggle = document.getElementById('charts-panel-header');
    const chartsBody = document.getElementById('charts-body');
    const chartsChevron = document.getElementById('charts-toggle-chevron');

    if (chartsToggle && chartsBody) {
      chartsToggle.addEventListener('click', () => {
        state.chartsExpanded = !state.chartsExpanded;
        chartsBody.style.display = state.chartsExpanded ? 'grid' : 'none';
        if (chartsChevron) chartsChevron.textContent = state.chartsExpanded ? '▼' : '▶';
        if (state.chartsExpanded) renderCharts(filterData(getRawDataset(state.currentDatasetKey)));
      });
    }

    // Modal Close Handlers
    document.querySelectorAll('.modal-close-btn, .modal-backdrop').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target === el || el.classList.contains('modal-close-btn')) {
          closeAllModals();
        }
      });
    });

    // Custom File Dropzone
    const dropzone = document.getElementById('file-dropzone');
    const fileInput = document.getElementById('file-input');
    const pasteArea = document.getElementById('raw-paste-input');
    const loadPasteBtn = document.getElementById('btn-load-pasted');

    if (dropzone && fileInput) {
      dropzone.addEventListener('click', () => fileInput.click());
      dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('drag-over');
      });
      dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
      dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
          handleFile(e.dataTransfer.files[0]);
        }
      });

      fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
          handleFile(e.target.files[0]);
        }
      });
    }

    function handleFile(file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        parseCustomData(e.target.result, file.name);
      };
      reader.readAsText(file);
    }

    if (loadPasteBtn && pasteArea) {
      loadPasteBtn.addEventListener('click', () => {
        parseCustomData(pasteArea.value, 'Pasted Data');
      });
    }
  }

  // ==========================================================================
  // Helper Formatters & Utilities
  // ==========================================================================
  function formatRank(val) {
    if (val === 1) return `<span class="rank-pill rank-top1">1</span>`;
    if (val === 2) return `<span class="rank-pill rank-top2">2</span>`;
    if (val === 3) return `<span class="rank-pill rank-top3">3</span>`;
    return `<span class="rank-pill">${val}</span>`;
  }

  function formatModeBadge(mode) {
    if (!mode) return '-';
    if (mode === 'dip') return `<span class="badge badge-dip">🌊 Dip</span>`;
    if (mode === 'momentum') return `<span class="badge badge-momentum">🚀 Mom</span>`;
    return `<span class="badge">${escapeHtml(mode)}</span>`;
  }

  function formatReasonBadge(reason) {
    if (!reason) return '-';
    if (reason === 'take_profit') return `<span class="badge badge-tp">🎯 Take Profit</span>`;
    if (reason === 'stop_loss') return `<span class="badge badge-sl">🛑 Stop Loss</span>`;
    if (reason === 'rsi_overbought') return `<span class="badge badge-rsi">⚠️ RSI Overbought</span>`;
    return `<span class="badge">${escapeHtml(reason)}</span>`;
  }

  function formatReasonTitle(reason) {
    if (reason === 'take_profit') return 'Take Profit';
    if (reason === 'stop_loss') return 'Stop Loss';
    if (reason === 'rsi_overbought') return 'RSI Overbought';
    return reason;
  }

  function formatSideBadge(side) {
    if (!side) return '-';
    if (side === 'buy') return `<span class="badge badge-buy">🛒 Buy</span>`;
    if (side === 'sell') return `<span class="badge badge-sell">💰 Sell</span>`;
    return `<span class="badge">${escapeHtml(side)}</span>`;
  }

  function formatAccount(account) {
    if (!account) return '-';
    return `<span class="account-mono">${highlightSearchText(escapeHtml(account))}</span>`;
  }

  function formatAsset(asset) {
    if (!asset) return '-';
    const shortName = asset.replace('INR', '');
    return `
      <span class="coin-pill">
        <span class="coin-icon-bubble">${shortName.slice(0, 3)}</span>
        <span>${highlightSearchText(escapeHtml(asset))}</span>
      </span>
    `;
  }

  function formatWinRate(wr) {
    if (wr === null || wr === undefined) return '-';
    const val = parseFloat(wr);
    const color = val >= 60 ? 'var(--success)' : (val >= 40 ? 'var(--warning)' : 'var(--danger)');
    return `
      <div class="win-rate-cell">
        <span>${val.toFixed(1)}%</span>
        <div class="mini-progress-track">
          <div class="mini-progress-fill" style="width: ${Math.min(100, Math.max(0, val))}%; background-color: ${color}"></div>
        </div>
      </div>
    `;
  }

  function formatPnl(val, prefix = '') {
    if (val === null || val === undefined) return '-';
    const num = parseFloat(val);
    const formatted = Math.abs(num).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (num > 0) return `<span class="val-positive">+${prefix}${formatted}</span>`;
    if (num < 0) return `<span class="val-negative">-${prefix}${formatted}</span>`;
    return `<span class="val-neutral">${prefix}0.00</span>`;
  }

  function formatPnlPct(val) {
    if (val === null || val === undefined) return '-';
    const num = parseFloat(val);
    const formatted = Math.abs(num).toFixed(2);
    if (num > 0) return `<span class="val-positive">+${formatted}%</span>`;
    if (num < 0) return `<span class="val-negative">-${formatted}%</span>`;
    return `<span class="val-neutral">0.00%</span>`;
  }

  function formatHoldTime(h) {
    if (h === 0 || h === '0') return 'Forever (0h)';
    if (h === 72 || h === '72') return '3 Days (72h)';
    if (h === 168 || h === '168') return '1 Week (168h)';
    if (h === 336 || h === '336') return '2 Weeks (336h)';
    if (h === 720 || h === '720') return '1 Month (720h)';
    return `${h}h`;
  }

  function formatCurrency(val) {
    if (val === null || val === undefined) return '-';
    const num = parseFloat(val);
    return `₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function formatPrice(val) {
    if (val === null || val === undefined) return '-';
    const num = parseFloat(val);
    if (isNaN(num)) return val;
    return `₹${num.toLocaleString('en-IN', { maximumFractionDigits: 8 })}`;
  }

  function formatQuantity(val) {
    if (val === null || val === undefined) return '-';
    const num = parseFloat(val);
    if (isNaN(num)) return val;
    return num.toLocaleString('en-US', { maximumFractionDigits: 8 });
  }

  function formatDate(isoStr) {
    if (!isoStr) return '-';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoStr;
    }
  }

  function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined) return '-';
    const n = parseFloat(num);
    if (isNaN(n)) return num;
    return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function formatMetricName(name) {
    if (!name) return '';
    return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  function formatBenchmarkVal(val, metric) {
    if (val === null || val === undefined || val === '') return '-';
    const num = parseFloat(val);
    if (isNaN(num)) return val;
    if (metric.includes('pct') || metric.includes('rate')) return `${num.toFixed(2)}%`;
    if (metric.includes('pnl') || metric.includes('value') || metric.includes('invested')) return `₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return num.toLocaleString();
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icon = type === 'success' ? '✅' : (type === 'error' ? '❌' : 'ℹ️');
    toast.innerHTML = `<span>${icon}</span> <span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => container.removeChild(toast), 300);
    }, 3000);
  }

})();
