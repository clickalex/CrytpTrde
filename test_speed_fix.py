#!/usr/bin/env python3
"""Legacy CLI shim for test_speed_fix.py.

The offline suite now lives at tests/test_speed_fix.py. This root shim keeps
the old `python3 test_speed_fix.py` command working in GitHub Actions
workflows that have not been updated to the new repo layout. Remove it once
.github/workflows/tests.yml runs tests/test_speed_fix.py.
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
sys.argv[0] = str(Path(__file__).resolve().parent / "tests" / "test_speed_fix.py")
runpy.run_path(sys.argv[0], run_name="__main__")
