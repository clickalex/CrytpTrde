# CI workflow drop-ins

The sandbox's GitHub App token cannot create, update or delete files under
`.github/workflows/` (it lacks the `workflows` permission), so workflow changes
are shipped as drop-ins in this directory instead.

Apply the ones you need from a machine with `workflows` permission:

```bash
cp docs/ci/tests.yml  .github/workflows/tests.yml   # new test count
git rm .github/workflows/static.yml                 # see pages.yml
git rm .github/workflows/jekyll-gh-pages.yml        # see pages.yml
cp docs/ci/pages.yml  .github/workflows/pages.yml   # publish web/ only
```

## Current status of each file

| File | Status |
|------|--------|
| `tests.yml` | **Out of date.** The live workflow still says "12 tests"; the suite now runs 13 (the extra one asserts every module imports the helpers it calls). Copy this drop-in to pick up the label. |
| `dca.yml.suggested` | **Already applied.** `.github/workflows/dca.yml` is byte-identical to this file — kept here only as a reference. |
| `pages.yml` | **New.** One of two ways to publish `web/` — the dashboard is currently not published at all. Not yet applied. |
| `tournament.yml` | **New.** Cloud control: `sweep-live`, `sweep-status`, `sweep` from the Actions tab. Not yet applied. |
| `trading.yml` | **New.** Cloud control: `check`, `status`, `assets`, `init`, `reset` from the Actions tab. Not yet applied. |
| `analysis.yml` | **New.** Cloud control: `backtest`, `bot`, `coin` from the Actions tab. Not yet applied. |
| `maintenance.yml` | **New.** Cloud control: `prune`, `wipe` from the Actions tab. Not yet applied. |

## `tests.yml` — offline suite

`python3 tests/test_speed_fix.py` (16 tests) on every push and pull request.
No network and no secrets needed. Test 13 walks each module's compiled bytecode
and asserts every `LOAD_GLOBAL` resolves at import time, so a missing import
after the package split cannot reach the hourly bot run again. Test 16 verifies
trade retention and pruning rules.

## `dca.yml.suggested` — hourly bot workflow

Updated for the reorganized layout: data lives under `data/`, the dashboard
generator is `scripts/build_data_js.py`, the generated dataset is `web/data.js`.
Already applied to the live workflow.

## `pages.yml` — Pages deployment

**The dashboard is not currently live.** Pages is configured as
*Deploy from a branch → `main` / `/docs`*, so the site root is the `docs/`
folder: `https://clickalex.github.io/CrytpTrde/` 404s, and so does
`/web/index.html`. It serves these CI notes instead.

Two ways to fix it — pick one:

- **Settings (no file change, no `workflows` permission needed):**
  Settings → Pages → Source → *Deploy from a branch* → branch `main`,
  folder **`/web`**.
- **Actions (this drop-in):** delete the two legacy workflows, copy
  `pages.yml` into `.github/workflows/`, then set the Pages source to
  *GitHub Actions*.

Either way the legacy workflows should be deleted: they both fire on every
push to `main`, share the `pages` concurrency group (so they race and bill
minutes twice), and `static.yml` uploads the whole repository (`path: '.'`).

## Cloud control drop-ins — every bot command from the Actions tab

Four `workflow_dispatch`-only workflows that let you trigger **all 13 bot
commands** from the GitHub Actions UI (browser, phone or `gh workflow run`),
without giving each run a 13-entry dropdown of mostly-irrelevant inputs.
Each workflow covers one command family, so the "Run workflow" form shows
only the inputs that family actually uses:

| Drop-in | Workflow name | Commands | Extra inputs |
|---------|---------------|----------|--------------|
| `tournament.yml` | Tournament Commands | `sweep-live`, `sweep-status`, `sweep` | `reset`, `sweep_days`, `sweep_count` |
| `trading.yml` | Trading Commands | `check`, `status`, `assets`, `init`, `reset` | — |
| `analysis.yml` | Analysis Commands | `backtest`, `bot`, `coin` | `account_id`, `coin_asset`, `backtest_days` |
| `maintenance.yml` | Maintenance Commands | `prune`, `wipe` | `max_trades`, `confirm_wipe` |

Usage guide for all 13 commands lives in
[`CLOUD_CONTROL.md`](../../CLOUD_CONTROL.md).

### Applying them

Copying files into `.github/workflows/` needs the `workflows` permission
(the sandbox GitHub App token does not have it), so from a machine that does:

```bash
cp docs/ci/tournament.yml  .github/workflows/tournament.yml
cp docs/ci/trading.yml     .github/workflows/trading.yml
cp docs/ci/analysis.yml    .github/workflows/analysis.yml
cp docs/ci/maintenance.yml .github/workflows/maintenance.yml
git add .github/workflows/
git commit -m "Apply cloud control workflows to .github/workflows/"
```

### Notes

- **`.github/workflows/dca.yml` is untouched.** It keeps running the hourly
  `sweep-live` on its cron schedule; these four are manual-only additions,
  not a replacement.
- All four (and `dca.yml`) share the `concurrency: crypto-bot` group with
  `cancel-in-progress: true`, so a manual run never races the hourly run or
  another command — the newest run wins, one at a time.
- Every run rebuilds `web/data.js` (`scripts/build_data_js.py`) and commits
  any state changes back to the repo, same as the hourly workflow.
- `wipe` is guarded: it only executes when `confirm_wipe` is exactly `YES`;
  anything else (or empty) aborts before the bot is invoked.
- `COINDCX_API_KEY` / `COINDCX_API_SECRET` are passed through from repo
  secrets but are optional — paper mode runs fine without them.
- The `run` subcommand (foreground hourly loop) is deliberately not exposed —
  it never terminates, so it has no place in a one-shot Actions run.

## Note on the root compatibility shims

An earlier version of this README described root shims (`build_data_js.py`,
`test_speed_fix.py`, `index.html`) kept so the unmodified workflows would work
against the new layout. **Those shims are gone** — `main` was later updated to
call `scripts/build_data_js.py` and `tests/test_speed_fix.py` directly, and the
root `index.html` was deleted. Do not re-add them.
