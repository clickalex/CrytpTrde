# Tax notes (India) — read before trusting any number the bot prints

> **This is not tax advice.** It is a summary of how *this bot* models Indian
> tax on virtual digital assets (VDA) so you can read its output critically.
> Tax law changes; talk to a CA before filing.

## Why the bot reports tax at all

Every sell in `paper` mode is a simulated disposal. Indian rules make the
difference between gross P&L and *taxable* P&L large enough to change whether
a strategy is worth running, so the bot reports both:

- `Realized P&L total` — what the strategy actually made.
- `Gross gains (taxable @30%)` — the sum of the **profitable** sells only.
- `Gross losses (NOT offsettable)` — shown separately, because losses do not
  reduce the tax bill.
- `Estimated tax on gains` — 30% of gross gains.
- `1% TDS withheld` — what the exchange already deducted; claim it as credit.

You can reproduce all of it from the paper portfolio at any time:

```bash
python3 cryptobot.py status          # portfolio + the tax block above
python3 cryptobot.py coin BTCINR     # per-coin attribution across the bots
```

## The rules being modelled

| Rule | What it means for the numbers |
|------|-------------------------------|
| **30% flat on VDA gains** (Sec. 115BBH) | Gains are taxed at 30% regardless of your slab. The bot's estimate does **not** add the 4% health & education cess, so the real figure is slightly higher (~31.2% of gross gains). |
| **No loss offset** | A ₹10,000 loss cannot cancel a ₹10,000 gain. Losses also cannot be carried forward under 115BBH. The bot therefore never nets them. |
| **No deductions** | Only the cost of acquisition is deductible. Exchange fees are part of the cost basis (the bot already folds buy fees into `avg_cost`), but no other expense reduces the gain. |
| **1% TDS (Sec. 194S)** | Deducted by the exchange on every sell. It is **not** an expense — it is tax already paid, creditable against the 30% liability. |
| **TDS is not P&L** | The bot deducts TDS from cash but deliberately keeps it out of `realized_pnl`, so a strategy's return is not understated by a refundable credit. |

## Conventions the bot uses

1. **Cost basis is fee-inclusive.** A buy's basis is `notional + fee`, averaged
   per unit (moving-average, not FIFO). This is what `PaperBroker` persists in
   `portfolio.json` and what `cryptobot.py bot acc_XXX` replays.
2. **TDS applies to sells only.** HODL benchmarks charge no TDS because nothing
   is disposed of — that is why the HODL comparison is not always apples-to-apples
   with a high-turnover strategy.
3. **Slippage and fees are modelled on both sides**, so the reported gain is
   net of trading friction but *before* tax.
4. **`realized_pnl_inr` in `trades.csv` is cumulative**, not per-sell. Use
   `cryptobot.py bot acc_XXX` (or the Bot Details page) for per-fill attribution.

## Known gaps

- The 4% cess is not added to the 30% estimate.
- No surcharge, and no interaction with any other income you have.
- Transfers between your own wallets, or VDA received as a gift, are not modelled.
- The tax estimate assumes every realized gain is a VDA gain. If you also hold
  assets outside this bot, they are not consolidated here.

## Files that hold the numbers

| Path | Contents |
|------|----------|
| `data/state/portfolio.json` | Main paper account: cash, positions, realized P&L |
| `data/state/trades.csv` | Append-only audit log of every fill |
| `data/sweep/accounts/acc_XXX/trades.csv` | Per-tournament-bot audit log |
| `data/backtest/backtest_trades.csv` | Backtest fills (historical replay) |
