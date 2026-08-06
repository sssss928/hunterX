# HunterX v0.4.7 release notes

HunterX v0.4.7 is a correctness and release-hardening update based on v0.4.6
(`9e2a25908426837d8c1645af6bd27eaa4fd9c6c7`).

## TixCraft post-submit recovery fix

The failure was a state-ordering race, not an area-sort bug. Enter could submit
the form and start a slow order transition, while `captcha_submit_until`
expired after 1.5 seconds. The next main-loop pass could read the old ticket
URL/DOM, a spinner, a blank overlay, or a temporary CDP timeout before the
order-processing detector ran. The stale ticket-count or soft-block path then
called recovery and navigated to the remembered area page.

v0.4.7 persists the attempt and submit-in-flight context before Enter
`keyDown`. That context includes tab identity, attempt ID, flow generation,
token and monotonic start time. Ticket/date/area/reload/recovery work now fails
closed during the protected transition. The short captcha timer only prevents
duplicate input; its expiry is never failure evidence.

Recovery requires affirmative evidence: a confirmed sold-out/reselect state,
a known retryable alert, a completed captcha-error reset, a confirmed canceled
order/continue-shopping state, or two matching explicit soft-block probes.
Spinner, white/empty DOM, probe timeout, delayed URL/DOM convergence and a
single soft-block-like snapshot are inconclusive. Known order-processing DOM
always overrides soft-block-like presentation.

Keyword, multi-keyword and empty-keyword selection, plus top, bottom, center,
random and most-remaining ordering, now converge on the same provisional area
click, navigation confirmation, ticket form and submit protection lifecycle.
Manual captcha and OCR-without-auto-submit are protected before control returns
to the operator.

## Cross-platform contract hardening

Every registry platform now resolves legacy helper state through a per-tab,
per-family PlatformEngine mapping. All automatic `get` navigation in platform
modules uses the bounded, single-flight guarded path. Platform-specific DOM and
API selectors remain unchanged; only lifecycle ownership, state isolation,
refresh safety and diagnostics are shared.

Platform polling deadlines, retry/backoff windows, cooldowns, queue timing and
redirect/log throttles use monotonic clocks. Wall-clock time remains only where
an actual calendar timestamp or filesystem/server timestamp comparison is
required.

The audited families are TixCraft/IndieVox/Ticketmaster SG, TicketPlus, KKTIX,
KHAM/ticket.com.tw/UDN, iBon, Cityline, HKTicketing/Galaxy Macau/Ticketek,
FamiTicket, FunOne and FANSI GO. See
`docs/04-implementation/platform-capability-matrix-v0.4.7.md` for the detailed
support and validation matrix.

## Zendriver listener stability

Chrome may finish a CDP request after its awaiting task was cancelled during a
rapid navigation or document replacement. Zendriver 0.15.3 then tries to finish
the already-done Future and raises `asyncio.InvalidStateError` from
`Listener.listener_loop`, which can stop subsequent browser event processing.

HunterX now installs an idempotent transport-layer guard before browser startup.
It discards only late results or protocol errors belonging to an already-done
transaction. Pending transactions, successful results and unrelated protocol
errors retain Zendriver's original behavior. No platform handler, selection,
submission, recovery or notification flow is changed by this guard.

## Validation and limits

The release suite covers production handlers with unit/integration browser
fixtures, state-machine races, concurrent tabs, protected-route rules, release
archives and long-running schedulers. The critical regression is repeatedly
executed and the complete pytest suite is required to pass three consecutive
runs before packaging.

No real ticket order, queue bypass, captcha bypass, automated payment or other
transaction was performed. Public-site evidence in the registry is read-only;
source-reviewed and fixture-tested capabilities are labeled separately. Runtime
behavior can still be affected by future browser or ticket-site DOM changes.
