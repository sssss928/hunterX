# HunterX v0.5.2 Final-Layer Root Cause

## Decision

The Windows RC2 crash was reproduced at the authoritative production boundary and required a production change. The release therefore advances to **v0.5.2 RC3 — 8H SOAK NOT VERIFIED**.

## Root cause

Platform and browser helpers correctly classified terminal CDP/WebSocket failures and re-raised them. RC2's `run_runtime_iteration()` also correctly propagated them, but `_run_main()` had no terminal-browser-failure boundary around its sole authoritative iteration call. A TixCraft `/login` transition that encountered `websockets.exceptions.ConnectionClosedError` therefore escaped `_run_main()`, `runtime.main()`, and `asyncio.run()`, causing the frozen `nodriver_tixcraft.exe` process to terminate.

The real log proves that the selected browser target/transport became unavailable during the login transition. It does not prove that the entire browser process died. RC3 deliberately asks the existing `BrowserSessionManager` for the browser exit state and lets its bounded recovery ladder distinguish live-process target/transport recovery from a genuinely crashed browser.

## Minimal correction

`_run_main()` remains the sole production loop owner and now catches only the exception leaving the authoritative `run_runtime_iteration()` call. `_handle_terminal_iteration_failure()` immediately reclassifies it. Non-terminal exceptions still propagate. Terminal failures are evaluated against the last authoritative page class, current central attempt state, browser exit state, safe URL, and the existing health-supervisor recovery budget.

The possible outcomes are:

- manual/clean browser close: `STOP`; no reopen;
- protected, completed, unknown-submit, checkout, payment, or queue state: fail closed; no replay;
- safe HOME/ACTIVITY/DATE/AREA state: bounded reacquire or transport rebind;
- only after a failed live recovery, and only for a transport-closed safe state with an ALIVE/CRASHED browser classification: one existing full-bootstrap safe restart;
- exhausted recovery budget: controlled fail-closed exit, not an uncaught traceback.

The classifier was not removed and no broad `except: pass`, blind reload, unbounded retry, duplicate submission, or unknown-submit replay was introduced.

## Production-level negative control

The pre-fix test invokes `runtime.main -> _run_main -> run_runtime_iteration -> TixCraft production helper`. The helper raises a real `ConnectionClosedError`; immutable RC2 lets it escape `_run_main()`. The post-fix version consumes it only at the lifecycle owner and returns a bounded lifecycle result. Evidence is recorded under `work/v052/final-layer/evidence/p0_rc2_pre_fix_run_main.xml` and `p0_rc3_post_fix_run_main.xml`.

