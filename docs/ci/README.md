# CI workflow drop-ins

The sandbox's GitHub App token cannot create or update files under
`.github/workflows/` (it lacks the `workflows` permission), so workflow
changes are shipped as drop-ins in this directory instead. The reorg branch
therefore keeps the existing `.github/workflows/*` files untouched and adds
root compatibility shims (`build_data_js.py`, `test_speed_fix.py`,
`index.html`) so the unmodified workflows still work against the new layout.

After merging the structure change, apply these updated workflows:

```bash
cp docs/ci/dca.yml.suggested .github/workflows/dca.yml
cp docs/ci/tests.yml .github/workflows/tests.yml
```

The drop-ins update the paths for the new layout:

- `dca.yml.suggested` — hourly bot workflow updated for the new layout
  (data lives under `data/`, the dashboard generator is
  `scripts/build_data_js.py`, the generated dataset is `web/data.js`, and
  the Pages deploy should publish `web/`).
- `tests.yml` — offline suite (`python3 tests/test_speed_fix.py`, 13 tests)
  on every push and pull request. No network or secrets needed. Test 13
  asserts every module imports the helpers it calls, so a missing import
  after the package split cannot reach the hourly bot run again.

Once the drop-ins are in place, the root compatibility shims
(`build_data_js.py`, `test_speed_fix.py`, and `index.html`) can be deleted.
