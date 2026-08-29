# Cloud Control Setup Instructions

The workflow file (`.github/workflows/dca.yml`) has been updated to support all bot commands, but GitHub requires manual approval for workflow changes. Follow these steps to enable cloud control.

## Quick Setup

### Step 1: Update the Workflow File

Go to your GitHub repository and edit `.github/workflows/dca.yml`:

1. Navigate to: `https://github.com/clickalex/CrytpTrde`
2. Click on `.github/workflows/dca.yml`
3. Click the pencil icon (Edit this file)
4. Delete all existing content
5. Paste the complete workflow content from below
6. Click **Commit changes** directly to the main branch

### Step 2: Verify Cloud Control Works

1. Go to the **Actions** tab
2. Click **Crypto Bot** in the workflows list
3. Click **Run workflow** (right side)
4. You should now see all 13 commands in the dropdown!

---

## Complete Workflow File Content

Copy everything below and paste it into `.github/workflows/dca.yml`:

```yaml
name: Crypto Bot

# Runs the bot for free in GitHub's cloud.
#   - Every hour at minute :30 UTC = minute 00 IST (e.g. 09:00, 10:00 ... IST).
#     Default: `sweep-live` — the 500-bot tournament: 500 demo accounts, each
#     with a unique strategy (dip + momentum families, hold periods from
#     forever to one month), trading LIVE on real prices. The ranking is in
#     data/sweep/live_summary.csv after every run. Use the Actions dropdown to run
#     `check` / `status` / `backtest` / `sweep` (offline tournament) manually
#     from your phone.
#   - Free quota (private repos): hourly runs stay within the 2,000 minutes/
#     month for the current tournament size. Make the repo PUBLIC for
#     unlimited minutes, or reduce frequency / sweep.accounts if needed.
#   - Pushes and pull requests are ALSO tested by tests.yml (offline suite).
on:
  schedule:
    - cron: "30 * * * *"
  workflow_dispatch:
    inputs:
      command:
        description: "Bot command to run"
        type: choice
        options:
          - sweep-live
          - check
          - status
          - sweep
          - sweep-status
          - backtest
          - init
          - reset
          - assets
          - prune
          - bot
          - coin
          - wipe
        default: sweep-live
      reset:
        description: "Reset all demo accounts (restart at Rs 10,000 each) - for sweep-live"
        type: boolean
        default: false
      account_id:
        description: "Account ID (e.g. acc_001) - for 'bot' command"
        type: string
        required: false
      coin_asset:
        description: "Coin asset (e.g. BTCINR) - for 'coin' command"
        type: string
        required: false
      backtest_days:
        description: "Number of days for backtest (default: 30)"
        type: number
        required: false
      sweep_days:
        description: "Number of days for sweep tournament (default: 30)"
        type: number
        required: false
      sweep_count:
        description: "Number of bots in sweep (default: 500)"
        type: number
        required: false
      max_trades:
        description: "Max trades to retain in prune (default: 100)"
        type: number
        required: false
      confirm_wipe:
        description: "Type 'YES' to confirm wipe command (deletes ALL data)"
        type: string
        required: false

permissions:
  contents: write

concurrency:
  group: crypto-bot
  cancel-in-progress: true

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run bot
        env:
          # Live trading (optional): repo -> Settings -> Secrets and actions.
          # Leave unset in paper mode — the bot runs without them.
          COINDCX_API_KEY: ${{ secrets.COINDCX_API_KEY }}
          COINDCX_API_SECRET: ${{ secrets.COINDCX_API_SECRET }}
        run: |
          CMD="${{ github.event.inputs.command }}"
          CMD="${CMD:-sweep-live}"
          ARGS=""
          
          # Handle command-specific arguments
          if [ "${CMD}" = "sweep-live" ]; then
            if [ "${{ github.event.inputs.reset }}" = "true" ]; then
              ARGS="--reset"
            fi
          elif [ "${CMD}" = "bot" ]; then
            ACCOUNT="${{ github.event.inputs.account_id }}"
            if [ -z "${ACCOUNT}" ]; then
              echo "Error: 'account_id' input is required for 'bot' command"
              exit 1
            fi
            ARGS="${ACCOUNT}"
          elif [ "${CMD}" = "coin" ]; then
            COIN="${{ github.event.inputs.coin_asset }}"
            if [ -z "${COIN}" ]; then
              echo "Error: 'coin_asset' input is required for 'coin' command"
              exit 1
            fi
            ARGS="${COIN}"
          elif [ "${CMD}" = "backtest" ]; then
            DAYS="${{ github.event.inputs.backtest_days }}"
            if [ -n "${DAYS}" ]; then
              ARGS="--days ${DAYS}"
            fi
          elif [ "${CMD}" = "sweep" ]; then
            ARGS=""
            DAYS="${{ github.event.inputs.sweep_days }}"
            COUNT="${{ github.event.inputs.sweep_count }}"
            if [ -n "${DAYS}" ]; then
              ARGS="--days ${DAYS}"
            fi
            if [ -n "${COUNT}" ]; then
              ARGS="${ARGS} --count ${COUNT}"
            fi
          elif [ "${CMD}" = "prune" ]; then
            MAX_TRADES="${{ github.event.inputs.max_trades }}"
            if [ -n "${MAX_TRADES}" ]; then
              ARGS="--max-trades ${MAX_TRADES}"
            fi
          elif [ "${CMD}" = "wipe" ]; then
            CONFIRM="${{ github.event.inputs.confirm_wipe }}"
            if [ "${CONFIRM}" != "YES" ]; then
              echo "Error: wipe command requires confirm_wipe='YES' to execute"
              echo "This command deletes ALL bot data and cannot be undone."
              exit 1
            fi
            ARGS="--yes"
          fi
          
          echo "Running: python cryptobot.py ${CMD} ${ARGS}"
          python cryptobot.py ${CMD} ${ARGS}

      - name: Rebuild dashboard data (web/data.js)
        # Embeds the fresh heartbeat (data/state/last_run.json -> "bot_status",
        # rendered as the "Last bot run" badge) plus the latest tournament
        # trades into web/data.js, so the Pages site shows WHEN the bot last ran.
        run: python3 scripts/build_data_js.py

      - name: Save state back to the repo
        run: |
          git config user.name "crypto-bot"
          git config user.email "crypto-bot@users.noreply.github.com"
          git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
          git add -A
          if git diff --cached --quiet; then
            echo "No state changes — nothing to commit."
          else
            git commit -qm "bot state: $(date -u +%FT%TZ)"
            git push
            echo "State saved."
          fi
```

---

## What's New

### All 13 Commands Now Available

✅ **Tournament Commands:**
- `sweep-live` - Run 500-bot tournament on live prices (with optional reset)
- `sweep-status` - Rebuild rankings without trading
- `sweep` - Historical tournament (configurable days & bot count)

✅ **Trading Commands:**
- `check` - One signal-check cycle
- `status` - Portfolio & P&L report
- `assets` - List monitored markets
- `init` - Initialize/reset paper portfolio
- `reset` - Clear live bot state

✅ **Analysis Commands:**
- `backtest` - Test strategy on historical data (configurable days)
- `bot` - View specific bot's trade history (requires account_id)
- `coin` - Find which bots traded a coin (requires coin_asset)

✅ **Maintenance Commands:**
- `prune` - Trim trade logs (configurable max_trades)
- `wipe` - Delete ALL data (requires typing 'YES' to confirm)

### New Parameters

| Parameter | Used By | Required | Description |
|-----------|---------|----------|-------------|
| `reset` | sweep-live | No | Reset all accounts to ₹10,000 |
| `account_id` | bot | Yes | Account ID (e.g., acc_001) |
| `coin_asset` | coin | Yes | Coin name (e.g., BTCINR) |
| `backtest_days` | backtest | No | Days of history (default: 30) |
| `sweep_days` | sweep | No | Days of history (default: 30) |
| `sweep_count` | sweep | No | Number of bots (default: 500) |
| `max_trades` | prune | No | Max trades to keep (default: 100) |
| `confirm_wipe` | wipe | Yes | Type 'YES' to confirm |

---

## Usage Examples

### From GitHub Web UI

1. Go to **Actions** → **Crypto Bot** → **Run workflow**
2. Select command from dropdown
3. Fill in required parameters (if any)
4. Click **Run workflow**

### From GitHub Mobile App

1. Open app → Navigate to repository
2. Tap **Actions** → **Crypto Bot**
3. Tap **Run workflow**
4. Select command and parameters
5. Tap **Run workflow**

### From GitHub CLI

```bash
# Check portfolio status
gh workflow run "Crypto Bot" -f command=status

# View bot #42's history
gh workflow run "Crypto Bot" -f command=bot -f account_id=acc_042

# Analyze Bitcoin traders
gh workflow run "Crypto Bot" -f command=coin -f coin_asset=BTCINR

# Reset tournament
gh workflow run "Crypto Bot" -f command=sweep-live -f reset=true

# Quick test with smaller dataset
gh workflow run "Crypto Bot" -f command=sweep -f sweep_days=7 -f sweep_count=100

# Wipe all data (with confirmation)
gh workflow run "Crypto Bot" -f command=wipe -f confirm_wipe=YES
```

---

## Safety Features

### Required Parameters
- `bot` command fails if `account_id` is missing
- `coin` command fails if `coin_asset` is missing
- Clear error messages guide you to provide the missing parameter

### Destructive Command Protection
- `wipe` command requires typing exactly `YES` (all caps)
- Any other value aborts the command
- Prevents accidental data deletion

### Concurrency Control
- Only one workflow runs at a time
- Starting a new run cancels any in-progress run
- Prevents conflicts from simultaneous commands

---

## Troubleshooting

### "account_id is required for 'bot' command"
→ Fill in the `account_id` field (e.g., `acc_001`)

### "coin_asset is required for 'coin' command"
→ Fill in the `coin_asset` field (e.g., `BTCINR`)

### "wipe command requires confirm_wipe='YES'"
→ Type `YES` (all caps, no quotes) in the `confirm_wipe` field

### Workflow stuck in "queued"
→ Another workflow is running. Wait or cancel it manually.

### Command not appearing in dropdown
→ Make sure you've committed the updated workflow file to the main branch

---

## Next Steps

1. ✅ Update `.github/workflows/dca.yml` with the content above
2. ✅ Test with a simple command: `status` or `assets`
3. ✅ Try commands with parameters: `bot` with `account_id=acc_001`
4. ✅ Read the full guide in `CLOUD_CONTROL.md` for advanced usage

---

## Support

- Full documentation: `CLOUD_CONTROL.md`
- Original README: `README.md`
- GitHub Actions logs: Check the Actions tab for detailed output

---

**Note:** The hourly scheduled run continues to work as before. Cloud control is an addition, not a replacement for automation.
