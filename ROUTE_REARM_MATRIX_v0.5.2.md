# HunterX v0.5.2 RC2 Route and Rearm Matrix

This is the release-root copy of the authoritative Round-2 route lifecycle
contract. `PageClass.TICKET` is protected and is never, by itself, proof that a
completed attempt may rearm.

## Central contract

| Page class | Start an initial attempt | Rearm generation N+1 | Automatic inventory refresh | Contract |
|---|---:|---:|---:|---|
| `ACTIVITY` | Yes | Yes | Adapter cadence permits it | Safe purchase context |
| `DATE` | Yes | Yes | Adapter cadence permits it | Safe purchase context |
| `AREA` | Yes | Yes | Adapter cadence permits it | Safe purchase context |
| `TICKET` | Yes when first entered | **No** | **No** | Protected ticket form; existing attempt only |
| `ORDER` | Yes when first entered | **No** | **No** | Protected post-selection/submit context |
| `CHECKOUT` | Yes when first entered | **No** | **No** | Protected transaction context |
| `PAYMENT` | Yes when first entered | **No** | **No** | Protected manual payment handoff |
| `QUEUE` | May retain/start the exact observed attempt | **No** | **No** | Provider-controlled; read-only observation |
| `UNKNOWN` | No | **No** | **No** | Known platform host without positive safe-route proof |
| `HOME` | No | No | Not a purchase-attempt route | Normal browsing/login context only |

`SOLD_OUT`, `REJECTED_ERROR`, `CANCELED_ORDER` and `CONTINUE_SHOPPING` are
outcome evidence, not safe rearm proof. A new attempt begins only after the
adapter subsequently classifies the actual route as `ACTIVITY`, `DATE` or
`AREA`.

## Adapter-specific routes

| Registry family | Safe route examples | Protected route examples | Important detail |
|---|---|---|---|
| TixCraft / TeamEar / IndieVox / Ticketmaster | activity detail/game and `/ticket/area/` | ticket/check-captcha/verify/order, checkout/payment | A TixCraft `PageClass.TICKET` never rearms |
| KKTIX | event/date and `/registrations/new` | orders, non-new registrations, checkout/payment | A fragment alone is not positive safe-route evidence |
| TicketPlus | activity and `/order/...` inventory | ticket, confirm/confirmseat, checkout/payment | TicketPlus inventory “order” is adapter-classified AREA |
| iBon | activity/event/performance and inventory `/ticket/` | order/checkout/payment | iBon `/ticket/` is adapter-classified AREA |
| KHAM / ticket.com.tw / UDN | event/product/activity/performance/salestable | ticketseat/seat, UTK0206/order/checkout/payment | Longest matching route rule wins |
| FamiTicket | activity and inventory `/ticket` | order/checkout/payment | FamiTicket `/ticket` is adapter-classified AREA |
| FunOne | events/sales and inventory `/ticket` | orders/checkout/payment | FunOne `/ticket` is adapter-classified AREA |
| FANSI GO | event and inventory `/ticket` | orders/checkout/payment | FANSI GO `/ticket` is adapter-classified AREA |
| Cityline | event/performance/UTS inventory | secure selection, order/checkout/payment | Cityline secure selection is protected |
| HKTicketing / Galaxy Macau / Ticketek | event/performance/secure selection | order/checkout/payment | Secure selection is AREA for this adapter family |

Explicit queue routes are `QUEUE`. Unmatched paths on a registered host are
`UNKNOWN`; both fail closed for reload and rearm.

## Ownership invariants

1. A protected/completed generation retains its identity on TICKET, ORDER,
   CHECKOUT, PAYMENT, QUEUE and UNKNOWN.
2. Positive ACTIVITY, DATE or AREA evidence clears attempt-local platform data,
   resets the existing refresh purchase guard and creates exactly one N+1.
3. The existing `PlatformRuntimeState`, `ReloadGuard`, `LeakWatchScheduler` and
   `RefreshCoordinator` remain the only owners; no parallel state system exists.
4. Delayed watchers/callbacks must still prove their original attempt ID,
   generation and token after browser awaits. A stale callback may diagnose and
   exit, but cannot mutate N+1.

## Verification

`tests/test_v052_round2_route_state_matrix.py` exercises 106 direct cases using
real adapters/initializers for all ten registry families, including safe rearm,
protected negative controls, ticket-named AREA contrasts and in-place sticky
state reset. Fresh-process repetition executed 1,060 case invocations. These
tests do not claim live third-party purchase, payment, CAPTCHA, queue,
challenge or risk-control validation.
