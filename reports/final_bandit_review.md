# Final Bandit Review

## Medium Finding #1

- Finding: `B103:set_bad_file_permissions` at `src/chrome_downloader.py:222`, setting downloaded Chrome executable to `0o755`.
- Classification: FIXED.
- Change: permission narrowed to `0o700` for current-user execution on Unix systems.

## Medium Finding #2

- Classification: NOT_PRESENT_IN_CURRENT_SCAN.
- Evidence: Final Bandit metrics reported Medium: 0.

## Medium Finding #3

- Classification: NOT_PRESENT_IN_CURRENT_SCAN.
- Evidence: Final Bandit metrics reported Medium: 0.

## Final Result

- `python -m bandit -c pyproject.toml -r src` exits 1 because 253 Low findings remain in inherited code.
- Medium: 0, High: 0 after the fix.
