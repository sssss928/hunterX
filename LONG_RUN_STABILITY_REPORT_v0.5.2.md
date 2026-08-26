# HunterX v0.5.2 Long-Run Stability Report

Release status: **FINAL delivery by explicit user waiver**
Mandatory qualifier: **BOTH 8H ACTUAL-BROWSER GATES USER WAIVED / NOT VERIFIED**

The final qualification run was stopped at the user's request after 2,052.408
seconds (about 34 minutes 12 seconds). All remaining qualifying soak processes
were terminated and both captured stderr files were empty. This partial result
is diagnostic evidence only and is not an eight-hour PASS. Historical shorter
soaks below remain valid for their stated durations.

## Executed real-browser scope

Microsoft Edge was driven through the packaged Zendriver/CDP stack against a local synthetic ticket SPA. The fixture exercised push/replace history, DOM rerender, activity/area/protected transitions, success-to-continue, login-return, reload and target replacement. It never contacted a ticketing service.

### Final post-fix single instance — 600.062 seconds

- 7,649 cycles; 25 target replacements; 25 reloads.
- 306 success → safe-route continuation cycles; 414 login restore cycles; 305 selector fallbacks.
- Duplicate submit claims: 0; general errors: 0; CDP errors: 0.
- Tabs max: 1; asyncio tasks start/end/max: 8/8/8.
- CDP mapper start/end/max: 0/0/0.
- HunterX RSS: 95.00 → 100.22 MiB; quarterly means 96.45/98.08/98.69/99.59 MiB; final 100-sample delta +1.30 MiB.
- Browser-process-tree RSS final 100-sample delta: -4.69 MiB.
- Maximum observed progress gap: 0.672 seconds.

### Final post-fix three instances — 300.031 seconds each

- 11,780 aggregate cycles; 39 target replacements; 39 reloads.
- 471 success → continue; 638 login restore; 471 fallback resolutions.
- Duplicate claims/errors/CDP errors: 0/0/0.
- Each instance held at most one tab.
- Shared asyncio task count max: 24; instances ended at 22, then 15, then 8 as each browser stopped. This is teardown convergence, not monotonic growth.
- Every instance reported CDP mapper start/end/max 0/0/0.

### Accelerated lifecycle soak

- 100,000 cycles across three engines and ten platform registry families.
- Formal and leak-watch modes alternated.
- Duplicate submit claims: 0.
- Final attempt state count: 30; final refresh owner count: 30 — exactly 3 engines × 10 platforms, not proportional to cycle count.
- Peak Python traced allocation: 1,201,460 bytes.
- Elapsed time: 256.709 seconds.

## Root causes found and fixed

1. **Windows monitoring type-cache leak.** The fallback RSS sampler recreated ctypes structures and rebound function signatures on each sample and for each Edge child. ctypes caches this metadata. `_windows_process_api()` is now single-entry cached. A post-fix microstress of 20,000 own-RSS reads plus 2,000 process-tree enumerations increased RSS only 696,320 bytes and returned the same cached API identity.
2. **Zendriver write-only event retention.** Zendriver inserted each parsed `EventTransaction` into `Connection.mapper`, while only command responses were popped. Events have no response and the listener dispatches its local event object directly, so the map grew with browser event volume. HunterX's existing Zendriver hardening layer now installs `_PendingTransactionMap`, retaining request transactions and discarding event transactions. BASE retained 2,000/2,000 negative-control events; candidate actual-browser mapper counts remained 0.
3. **Task/action/state ownership risks.** Production `asyncio.create_task` sites were routed through the bounded task registry; action ownership keeps exact tab/token identity; weak and fallback state stores are identity safe and capacity bounded.
4. **Target/CDP lifecycle gaps.** Empty URL, stale target, execution-context loss, transport close, confirmed crash, clean user close and ambiguous exit now have different recovery decisions. Recovery is bounded and fail-closed whenever submit outcome is unknown.

## Investigative runs retained

- First requested 15-minute run: failed harness control with `ConnectionClosedError`; not counted as a product pass.
- Corrected 15-minute single run: 11,603 cycles, 0 errors/duplicates.
- 15-minute three-instance behavioral run: 35,447 cycles, 0 errors/duplicates.
- Two 15-minute metric runs were used to discover the ctypes and Zendriver retention causes. Their behavior passed, but their RSS evidence is intentionally retained as failure/investigation evidence rather than final stability proof.
- A 60-second post-mapper-fix smoke and the final 10-minute/5-minute runs verified bounded mapper/task/tab state.

## Conclusion and limitation

The executed deterministic, fault-injection and local real-browser durations show no duplicate submit, permanent stall, unbounded attempt/refresh-owner/task/action/CDP-map growth, or browser reopening after a manual close. Short-run Python allocator warmup remains visible and the required two eight-hour runs were not completed.

Therefore: **BOTH 8H ACTUAL-BROWSER GATES USER WAIVED / NOT VERIFIED**.
The user requested FINAL artifact naming despite that limitation. This release
must not be described as eight-hour-qualified; see `FINAL_8H_SOAK_WAIVER.json`.
