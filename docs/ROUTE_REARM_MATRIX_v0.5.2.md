# HunterX v0.5.2 Route and Rearm Matrix

This document is the authoritative Round 2 clarification of the v0.5.2
route lifecycle. It corrects the earlier shorthand
`activity/date/area/ticket safe route`: `PageClass.TICKET` is protected and is
never, by itself, proof that a completed attempt may rearm.

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
adapter subsequently classifies the real route as `ACTIVITY`, `DATE` or
`AREA`.

The words “ticket page” in a platform UI or URL are not a lifecycle class.
The adapter's `PageClass` is authoritative. This distinction lets inventory
selection pages whose paths happen to contain `ticket` remain safe while
keeping actual ticket forms protected.

## Adapter-specific routes

| Registry family | Safe route examples | Protected route examples | Important classification detail |
|---|---|---|---|
| TixCraft / IndieVox / Ticketmaster | `/activity/detail/` → ACTIVITY; `/activity/game/` → DATE; `/ticket/area/` → AREA | `/ticket/ticket/`, `/ticket/check-captcha/`, `/ticket/verify/` → TICKET; `/ticket/order` → ORDER; checkout/payment | A TixCraft `PageClass.TICKET` never rearms |
| KKTIX | `/events/...` → DATE; `/registrations/new` → AREA | `/orders/`, non-new `/registrations/`, checkout/payment | A fragment alone such as `#/booking` is not positive path evidence; an otherwise unclassified route remains UNKNOWN |
| TicketPlus | `/activity/` → DATE; `/order/...` → AREA | `/ticket/` → TICKET; `/confirm/`, `/confirmseat/`, checkout/payment | TicketPlus calls its inventory page “order”, but the adapter deliberately classifies it AREA |
| iBon | activity/activityinfo/event → DATE; performance and `/ticket/` → AREA | order/checkout/payment | iBon `/ticket/` is an inventory AREA, not `PageClass.TICKET` |
| KHAM / ticket.com.tw / UDN | `/event/` → ACTIVITY; product/activity → DATE; performance/salestable → AREA | ticketseat/seat.aspx → TICKET; UTK0206/order/checkout/payment | Longest matching route rule wins |
| FamiTicket | activity → DATE; `/home/activity` and `/ticket` → AREA | order/checkout/payment | FamiTicket `/ticket` is adapter-classified AREA |
| FunOne | events → DATE; sales and `/ticket` → AREA | orders/checkout/payment | FunOne `/ticket` is adapter-classified AREA |
| FANSI GO | events/event → DATE; `/ticket` → AREA | orders/checkout/payment | FANSI GO `/ticket` is adapter-classified AREA |
| Cityline | event → DATE; performance/UTS internet route → AREA | `/secure/selection` → TICKET; order/checkout/payment | Cityline secure selection is protected |
| HKTicketing / Galaxy Macau / Ticketek | events/event → DATE; performance and `/secure/selection` → AREA | order/checkout/payment | The same-looking secure-selection path is AREA for this family, unlike Cityline |

Every registered platform also classifies explicit queue routes as `QUEUE`.
Unmatched paths on a registered host are `UNKNOWN`; both are fail-closed for
reload and rearm.

## Attempt and state reset invariants

1. A protected or completed generation keeps the same attempt identity while
   it remains on TICKET, ORDER, CHECKOUT, PAYMENT, QUEUE or UNKNOWN.
2. A transition to ACTIVITY, DATE or AREA clears attempt-local platform data,
   resets the existing `RefreshCoordinator.purchase_guard`, and creates exactly
   one generation N+1.
3. The `PlatformRuntimeState`, `ReloadGuard`, `LeakWatchScheduler` and
   `RefreshCoordinator` objects remain the same owners; the platform data
   mapping is cleared in place and rebuilt by the real platform initializer.
4. A delayed submission watcher must prove its original attempt id and submit
   token are still current after every browser await. A stale callback may log
   once and exit, but cannot clear, retry or mark the new attempt unknown.

## Direct regression evidence

- `tests/test_v052_round2_route_state_matrix.py`
  - real adapter URL classification for all ten registry families;
  - ACTIVITY/DATE/AREA rearm and TICKET/ORDER/CHECKOUT/PAYMENT/QUEUE/UNKNOWN
    negative controls;
  - ticket-named AREA contrasts;
  - real initializer/sticky-state reset matrix for all ten families.
- `tests/test_v052_ticketplus_attempt_scope.py`
  - exact submit ownership;
  - unknown outcome fail-closed;
  - attempt-N stale watcher cannot mutate rearmed attempt N+1.

These are deterministic source/fixture tests. They do not claim live
third-party purchase, payment, queue, CAPTCHA, challenge or risk-control
validation.
