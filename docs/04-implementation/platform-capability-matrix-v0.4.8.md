# HunterX v0.4.8 platform capability and consistency matrix

`platform_registry.py` owns exact hostname matching, the declarative adapters
own safe/protected route policy, and `src/platforms/*.py` retains the real
site-specific purchase handlers. A family registration is not proof that every
live selector is current.

## Selection and purchase flow

| Family | Hosts / classifier | Login / waiting room | Date / area / quantity | Qualification / verification | Submit and checkout handoff | Evidence / known limits |
|---|---|---|---|---|---|---|
| TixCraft / TeamEar / Indievox | Exact TixCraft-family hosts; activity/date/area/ticket/order/checkout/payment classes | Existing session/login; Queue-it pauses manual refresh | Existing shared pipeline; exact count unless allow-less; all keyword/order modes | Existing CAPTCHA/OCR/manual guards; no bypass | Immutable submit context and post-submit guard; payment manual | Public route/source plus extensive synthetic submit, timing, soft-block and long-run tests; live DOM can change |
| Ticketmaster SG | `ticketmaster.sg`; detail/game/area/check-captcha routes | Existing login/session; Queue-it protected | Detail enters date handler; area map; exact/largest-smaller quantity; stale reload throttled | Existing captcha/promo handler, manual-safe | Same TixCraft attempt/checkout guard | Synthetic route, quantity, stale/guard coverage; custom TixCraft delay intentionally not applied |
| KKTIX | `kktix.com`, `kktix.cc` | Waiting room self-refreshes; challenge manual; explicit field/submit/CDP diagnostics | Event/date and registration ticket selection | Selected-unit qualification → enabled field → member code → recheck; invitation manual | Duplicate-next/order flags; payment manual | Synthetic dynamic form and diagnostics; no live registration submitted |
| TicketPlus / 遠大 | `ticketplus.com.tw`, `.com` | Existing account/session and queue-aware route policy | Activity/order layouts, keyword/fallback/order and quantity flow retained | Member/exclusive questions manual-safe when unanswered | Confirm/checkout/payment protected | Source and adapter/fixture regressions; live BigBang inventory/DOM not validated |
| KHAM / 寬宏 | `kham.com.tw` | Existing KHAM login | Product/performance/seat flows and ordering retained | Optional OCR/manual CAPTCHA | Cart/order/confirm/payment protected | Source/fixture and shared coordinator regressions; live seat map can change |
| ticket.com.tw / UDN | Exact `ticket.com.tw`, `tickets.udnfunlife.com` family hosts | Existing family-specific login | Performance/seat flows retained | Existing verification/manual behavior | Order/checkout/payment protected | Source/fixture; family-specific layouts remain separate |
| iBon | Exact iBon hosts | Existing account/session; queue/challenge protected | Activity/performance/EventBuy/tour selectors retained | Existing image/verification handling | Order/checkout/payment protected | Exact DMP endpoint fixture; no broad iBon API block |
| FamiTicket | Exact FamiTicket hosts | Existing account/session | Activity/date/ticket/area retained | Verification question handler | Order/checkout/payment protected | Source/fixture and shared coordinator coverage |
| Cityline | `cityline.com`, `.com.hk` | Login Turnstile/manual challenge retained | Event/performance/area/quantity retained | No captcha bypass | Basket/order/checkout/payment protected | Pre-existing user-tab fixture; bot-popup ownership only |
| HKTicketing / Galaxy / Ticketek | Exact registered family hosts | Existing login/traffic handling | Event/performance/selection/type02 retained | Robot/captcha manual-safe | Confirm/order/checkout/payment protected | Public/source route fixtures; live variants may diverge |
| FunOne | `tickets.funone.io` | Existing session/login | Event/sales/quantity handlers retained | Bounded optional OCR/manual fallback | Reservation/order/checkout/payment protected | Source/public fixtures and shared coordinator coverage |
| FANSI GO | `go.fansi.me` plus registered Cognito auth host | Existing Cognito login | Event/show/section/quantity retained | No purchase captcha claim | Checkout/order-result/payment protected | Source/fixture and shared coordinator coverage |

## Runtime policy by family

| Family | Scheduled refresh | On-sale mode | Leak-watch mode | Soft-block / backoff | Tab / disconnect behavior |
|---|---|---|---|---|---|
| TixCraft / Indievox | Aware millisecond one-shot; periodic intent coalesced | Pre-boundary gate then shared purchase pipeline | Safe area/date only; one completed no-ticket scan per document generation | `CLEAR → SUSPECTED → CONFIRMED_WAIT → RECOVERING`; immutable configured/default wait | PlatformEngine per-tab state; persistent empty URL stops safely |
| Ticketmaster | Same coordinator; detail route supported | Same purchase pipeline with strict quantity | Safe classified pages, common deterministic interval | Family evidence detection; no custom TixCraft delay inheritance | Bot-created popup registry; user tabs preserved |
| KKTIX | Queue-aware one-shot; no reload in detected waiting room | Same registration pipeline | Safe event/registration policy and per-tab scheduler | Queue/challenge/manual backoff | Per-tab state; explicit CDP/empty-field diagnostics |
| TicketPlus | Queue-aware one-shot | Existing platform pipeline | Adapter-safe activity routes; common per-tab scheduler | Known failure only; protected unknown state | Per-tab state; 30-second main-loop empty URL stop |
| KHAM / ticket / UDN | Millisecond one-shot on safe route | Existing family pipeline | Adapter-safe performance routes; common scheduler | Existing platform retry policy, no bypass | Per-tab family mapping; safe stop on disconnect |
| iBon | Queue-aware one-shot | Existing iBon pipeline | Safe activity/performance routes; common scheduler | Existing known-alert policy; challenge manual | Exact network blocks; per-tab state |
| FamiTicket | Queue-aware one-shot | Existing pipeline | Safe activity/ticket routes; common scheduler | Existing bounded retry policy | Per-tab state and safe stop |
| Cityline | Queue-aware one-shot | Existing pipeline | Safe event/performance routes; common scheduler | Modal/login outcomes explicit | Only owned new tab activated/closed; user tabs ignored |
| HKTicketing / Ticketek | Queue-aware one-shot | Existing legacy/type02 pipeline | Safe event/performance routes; common scheduler | Traffic/challenge remains protected | Per-tab state and safe stop |
| FunOne | Millisecond one-shot | Existing step pipeline | Safe event/sales routes; common scheduler | Bounded captcha/step retries | Per-tab state and safe stop |
| FANSI GO | Millisecond one-shot | Existing API/DOM pipeline | Safe event/show routes; common scheduler | Explicit API/DOM non-success | Per-tab state and safe stop |

## Cross-cutting invariants

- One `RefreshCoordinator` exists per tab. Every `tab.reload()` is reached
  through `guarded_reload`, the page/submit single-flight guard and the same
  deterministic minimum-interval clock.
- Order, checkout, payment, queue/challenge and manual handoff cancel pending
  refresh work. Unknown routes fail closed.
- On-sale and leak-watch select the same platform purchase handlers; only
  deadline, scan, priority and backoff policy differ.
- Coordinator trace, scheduler histories, ownership fallback and PlatformEngine
  fallback mappings are bounded; tab cleanup removes state.
- No platform claims automated payment, CAPTCHA/queue bypass, risk-control
  evasion, proxy/account pooling, bulk purchase or guaranteed success.
- Validation is offline/source/fixture unless explicitly called public-page
  read-only evidence. No real order or payment was executed for v0.4.8.
