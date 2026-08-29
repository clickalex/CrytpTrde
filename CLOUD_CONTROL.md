# Cloud Control Guide

Control your CrytpTrde bot remotely through GitHub Actions. Trigger any bot command from anywhere using your browser, phone, or the GitHub mobile app.

## Accessing Cloud Control

1. Go to your repository on GitHub: `https://github.com/clickalex/CrytpTrde`
2. Click on the **Actions** tab
3. Select **Crypto Bot** from the workflows list
4. Click **Run workflow** button (right side)
5. Fill in the command and parameters
6. Click **Run workflow** to execute

## Available Commands

### Tournament Commands

#### `sweep-live` (Default)
Run the 500-bot tournament on live prices (paper trading).

**Parameters:**
- `reset` (boolean): Reset all demo accounts to ₹10,000 each

**Example:**
- To restart the tournament fresh: Set `reset = true`
- To continue current tournament: Leave `reset = false`

---

#### `sweep-status`
Rebuild the tournament ranking without executing new trades.

**Parameters:** None

**Use case:** Refresh the leaderboard display after manual data changes.

---

#### `sweep`
Run an offline historical tournament across the strategy parameter grid.

**Parameters:**
- `sweep_days` (number, optional): Number of days of history to replay (default: 30)
- `sweep_count` (number, optional): Number of bots in tournament (default: 500)

**Example:**
- To test with 7 days and 100 bots: `sweep_days = 7`, `sweep_count = 100`

---

### Trading Commands

#### `check`
Execute one signal-check cycle: fetch market data, evaluate RSI signals, and place simulated fills.

**Parameters:** None

**Use case:** Manual trigger to check for trading opportunities outside the hourly schedule.

---

#### `status`
Display current portfolio, per-asset P&L, and paper tax estimate.

**Parameters:** None

**Output:** Shows in the GitHub Actions log. View results in the Actions tab after the run completes.

---

#### `assets`
List all assets the current configuration resolves to (including market discovery).

**Parameters:** None

**Use case:** Verify which markets the bot is monitoring.

---

#### `init`
Create or reset the paper portfolio at `initial_cash_inr` (default ₹50,000).

**Parameters:** None

**Warning:** This resets the main bot's portfolio state.

---

#### `reset`
Delete `data/state/portfolio.json` and `data/state/trades.csv` to start fresh.

**Parameters:** None

**Warning:** Clears all live bot trading history.

---

### Analysis Commands

#### `backtest`
Replay the strategy on real 1-hour historical candles vs a HODL benchmark.

**Parameters:**
- `backtest_days` (number, optional): Number of days of history to test (default: 30)

**Example:**
- To test last 60 days: `backtest_days = 60`

**Output:** Results saved to `data/backtest/`

---

#### `bot`
Display full trade history of one tournament bot.

**Parameters:**
- `account_id` (string, **required**): Account ID (e.g., `acc_001`, `acc_123`)

**Example:**
- To view bot 1's history: `account_id = acc_001`

**Output:** Shows all fills and current holdings for that account.

---

#### `coin`
List every tournament bot that traded a specific coin.

**Parameters:**
- `coin_asset` (string, **required**): Coin asset name (e.g., `BTCINR`, `ETHINR`, `SOLINR`)

**Example:**
- To see which bots traded Bitcoin: `coin_asset = BTCINR`

**Output:** Shows per-bot buy/sell counts, net quantity, fees, TDS, and realized P&L.

---

### Maintenance Commands

#### `prune`
Trim trade audit logs across all accounts to reduce file size.

**Parameters:**
- `max_trades` (number, optional): Maximum trades to retain per account (default: 100)

**Example:**
- To keep only last 50 trades: `max_trades = 50`

**Use case:** Reduce repository size when trade logs get large.

---

#### `wipe` ⚠️ **DESTRUCTIVE**
Delete ALL bot data and reset the dashboard to a blank slate.

**Parameters:**
- `confirm_wipe` (string, **required**): Type `YES` (exactly) to confirm

**Warning:** This permanently deletes:
- `data/state/` (live bot portfolio and trades)
- `data/sweep/` (all 500 tournament accounts)
- `data/backtest/` (all backtest results)

**Safety:** The workflow requires `confirm_wipe = YES` to execute. Any other value (or empty) will abort the command.

**Example:**
- To execute wipe: `confirm_wipe = YES`
- To cancel (any other value): `confirm_wipe = NO` or leave empty

---

## Common Workflows

### Daily Monitoring
```
Command: status
```
Check portfolio performance and P&L.

### Weekly Tournament Review
```
Command: sweep-status
```
Refresh rankings without running new trades.

### Test New Strategy Parameters
```
Command: sweep
sweep_days: 7
sweep_count: 100
```
Quick test with smaller dataset and fewer bots.

### Investigate Specific Bot
```
Command: bot
account_id: acc_042
```
View complete trade history for bot #42.

### Analyze Bitcoin Traders
```
Command: coin
coin_asset: BTCINR
```
See which bots traded Bitcoin and their performance.

### Reset Tournament
```
Command: sweep-live
reset: true
```
Restart all 500 accounts at ₹10,000 each.

### Emergency Data Wipe
```
Command: wipe
confirm_wipe: YES
```
⚠️ Deletes everything and starts from scratch.

---

## Tips

### Mobile Control
Install the GitHub mobile app (iOS/Android) to trigger commands from your phone:
1. Open the app
2. Navigate to your repository
3. Go to Actions → Crypto Bot
4. Tap "Run workflow"
5. Select command and parameters
6. Tap "Run workflow"

### Scheduled Runs
The bot runs automatically every hour at minute :30 UTC (minute 00 IST). You don't need to manually trigger `sweep-live` unless you want to:
- Reset the tournament
- Run immediately instead of waiting for the next scheduled hour

### Viewing Results
- **Logs:** Click on the workflow run in Actions tab to see command output
- **Dashboard:** Results are reflected in the GitHub Pages site after the run completes
- **Data files:** Raw CSV/JSON data is committed back to the repository

### Cost & Quotas
- **Public repos:** Unlimited GitHub Actions minutes (free)
- **Private repos:** 2,000 minutes/month (free tier)
- Each command takes 1-5 minutes depending on complexity
- `sweep` and `sweep-live` are the most time-intensive (~3-5 minutes)
- `status`, `check`, and `assets` are fastest (~30 seconds)

### Concurrency
The workflow has `concurrency: crypto-bot` with `cancel-in-progress: true`. This means:
- Only one workflow runs at a time
- Starting a new run cancels any in-progress run
- Prevents conflicts from simultaneous commands

---

## Command Compatibility Matrix

| Command | Needs API Key | Modifies State | Takes Time | Safe to Run Freently |
|---------|---------------|----------------|------------|---------------------|
| `sweep-live` | No | Yes | ~3-5 min | Yes (hourly) |
| `sweep-status` | No | Yes | ~1 min | Yes |
| `sweep` | No | Yes | ~3-5 min | Occasionally |
| `check` | No | Yes | ~30 sec | Yes |
| `status` | No | No | ~10 sec | Yes |
| `assets` | No | No | ~10 sec | Yes |
| `backtest` | No | Yes | ~1-2 min | Occasionally |
| `bot` | No | No | ~10 sec | Yes |
| `coin` | No | No | ~10 sec | Yes |
| `prune` | No | Yes | ~30 sec | Rarely |
| `init` | No | Yes | ~10 sec | Rarely |
| `reset` | No | Yes | ~10 sec | Rarely |
| `wipe` | No | Yes | ~10 sec | ⚠️ Very rarely |

---

## Troubleshooting

### "account_id is required for 'bot' command"
- You selected the `bot` command but didn't provide an account ID
- Fill in the `account_id` field (e.g., `acc_001`)

### "coin_asset is required for 'coin' command"
- You selected the `coin` command but didn't provide a coin name
- Fill in the `coin_asset` field (e.g., `BTCINR`)

### "wipe command requires confirm_wipe='YES'"
- The wipe command requires explicit confirmation
- Type `YES` (all caps, no quotes) in the `confirm_wipe` field
- Any other value will abort the command

### Workflow stuck in "queued"
- Another workflow run is in progress
- Wait for it to complete, or cancel it manually
- The concurrency setting prevents simultaneous runs

### Command failed but no error message
- Check the full log output in the Actions tab
- Click on the failed run → "Run bot" step → expand to see details
- Common causes: network issues with CoinDCX API, invalid parameters

---

## Advanced: GitHub CLI

You can also trigger workflows from the command line using [GitHub CLI](https://cli.github.com/):

```bash
# Install GitHub CLI
# macOS: brew install gh
# Linux: see https://cli.github.com/

# Login
gh auth login

# Trigger a command
gh workflow run "Crypto Bot" -f command=status

# With parameters
gh workflow run "Crypto Bot" \
  -f command=bot \
  -f account_id=acc_042

# Sweep with custom parameters
gh workflow run "Crypto Bot" \
  -f command=sweep \
  -f sweep_days=7 \
  -f sweep_count=100

# Reset tournament
gh workflow run "Crypto Bot" \
  -f command=sweep-live \
  -f reset=true

# Wipe all data (requires confirmation)
gh workflow run "Crypto Bot" \
  -f command=wipe \
  -f confirm_wipe=YES
```

This is useful for scripting or automation (e.g., cron jobs, CI/CD pipelines).

---

## Security Notes

- All commands run in GitHub's cloud infrastructure
- No API keys are needed for paper trading mode
- The `wipe` command requires explicit confirmation to prevent accidents
- Workflow runs are logged and auditable in the Actions tab
- Only collaborators with write access can trigger workflows
- State changes are committed to the repository with timestamps

---

## Support

For issues or questions:
1. Check the [main README](README.md)
2. Review the workflow logs in the Actions tab
3. Open an issue on GitHub

---

**Remember:** This is educational paper-trading software. No real money is at risk, but treat the data with care — especially the `wipe` command!
