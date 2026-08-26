# HunterX v0.5.2 RC2 — Round 2 Long-Run Stability Report

Release status: **RC2**

> **8H SOAK NOT VERIFIED**

## Runtime architecture exercised

The application and local browser harness both call the one authoritative
`run_runtime_iteration`. The tested path includes actual URL observation,
PlatformEngine lifecycle, pause/refresh gates, all registered platform-family
dispatch branches, expected-progress observation, terminal exception
propagation and BrowserSessionManager ownership. The harness does not maintain
a second engine, refresh coordinator, leak scheduler, reload guard or submit
owner.

## Final post-dictionary Edge results

| Run | Duration / cycles | Duplicate submit | Errors / CDP | Tabs | Owned tasks | asyncio tasks | CDP mapper |
|---|---:|---:|---:|---:|---:|---:|---:|
| Single named instance | 183.187 s / 153 | 0 | 0 / 0 | max 1 | 0→0, max 0 | 8→8, max 8 | 0→0, max 0 |
| Instance 1 | 61.828 s / 69 | 0 | 0 / 0 | max 1 | 0→0, max 0 | 22→22, max 22 | 0→0, max 0 |
| Instance 2 | 62.672 s / 70 | 0 | 0 / 0 | max 1 | 0→0, max 0 | 22→15, max 22 | 0→0, max 0 |
| Instance 3 | 62.688 s / 70 | 0 | 0 / 0 | max 1 | 0→0, max 0 | 22→8, max 22 | 0→0, max 0 |

The single run recorded six success/continue cycles, nine login restores and six
fallback resolutions. It injected one guarded reload to exercise the existing
reload owner. The three-instance run recorded three success/continue cycles,
four login restores and two fallback resolutions per instance. Recovery count
was zero because no injected target/transport failure was requested in these
final runs; targeted fault-injection tests separately exercise those paths.

The three instance samples share one Python event loop, so their asyncio counts
are process-wide snapshots and must not be interpreted as isolated per-driver
counts. Each instance owned a separate Edge driver/profile and at most one
automated tab. Same-browser concurrent multi-tab automation is intentionally not
claimed.

## Long-run root causes addressed

- user/manual closure is separated from confirmed target/transport/browser
  failure, so closing a browser does not reopen repeated pages;
- recovery proves exact target identity and live target transport before
  committing a tab;
- restart replays the same full bootstrap as initial launch;
- expected transitions are attempt/tab/owner/token scoped rather than inferred
  from loop activity;
- terminal browser exceptions propagate instead of being converted to ordinary
  DOM fallbacks;
- production async work and browser actions have explicit bounded ownership;
- Zendriver write-only events and Windows process sampling do not accumulate
  per cycle;
- TicketPlus and TixCraft delayed callbacks cannot mutate a later attempt.

## Evidence limitations

All actual-browser runs used Microsoft Edge against a local loopback synthetic
SPA. No third-party ticketing site, live inventory, submit, payment, CAPTCHA,
Queue-it, challenge or risk-control operation was exercised. Packet-level
capture was not performed.

The required eight-hour single-instance and eight-hour three-instance actual-
browser soaks were not completed. Therefore:

> **8H SOAK NOT VERIFIED — RC2 ONLY, NOT FINAL**
