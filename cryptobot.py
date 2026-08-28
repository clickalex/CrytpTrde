#!/usr/bin/env python3
"""CrytpTrde - thin CLI launcher.

The real bot lives in the ``src/cryptobot`` package. This script keeps the
familiar ``python3 cryptobot.py ...`` command working without installation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cryptobot.bot import main  # noqa: E402

if __name__ == "__main__":
    main()
