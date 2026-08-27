#!/usr/bin/env python3
"""
Regenerate the embedded live-trade datasets in data.js.

The dashboard is fully static: `data.js` embeds snapshots of the bot's CSV
outputs. Two datasets aren't produced by any single CSV, so this script builds
them from the per-account audit logs:

  * `live_trades`  — every paper fill across all 500 tournament accounts,
                    flattened with the account id (oldest first; the UI sorts
                    newest-first by default).
  * `last_trades`  — the single most recent fill for each account (the
                    "last trade" per bot), newest first.

Source of truth: sweep/accounts/acc_XXX/trades.csv (append-only audit logs
written by `cryptobot.py sweep-live`).

Usage:
    python3 build_data_js.py             # rebuild + inject into data.js
    python3 build_data_js.py --check     # report counts, write nothing

The script is idempotent: re-running replaces the previous auto-generated
block and leaves the hand-maintained datasets in data.js untouched.
"""

import argparse
import csv
import glob
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, "data.js")
ACCOUNT_DIR = os.path.join(ROOT, "sweep", "accounts")

# Everything between this marker and the end of data.js is owned by this script.
MARKER = "// ---- AUTO-GENERATED LIVE TRADE DATASETS (build_data_js.py) ----"


def _num(value):
    value = (value or "").strip()
    return float(value) if value else 0.0


def _round(value, decimals):
    return round(value, decimals)


def load_trades():
    """Flatten every account's trades.csv into one row per fill.

    Returns ``(rows, last_trades)``:
      * ``rows``        — every fill, oldest first (global order).
      * ``last_trades`` — the most recent fill per account, newest first.
    """
    rows = []
    last = {}
    for path in sorted(glob.glob(os.path.join(ACCOUNT_DIR, "*", "trades.csv"))):
        account = os.path.basename(os.path.dirname(path))
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                record = {
                    "account": account,
                    "timestamp_utc": (row.get("timestamp_utc") or "").strip(),
                    "asset": (row.get("asset") or "").strip(),
                    "side": (row.get("side") or "").strip(),
                    "price_inr": _round(_num(row.get("price_inr")), 6),
                    "quantity": _round(_num(row.get("quantity")), 8),
                    "notional_inr": _round(_num(row.get("notional_inr")), 2),
                    "fee_inr": _round(_num(row.get("fee_inr")), 2),
                    "tds_inr": _round(_num(row.get("tds_inr")), 2),
                    "realized_pnl_inr": _round(_num(row.get("realized_pnl_inr")), 2),
                }
                rows.append(record)

                # trades.csv is append-only, so the last row with the latest
                # timestamp is the true "last trade" (ties = same-cycle fills
                # resolved by file order via `>=`).
                current = last.get(account)
                if current is None or record["timestamp_utc"] >= current["timestamp_utc"]:
                    last[account] = record

    rows.sort(key=lambda r: (r["timestamp_utc"], r["account"], r["asset"], r["side"]))
    last_list = sorted(
        last.values(),
        key=lambda r: (r["timestamp_utc"], r["account"]),
        reverse=True,
    )
    return rows, last_list


def _js_block(name, data):
    body = json.dumps(data, indent=2, ensure_ascii=False)
    body = "\n".join(("  " + ln) if ln else ln for ln in body.split("\n"))
    body = body.lstrip()
    return f'  "{name}": {body}'


def inject(content, live_trades, last_trades):
    """Replace (or append) the auto-generated block inside data.js."""
    marker_idx = content.find(MARKER)
    if marker_idx != -1:
        content = content[:marker_idx]

    content = content.rstrip()
    if content.endswith("};"):
        content = content[:-2].rstrip()
    if not content.endswith(","):
        content += ","

    block = (
        "\n" + MARKER + "\n"
        + _js_block("live_trades", live_trades) + ",\n"
        + _js_block("last_trades", last_trades) + "\n"
        + "};\n"
    )
    return content + block


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report counts without writing data.js")
    args = parser.parse_args()

    live, last = load_trades()

    print(f"live_trades : {len(live):,} fills across 500 accounts")
    print(f"last_trades : {len(last):,} rows (one latest trade per bot)")
    if last:
        newest = last[0]
        print("newest fill :", newest["account"], newest["timestamp_utc"],
              newest["side"], newest["asset"],
              f"pnl=₹{newest['realized_pnl_inr']:,.2f}")

    if args.check:
        return

    with open(DATA_JS, encoding="utf-8") as fh:
        content = fh.read()
    new_content = inject(content, live, last)
    with open(DATA_JS, "w", encoding="utf-8") as fh:
        fh.write(new_content)

    size_before = len(content.encode("utf-8"))
    size_after = len(new_content.encode("utf-8"))
    print(f"wrote {DATA_JS} ({size_before/1024:.0f} KiB -> {size_after/1024:.0f} KiB)")


if __name__ == "__main__":
    main()
