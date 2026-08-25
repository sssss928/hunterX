# HunterX v0.5.2 Final Implementation Diff

## Changed

- Restored the intended indentation and synchronization scope of one settings-server shutdown regression test.
- CI and Release now compile `src tests scripts` and CI invokes pytest through the selected interpreter with `python -m pytest`.
- CI benchmark invocation also uses `python -m pytest`.
- Restored omitted historical release-contract reports from the immutable RC3 commit.
- Added this pre-final evidence set to the RC3 Windows required-document manifest and verifier.
- Added workflow contract assertions for full Python compilation and module-based pytest invocation.

## Deliberately unchanged

No file under `src/**` changed. Purchase selection, refresh cadence, attempt scope, submit ownership, browser recovery, CAPTCHA/manual handling, profiles, named-instance behavior, notification delivery, and platform adapters retain the RC3 implementation.

## Safety rationale

The only reproduced code failure was in test/workflow/release evidence plumbing. Dictionary and performance production defects were not reproduced, so changing production code would have violated the minimal-fix and no-regression requirements.
