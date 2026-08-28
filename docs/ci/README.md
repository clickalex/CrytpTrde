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

## Note on the root compatibility shims

An earlier version of this README described root shims (`build_data_js.py`,
`test_speed_fix.py`, `index.html`) kept so the unmodified workflows would work
against the new layout. **Those shims are gone** — `main` was later updated to
call `scripts/build_data_js.py` and `tests/test_speed_fix.py` directly, and the
root `index.html` was deleted. Do not re-add them.
