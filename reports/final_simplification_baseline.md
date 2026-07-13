# Final Simplification Baseline

Generated: 2026-07-13

## Source

Started from supplied `hunterX_source_0.1.6.zip` and extracted 150 files into the current repository root. The zip was a source tree without a top-level wrapper directory and without a .git directory.

## Baseline Commands

| Command | Result | Evidence |
| --- | ---: | --- |
| `git.exe status --short` | 128 | fatal: not a git repository (or any of the parent directories): .git |
| `git.exe diff` | 129 |                           select files by diff type /     --max-depth <depth>   maximum tree depth to recurse /     --output <file>       output to a specific file |
| `git.exe diff --stat` | 129 |                           select files by diff type /     --max-depth <depth>   maximum tree depth to recurse /     --output <file>       output to a specific file |
| `python.exe -m compileall src tests scripts` | 0 | Listing 'scripts'... / Compiling 'scripts\\release_utils.py'... / Compiling 'scripts\\render_benchmark_chart.py'... |
| `python.exe -m pytest -q` | 1 | =========================== short test summary info =========================== / FAILED tests/test_metadata.py::test_settings_ui_referenced_vendor_assets_exist / 1 failed, 101 passed in 15.27s |
| `python.exe -m ruff check src tests scripts` | 0 | All checks passed! |
| `python.exe -m mypy` | 0 | Success: no issues found in 4 source files |
| `python.exe -m pip_audit -r requirement.txt` | 0 | No known vulnerabilities found |

## Baseline Result

- Git commands were unavailable because the supplied source zip is not a git checkout.
- Baseline compileall, Ruff, MyPy, and pip-audit passed.
- Baseline pytest had 1 failure: `tests/test_metadata.py::test_settings_ui_referenced_vendor_assets_exist`; the source zip referenced local Bootstrap/jQuery assets that were not present.
