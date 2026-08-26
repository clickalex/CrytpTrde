# CI workflow drop-ins

The sandbox's GitHub App token cannot create or update files under
`.github/workflows/` (it lacks the `workflows` permission), so the new test
workflow ships here instead.

To enable CI testing, move the file into place:

```bash
mv docs/ci/tests.yml .github/workflows/tests.yml
```

- `tests.yml` — runs the offline suite (`python3 test_speed_fix.py`, 11 tests)
  on every push and pull request. No network or secrets needed.

- `dca.yml.suggested` — the hourly bot workflow with refreshed comments
  (500-bot tournament, quota wording, pointer to tests.yml). The only
  differences from the live `.github/workflows/dca.yml` are comments, so
  updating it is optional:
  `cp docs/ci/dca.yml.suggested .github/workflows/dca.yml`
