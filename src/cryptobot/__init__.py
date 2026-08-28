"""CrytpTrde: a paper-trading bot for CoinDCX INR spot markets.

The package is intentionally split into small, focused modules:

- ``indicators``:   RSI / SMA math
- ``strategy``:     entry/exit signal rules
- ``coindcx``:      CoinDCX REST client
- ``broker``:       PaperBroker (fees, TDS, persistence)
- ``engine``:       market-data cache and the live decision loop
- ``backtest``:     historical replay + HODL benchmark
- ``sweep``:        strategy grid, tournament simulation, live tournament
- ``bot``:          CLI commands
- ``paths``:        repository / data / web paths
"""

from . import paths
from .indicators import *  # noqa: F401,F403
from .strategy import *  # noqa: F401,F403
from .coindcx import *  # noqa: F401,F403
from .broker import *  # noqa: F401,F403
from .engine import *  # noqa: F401,F403
from .backtest import *  # noqa: F401,F403
from .sweep import *  # noqa: F401,F403
from .bot import *  # noqa: F401,F403

# Underscored helpers used by the offline test suite / drill-down API.
from .bot import (_account_trades, _analyze_fills)  # noqa: F401

# Convenience aliases to the configured filesystem locations.
SWEEP_DIR = paths.SWEEP_DIR
ACCOUNTS_DIR = paths.ACCOUNTS_DIR
ACCOUNT_CSV = paths.ACCOUNT_CSV
STATE_DIR = paths.STATE_DIR
LAST_RUN_FILE = paths.LAST_RUN_FILE
