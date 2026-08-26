# HunterX v0.5.2 RC2 — Implementation Diff from Round-1 RC

## Base and scope

Round 2 was developed directly from the v0.5.2 Round-1 RC, not from v0.5.1.

- Source base: `hunterX_source_0.5.2_rc.zip`
- Source base SHA-256:
  `ca4c28ca0df8c3054507233e3f6e20777bc0a0ead1b9bb0cec308cba2b4f2b04`
- Windows overlay base: `hunterX_windows_0.5.2_rc.zip`
- Windows base SHA-256:
  `b593dc3899316a4700d425461ac9413610bad04f462839d2d93b2fcc179f26ed`
- Frozen Round-1 Git commit:
  `29380aa682907b916a60a1d0d1960fbb181b2a60`

The platform selector, quantity, CAPTCHA/manual-input, click, queue, checkout,
and payment algorithms were not redesigned. Changes are limited to direct
Round-2 lifecycle, ownership, recovery, observability, integration, and release
verification findings.

## Runtime ownership changes

### One authoritative production iteration

`src/nodriver_tixcraft.py` now exposes one `run_runtime_iteration` used by the
normal application loop and `scripts/v052_browser_soak.py`. It owns real URL
observation, pause/refresh gates, PlatformEngine dispatch, all ten registered
platform families, attempt lifecycle, expected-progress observation, and
terminal exception propagation. The soak no longer simulates these transitions
with a second manual engine lifecycle.

### Attempt-scoped expected progress

`src/runtime_health.py` and `src/reload_guard.py` add a passive expectation
model keyed by tab identity, attempt ID/generation, action owner/token, accepted
transition, and deadline. It does not create a second refresh or submission
owner. Protected/unknown submit expiry remains fail-closed and read-only;
existing recovery owners perform any reacquire/rebind/restart action.

### Browser target and bootstrap ownership

`src/browser_session.py` and `src/nodriver_tixcraft.py` now:

- reject empty, wrong-event, and ambiguous duplicate target recovery;
- permit a route advance only through the saved owned target ID;
- prove target transport with a bounded read-only CDP query before success;
- commit `active_tab` only after identity and transport proof;
- share the complete initial/restart bootstrap sequence;
- preserve login return intent through a restart;
- expose bounded passive diagnostics for extra same-platform tabs.

The product contract is one automated tab per named HunterX instance. Same-
browser concurrent automation is not claimed.

## Platform lifecycle changes

### All registered families

Real adapter/PlatformEngine tests define the authoritative rearm matrix:

- ACTIVITY, DATE, and AREA may rearm only with adapter and positive safe-state
  evidence;
- TICKET, ORDER, CHECKOUT, PAYMENT, QUEUE, and UNKNOWN are protected;
- ticket-named paths rearm only when the adapter classifies them as AREA;
- every platform's sticky data resets in-place without replacing the central
  ownership mapping.

### TicketPlus

- A submission watcher snapshots its exact attempt/token and revalidates after
  every browser await before any mutation.
- A stale watcher emits a bounded diagnostic and cannot clear, retry, mark
  unknown, or cancel the next attempt's expected progress.
- The pre-probe 50 ms path returns before central-owner lookup while no deadline
  or probe is due; ownership is still checked before every mutation/await.

### TixCraft

- Every explicit tab resolves to per-tab PlatformEngine state; process fallback
  remains only for direct legacy `tab=None` calls with a diagnostic.
- The inner attempt carries the exact central attempt ID/generation/token and
  positive safe-rearm proof.
- Route text alone cannot clear submit ownership; confirmed interactive AREA
  evidence rearms both lifecycles together.
- Confirmed rejection releases only the same attempt ID/generation/token and
  matching proof. Ambiguous and stale reset paths preserve the fence.
- The global CDP alert handler snapshots immutable submit context when the event
  is delivered, before coroutine scheduling. Delayed captcha/unknown callbacks
  cannot mutate a newly armed attempt; a current exact callback remains live.

### Cityline

Popup adoption rebinds dispatch state to the adopted tab before subsequent
platform state changes, preventing the new-tab flow from mutating the old tab's
mapping.

## Cross-platform user dictionary changes

`src/util.py` now owns one lossless dictionary parser and serializer. It accepts
the legacy quoted fragment, a JSON array, newline, ASCII/full-width semicolon or
list input; preserves commas, quotes and backslashes; trims empty entries;
deduplicates stably; reads the complete UTF-8/UTF-8-BOM online file; and merges
local answers before online answers. `src/settings.py` and the settings frontend
use the same contract at migration, display, save and runtime hot-reload
boundaries.

Production handlers in the TixCraft family, KKTIX, FamiTicket, iBon, KHAM,
TicketPlus and HKTicketing families now consume decoded shared-parser answers.
JavaScript-bound values are JSON encoded instead of interpolated. FamiTicket's
invalid `area_auto_select.area_answer` lookup and raw HKTicketing/iBon splitting
were removed. Registry coverage now includes TeamEar and Ticketek Australia.

Cityline, FunOne and FANSI GO have no text-question dictionary field and were
not given an unrelated CAPTCHA/login/queue/risk/payment integration.

## Terminal exception changes

`scripts/audit_browser_exception_handlers.py` performs a source AST audit of
broad handlers containing browser/CDP awaits. Audited handlers must call the
terminal browser-error classifier as their first effective action and may then
retain their original ordinary DOM/unsupported-operation fallback. Round 2
converged 466/466 audited handlers with zero manual-review dispositions.

## Test and integration changes

Round 2 adds direct negative controls and regressions for:

- expected-progress arm/observe/expiry and false-positive boundaries;
- real production iteration ownership for every registered family;
- wrong-event/empty/duplicate recovery and dead cached transport;
- full restart bootstrap parity;
- real ten-platform route/sticky-state reset;
- TixCraft local/central exact rejection, stale token, and delayed callback;
- TicketPlus stale watcher scheduling;
- terminal exception escalation;
- lossless cross-platform user dictionary parsing, settings round-trip, online
  multi-line merge, hot reload and actual answer-field production paths;
- source/Windows RC2 builder, provenance, joint parity, and packaged smoke.

No failure was converted to PASS with skip, xfail, deletion, assertion
weakening, or a fixture that avoids the production branch. Detailed loops are
recorded in `ROUND2_FAILURE_FIX_LOG_v0.5.2.md`.

## Release-chain changes

The RC2 release path is fail-closed around:

- one clean, committed, full 40-hex source snapshot shared by both artifacts;
- `_rc2` artifact naming and prohibition on FINAL branding;
- the exact Round-1 RC Windows base name and SHA-256;
- source metadata version equality;
- exact parity between source `src/**` and both embedded Windows `app_src`
  trees;
- fresh-extract packaged executable/settings smoke;
- qualifier-specific required reports and provenance;
- prerelease-only workflow semantics.

The exact RC2 source commit and final artifact hashes are recorded in the
co-delivered provenance/build information and SHA-256 manifest.

## Deliberate limitations

- No CAPTCHA, Queue-it, challenge, risk-control, checkout, payment, or
  fraud-control bypass was added.
- Local actual-browser integration uses synthetic loopback pages.
- `8H SOAK NOT VERIFIED`; filenames and release status are RC2, never FINAL.
