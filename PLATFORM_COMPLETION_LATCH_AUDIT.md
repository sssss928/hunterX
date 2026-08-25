# HunterX v0.5.2 Platform Completion Latch Audit

Release status: **RC**
Long-run qualifier: **8H SOAK NOT VERIFIED**

## Root cause

The cross-platform defect had one common lifecycle gap and one TicketPlus-specific latch.

- Common gap: platform dictionaries and local success/checkout flags were retained while a tab remained alive, but there was no authoritative attempt boundary connecting `success -> protected route -> adapter-confirmed ACTIVITY/DATE/AREA route`. Returning to a legitimate purchase route could therefore inherit terminal state from the previous purchase. `PageClass.TICKET` is protected, not safe.
- TicketPlus-specific gap: `nodriver_tixcraft.py` additionally kept the process-local `ticketplus_purchase_done` Boolean. Once set, it disabled TicketPlus automation for the process rather than only for the completed attempt.
- Submission ambiguity gap: a transport failure after a click could leave the caller unable to distinguish "not submitted" from "submitted but response not observed". Retrying in that state could duplicate a transaction.
- Registry gap: fallback storage keyed only by raw object id could theoretically return state belonging to a recycled Python object id.

## Authoritative v0.5.2 lifecycle

`PlatformEngine` remains the sole per-tab platform owner. It now owns one immutable `PurchaseAttempt` per tab and platform family, with monotonic generation, route identity and attempt-scoped submit ownership.

1. Entering a safe purchase route starts generation 1, or rearms generation N+1 after a terminal/protected attempt.
2. A submit owner may claim that exact attempt once.
3. Confirmed completion disables automation only for that attempt.
4. Remaining on protected/queue/order/checkout/payment routes never starts a new attempt and never permits another submit.
5. A confirmed return to an `ACTIVITY`, `DATE` or `AREA` route clears attempt-local platform flags and starts a new generation. A ticket-named path qualifies only if that adapter classifies it as `AREA`; `PageClass.TICKET` never qualifies.
6. An unknown submit outcome enters `SUBMIT_OUTCOME_UNKNOWN`; it is fail-closed until a confirmed safe route creates a new attempt.

No CAPTCHA, Queue-it, challenge, risk-control or payment bypass was added.

## Platform matrix

| Registry family | Included sites | Previous completion ownership | v0.5.2 behavior | Direct evidence |
|---|---|---|---|---|
| `tixcraft` | TixCraft, IndieVox, Ticketmaster family | Per-tab platform dictionary, without a common attempt transition | Existing selection flow retained; terminal flags reset only when the engine confirms a new safe-route generation | Adapter matrix plus explicit `test_tixcraft_family_hosts_rearm_attempt_independently[indievox/ticketmaster]` |
| `kktix` | KKTIX | Per-tab platform dictionary and existing KKTIX lifecycle | Existing KKTIX route/login ownership retained; common attempt generation prevents stale terminal state | Same matrix test `[kktix]` plus adjacent KKTIX regressions |
| `famiticket` | FamiTicket | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[famiticket]` |
| `ibon` | iBon | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[ibon]` |
| `kham` | KHAM, ticket.com.tw, UDN family | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[kham]` |
| `ticketplus` | TicketPlus | Per-tab state plus process-global `ticketplus_purchase_done` | Global latch removed; watcher claims exact attempt; unknown result fails closed; safe route rearms | Matrix test, `test_v052_ticketplus_attempt_scope.py` |
| `cityline` | Cityline | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[cityline]` |
| `hkticketing` | HKTicketing, Galaxy Macau, Ticketek family | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[hkticketing]` |
| `funone` | FunOne | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[funone]` |
| `fansigo` | FANSI GO | Per-tab local state | Attempt-scoped through the existing engine-owned state proxy | Same matrix test `[fansigo]` |

IndieVox is explicitly covered by the `tixcraft` registry family and shares the same attempt lifecycle; its inherited site-specific purchase selectors were not replaced.

## Duplicate-submit proof

- Submit tokens contain attempt identity and generation.
- A second claim in the same attempt is rejected.
- Terminal and protected states reject claims.
- Stale tokens cannot control a newer generation.
- Unknown post-click outcomes are not retried automatically.
- A new claim is possible only after the route classifier and platform adapter agree that the tab is back on a safe purchase route.

## Negative control

The new lifecycle tests were executed against immutable BASE-v0.5.1. The base failed the cross-platform negative control (14 failures), and the TicketPlus-specific negative control failed two tests. This demonstrates that the v0.5.2 regression tests detect behavior absent from the base rather than documenting an already-passing capability.
