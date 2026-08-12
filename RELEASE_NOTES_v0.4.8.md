# HunterX v0.4.8 release notes

HunterX v0.4.8 is a deterministic refresh, long-run liveness and selective
upstream-integration release based on HunterX v0.4.7. It keeps the existing
per-platform purchase handlers and manual CAPTCHA/payment handoff.

## Why refreshes looked late or random

The sale-boundary loop could start a synchronous CDP/DOM health probe at the
same moment as the target, delaying the actual reload dispatch. Periodic
reload decisions also lived in several platform loops, and legacy calls did
not always pass their interval config to the common reload guard.

v0.4.8 adds one `RefreshCoordinator` per tab. It uses a monotonic nanosecond
clock, defines the configured interval as a minimum between dispatch starts,
coalesces a periodic intent into an armed scheduled intent, never catches up
missed intervals, and records bounded structured decisions. The absolute sale
time keeps milliseconds, is interpreted in `Asia/Taipei` by default and is
mapped to a frozen monotonic one-shot deadline. No new DOM/CDP probe begins
after that deadline on the critical path.

## TixCraft-family white-screen recovery

The long-run one-second refresh was caused by an inconclusive/failed recovery
path that replaced the configured wait with `current + 1.0`. A confirmed
incident now owns one immutable wait value and deadline. Repeated observations
cannot shorten or extend it; periodic/no-ticket intents are suppressed while
waiting; expiry permits one guarded recovery; an unsuccessful recovery starts
a new full wait. TixCraft state remains PlatformEngine-owned and per tab.
Ticketmaster keeps its separate family behavior and does not inherit the
custom TixCraft/Indievox delay merely because it shares handlers.

## Zendriver listener continuity

Zendriver 0.15.3 can receive a late Chrome DevTools Protocol reply after its
waiting Future was cancelled. Its original transaction callback then calls
`set_result`/`set_exception` on an already-finished Future, raises
`InvalidStateError`, and terminates the only listener task. The visible
`StopIteration` in the traceback is the coroutine return carrying the reply;
the fatal condition is the subsequent invalid Future transition.

v0.4.8 installs a small, idempotent guard before any browser configuration is
built. It discards only replies whose transaction is already done or cancelled,
keeps live reply/error behavior unchanged, and re-raises an invalid transition
for a still-live transaction. A real Zendriver Listener stress fixture sends
2,000 alternating late result/error messages, then a normal live result, and
verifies that the listener remains active and its transaction map is empty.

## On-sale and leak-watch modes

Both modes use the same date, area, quantity, qualification, verification,
submit, transition and checkout-handoff handlers.

- `onsale`: validates and arms the absolute sale deadline, performs the single
  scheduled refresh when configured, then immediately uses the shared purchase
  flow. Its normal reload interval is `auto_reload_page_interval`.
- `leak_watch`: uses `leak_refresh_interval_seconds` only on classified safe
  pages. A completed no-ticket scan consumes the current document generation;
  the same unchanged DOM is not fully rescanned until navigation/reload creates
  a new generation. While explicitly waiting on a consumed TixCraft area
  document it also uses the cached classified URL, avoiding a hidden per-tick
  `location.href` CDP call. Fresh documents are inspected once; queue,
  challenge and purchase pages remain protected.

Neither mode bypasses CAPTCHA, waiting rooms, Cloudflare, risk controls or
payment. No software setting can guarantee ticket inventory or a successful
purchase.

## Platform corrections

- Ticketmaster detail routes enter the date handler. Quantity is exact by
  default; `allow_less_tickets` chooses the largest smaller value and never a
  larger value. Repeated stale ticket-list failures request a throttled reload.
- KKTIX handles dynamically inserted qualification fields inside the selected
  ticket unit in a strict sequence, then rechecks form readiness before next.
  Login diagnostics distinguish waiting room, manual challenge, missing
  fields, missing submit selector and browser/CDP failure.
- Cityline and Ticketmaster close only tabs registered as bot-created; manual
  user tabs are ignored.
- iBon adds only two exact DMP event-collector patterns. Ticket, checkout and
  payment APIs are not broadly blocked.
- All legacy platform reload calls inherit the current per-tab config snapshot,
  so the common coordinator enforces the same interval invariant.

## Configuration migration

- New profiles exclude `愛心` tickets by default. Existing
  `keyword_exclude` values are preserved exactly.
- Dead `auto_reload_overheat_count` and `auto_reload_overheat_cd` keys are
  removed during migration and cannot impose a hidden one-second fallback.
- Missing `refresh_calibration` data is backfilled with timezone
  `Asia/Taipei`; deprecated early-trigger calibration remains disabled.
- Launchers use an argument vector with `shell=False`, including paths with
  spaces and shell metacharacters.

## Selective upstream work

This release reviewed `bouob/tickets_hunter` v2026.08.07, integration commit
`96c1c14ec13c1149d7ae60dfc94e9ebdb37142b4`, and release HEAD
`5793ee6daba562c42c902a1b84625528e421f849`. Applicable behavior was rewritten
for HunterX rather than merged wholesale. HunterX v0.4.7 submit-race guards,
per-tab ownership, bounded schedulers, post-submit protection and release
safety remain authoritative. Upstream authors and contributors retain credit
under the repository's GPL terms.

See `reports/v0.4.8-gap-analysis.md` for the decision table and
`docs/04-implementation/platform-capability-matrix-v0.4.8.md` for platform
coverage.

## Validation and limits

The release gate uses compileall, Ruff, focused strict-mypy for the new typed
core, pytest, pip-audit, Bandit, deterministic/long-run scheduler fixtures,
performance comparison, PyInstaller build, archive verification and packaged
smoke tests. Exact final counts, timings, hashes and any environment-specific
limitations are recorded in `reports/v0.4.8-validation.md` and the checksum
manifest shipped beside the archives.

No live ticket order, CAPTCHA/queue bypass or payment was attempted. Site DOM,
inventory, load, network path, browser version and Windows scheduling can still
change real-world results. Follow `guide/v0.4.8-operations.md` and perform the
listed manual read-only preflight before an important sale.
