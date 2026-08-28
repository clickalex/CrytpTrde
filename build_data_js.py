#!/usr/bin/env python3
"""Legacy CLI shim for build_data_js.py.

The generator now lives at scripts/build_data_js.py and writes web/data.js.
This root shim keeps the old `python3 build_data_js.py` command working in
GitHub Actions workflows that have not been updated to the new repo layout.
Remove it once .github/workflows/dca.yml runs scripts/build_data_js.py.
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
sys.argv[0] = str(Path(__file__).resolve().parent / "scripts" / "build_data_js.py")
runpy.run_path(sys.argv[0], run_name="__main__")
