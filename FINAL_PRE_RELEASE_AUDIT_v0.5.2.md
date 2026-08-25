# HunterX v0.5.2 RC3 Historical Pre-Release Audit

> This is the preserved pre-waiver checkpoint. The authoritative delivery
> decision is `FINAL_RELEASE_AUDIT_v0.5.2.md`.

Audit date: 2026-08-25 (Asia/Taipei)

## Baseline

- Latest public GitHub `main` was frozen at `bbdd1f1a9dc02fef37da18a43e7f20f928b39ffd` in a new clean clone.
- Local RC2 and RC3 ZIPs were treated as immutable comparison evidence and re-hashed before use.
- No `src/**` production file was changed in this pre-final pass.

## Reproduced defects

1. `tests/test_settings_control_hardening.py` had a malformed function body. Ruff, full Python compilation, and pytest collection all independently failed at line 1170.
2. CI compiled only `src`, delaying syntax detection in `tests` and `scripts` until a later gate.
3. Later GitHub uploads omitted release-contract reports still required by the Windows staging code, including the user-dictionary acceptance report.
4. Windows CI and Release require `v0.5.2-rc2/hunterX_windows_0.5.2_rc2.zip`, but that prerelease is absent from the public repository.

## Claims independently checked

- User dictionary broad failure: **NOT REPRODUCED** in source/runtime tests or packaged source parity.
- Large RC2-to-RC3 performance regression: **NOT REPRODUCED** after balanced and high-density A/B repeats.
- RC3 terminal-browser lifecycle recovery: retained and regression-tested through the authoritative `_run_main` boundary.

## Release decision

At this historical checkpoint the source was suitable only for RC3/pre-final
status. It does not claim that later GitHub CI/Release has run or passed.

**8H SOAK NOT VERIFIED**

FINAL branding is forbidden until both mandatory 8-hour actual-browser gates and the current GitHub workflows genuinely pass.
