# HunterX v0.5.2 RC Implementation Diff

Release qualifier: **8H SOAK NOT VERIFIED**

## Base and references

- Only product base: HunterX v0.5.1 source ZIP, SHA-256 `BEF25688229B58929623A5B0326F7B7F0E8755FEB9F4294ECC6FD6EFA50FE113`.
- Windows runtime base: HunterX v0.5.1 Windows ZIP, SHA-256 `6C9083D9F743AEA71C7CCAD399F363AFA5D8E4E1C671883D2A8092F9942CBD8B`.
- Read-only references: tickets_hunter 2026.08.07 and 2026.08.17. Their code was compared for platform behavior; no upstream module was copied wholesale.

## Production changes

- `src/attempt_lifecycle.py`: immutable attempt states, monotonic generations, exact-attempt submit leases, identity-safe fallback registry and fail-closed unknown outcomes.
- `src/platform_engine.py`: integrates attempt creation/rearm, safe/protected route transitions, SPA route generation and platform state reset into the existing authoritative engine.
- `src/platform_contract.py`: exposes attempt context to the existing adapter contract without replacing platform implementations.
- `src/nodriver_tixcraft.py`: removes the process-global TicketPlus completion latch; connects engine decisions, task ownership, browser recovery and terminal failure propagation to the existing main loop.
- `src/platforms/ticketplus.py`: exact-attempt submission claim, bounded unknown-outcome handling, expiring/rearmable login context and bounded target restore.
- `src/navigation_context.py`: canonical tab-scoped target and login intent.
- `src/browser_session.py`: explicit clean/manual, ambiguous, transport and crash exit categories; bounded reacquire, rebind and safe restart decisions.
- `src/runtime_health.py`: exception taxonomy, circuit breaker, bounded supervisor and redacted 1,000-event critical trace.
- `src/task_registry.py` and `src/runtime_diagnostics.py`: owned async tasks and low-frequency task/action/resource observations.
- `src/zendriver_hardening.py`: keeps the existing late-response guard and prevents Zendriver's write-only CDP event transactions from accumulating in its request mapper.
- `src/dom_drift.py`: high-confidence route-scoped fallback that detects candidates but never clicks autonomously.
- `src/nodriver_common.py`: classifies URL-read side-channel failures instead of treating every empty URL as the same 30-second terminal condition.
- `src/refresh_timing.py`: preserves timing semantics while accepting the attempt-aware ownership path.
- Version, settings UI, build scripts, archive verifier and package documentation moved to v0.5.2 RC and the verified v0.5.1 runtime base.

## New verification code

- Cross-platform lifecycle, TicketPlus submit scope, login target recovery, browser failure classification, three-tab/three-instance isolation, SPA/DOM drift, task/resource stability and debug-trace tests.
- Local synthetic ticket SPA fixture.
- Actual Edge/Zendriver browser soak and deterministic 100,000-cycle lifecycle soak tools.
- Twelve-scenario same-machine v0.5.1 versus v0.5.2 performance benchmark.
- Balanced A/B and B/A performance runs; immutable dispatch results and a stable-route fast path removed the initially observed dispatch regression.

## Deliberately unchanged core

Date, area, ticket, quantity and `allow_less_tickets` selection algorithms remain the inherited platform implementations. TixCraft, IndieVox, Ticketmaster, KKTIX and the other platform selector/click sequences were not rewritten. OCR/dictionary/notifications, user profiles, multi-instance launch behavior, CAPTCHA manual fallback and existing protected-page boundaries were retained. No CAPTCHA, queue, risk-control or payment bypass was introduced.

## Advisory decisions

Accepted from the supplied Gemini guidance: saved login target, post-login restore, SPA/navigation awareness, attempt rearm after safe-route return, tab-scoped state, bounded stale-element validation and additive lifecycle defense.

Rejected: body-wide high-frequency mutation observation, random jitter, global fixed click delay, hidden refresh-interval changes, background API keepalive claims, a second global state manager and all CAPTCHA/queue/challenge/risk/payment bypass ideas.

Accepted from upstream only as behavioral reference: TicketPlus DOM/route handling and the need to preserve activity context around login. Rejected wholesale upstream replacement because it would discard HunterX ownership, refresh, leak-watch, reload-guard, notification and multi-instance architecture.
