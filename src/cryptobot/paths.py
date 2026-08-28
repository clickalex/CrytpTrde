"""Filesystem paths for bot source, config, data, and the web dashboard.

Everything is resolved from the repository root (the package lives in
``src/cryptobot``) so the commands, tests, and GitHub Actions work no matter
which directory they are launched from.
"""

from pathlib import Path

# src/cryptobot/paths.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
WEB_DIR = REPO_ROOT / "web"

# Configuration
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"

# Runtime / generated data (the repo *is* the database)
DATA_DIR = REPO_ROOT / "data"

# Live bot state
STATE_DIR = DATA_DIR / "state"
LAST_RUN_FILE = STATE_DIR / "last_run.json"

# Tournament state
SWEEP_DIR = DATA_DIR / "sweep"
ACCOUNTS_DIR = SWEEP_DIR / "accounts"
ACCOUNT_CSV = SWEEP_DIR / "accounts.csv"
SIGNATURE_FILE = SWEEP_DIR / "grid_signature.txt"
LIVE_SUMMARY_CSV = SWEEP_DIR / "live_summary.csv"
RESULTS_CSV = SWEEP_DIR / "results.csv"
EQUITY_TOP10_CSV = SWEEP_DIR / "equity_top10.csv"
BEST_STRATEGY_YAML = SWEEP_DIR / "best_strategy.yaml"

# Backtest outputs
BACKTEST_DIR = DATA_DIR / "backtest"
BACKTEST_RESULTS_CSV = BACKTEST_DIR / "backtest_results.csv"
BACKTEST_EQUITY_CSV = BACKTEST_DIR / "backtest_equity.csv"
BACKTEST_TRADES_CSV = BACKTEST_DIR / "backtest_trades.csv"

# Embedded dashboard dataset
DATA_JS = WEB_DIR / "data.js"
