# HunterX v0.4.9 platform capability matrix

`PlatformSpec` is the authoritative key/display-name/hostname source. Runtime
capabilities and validation evidence are separate types. Site-specific purchase
handlers remain in `src/platforms/*.py`; registration is not proof that a live
selector has not changed.

| Priority | Family | Existing flow retained | Shared v0.4.9 invariants | Evidence |
|---|---|---|---|---|
| A | TixCraft / IndieVox / Ticketmaster | activity/date/area/ticket, quantity, CAPTCHA/manual handoff | attempt-scoped evidence, confirmed AREA recovery, protected submit, bounded readiness/OCR | production handlers plus attempt, race, soft-block, timing and soak fixtures |
| A | TicketPlus / 遠大 | activity/order, keyword/fallback/count, member questions | per-tab state, safe/protected route policy, shared coordinator | route/state and cross-platform fixtures |
| A | KKTIX | event/registration, qualification/member code/next | waiting room protected, bounded readiness, per-tab state | dynamic-form, error-state and route fixtures |
| A | KHAM / ticket.com.tw / UDN | product/performance/seat/cart variants | family mapping without layout over-unification, shared coordinator | selector/state and cross-platform fixtures |
| A | HKTicketing / Galaxy / Ticketek | event/performance/selection/type02 | traffic/challenge protected, per-tab state | adapter and route/state fixtures |
| B | iBon | activity/performance/EventBuy/tour | queue/challenge protected, exact network blocking | regression fixtures |
| B | FamiTicket | activity/date/ticket/area | shared safe refresh and per-tab ownership | regression fixtures |
| B | Cityline | event/performance/area/count | only bot-owned popups may be closed | popup/route fixtures |
| B | FunOne | event/sales/count/OCR fallback | bounded polling and shared coordinator | regression fixtures |
| B | FANSI GO | event/show/section/API/DOM | explicit non-success and shared coordinator | regression fixtures |

Across every family, on-sale and leak-watch modes enter the same purchase
handlers. Their differences are scheduling and scan policy. Queue/challenge,
TICKET, ORDER, CHECKOUT, PAYMENT and unknown unsafe routes fail closed against
background refresh. No family claims automated payment, CAPTCHA/queue bypass,
risk-control evasion or guaranteed purchase success.
