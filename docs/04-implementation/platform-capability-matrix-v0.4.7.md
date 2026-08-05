# HunterX v0.4.7 platform capability and consistency matrix

This matrix distinguishes three questions:

1. `platform_registry.py` owns exact hostname-to-family matching and records
   validation evidence (`SOURCE_REVIEWED`, `FIXTURE_TESTED`, or
   `PUBLIC_PAGE_TESTED`).
2. `platform_adapters.py` and `platform_contract.py` describe common route and
   lifecycle semantics. Unknown routes are protected and fail closed.
3. `src/platforms/*.py` retains each site's real DOM/API handlers. A common
   contract does not imply common selectors or an unsupported site step.

## Identity, route and selection capabilities

| Family | Exact hostnames | Production handler | Activity/date/area/ticket routes | Captcha or verification | Order/checkout/payment | Area filtering and ordering |
|---|---|---|---|---|---|---|
| TixCraft family | `tixcraft.com`, `indievox.com`, `ticketmaster.sg` | `platforms.tixcraft.nodriver_tixcraft_main` | Supported: detail/game, area, ticket/verify/check-captcha | TixCraft/Ticketmaster image captcha; OCR optional; manual and non-auto OCR protected; no bypass | Supported and protected: order, checkout, payment, processing DOM | Empty/single/multiple AND/OR keyword groups, exclusion, optional fallback; top, bottom, center, random, most remaining |
| TicketPlus | `ticketplus.com.tw`, `ticketplus.com` | `platforms.ticketplus.nodriver_ticketplus_main` | Supported: activity, order-based area/ticket layouts | Verification/member/exclusive-code questions; manual-safe when no configured answer; no generic image-captcha claim | Supported: order, confirm/checkout, payment handoff | Multi-keyword priority, exclusion/fallback and configured ordering through unified selectors |
| KKTIX | `kktix.com`, `kktix.cc` | `platforms.kktix.nodriver_kktix_main` | Supported: event/date, registrations ticket/area, optional booking seat map | Custom registration question/captcha handler; no anti-bot bypass | Supported: registration/order and account order confirmation; payment remains user-owned | AND keyword matching, fallback and configured ordering |
| KHAM family | `kham.com.tw`, `ticket.com.tw`, `tickets.udnfunlife.com` | `platforms.kham.nodriver_kham_main` plus KHAM/UDN/ticket seat handlers | Supported: product/activity, performance/sales table, ticket seat flows | KHAM image captcha with optional OCR; UDN login reCAPTCHA remains manual | Supported: cart/order/confirm/payment routes; payment remains manual | Keyword priority, exclusion/fallback, configured order; family-specific seat-map handlers retained |
| iBon | `ibon.com.tw`, `ibon.com` | `platforms.ibon.nodriver_ibon_main` | Supported: activity/event, performance/ticket, EventBuy and tour variants | Image captcha with optional OCR plus card/verification questions | Supported: order/checkout/payment protected; checkout alerts restricted to known retryable failures | Keyword groups, exclusion/fallback and configured ordering across normal/EventBuy/tour layouts |
| Cityline | `cityline.com`, `cityline.com.hk` | `platforms.cityline.nodriver_cityline_main` | Supported: event/date, performance/selection, basket | No purchase image-captcha handler claimed; login Turnstile is a user/platform challenge | Basket/order/checkout/payment protected; notification supported | Keyword filtering and configured ordering |
| HKTicketing family | `hkticketing.com`, `galaxymacau.com`, `ticketek.com.sg`, `ticketek.com` | `platforms.hkticketing.nodriver_hkticketing_main` | Supported: event/date, performance/secure selection, type02 variants | Robot/captcha steps remain manual-safe; no captcha bypass claim | Confirm order, checkout and payment handoff supported and protected | Keyword sets, exclusion/fallback and configured ordering in both legacy and type02 layouts |
| FamiTicket | `famiticket.com.tw`, `famiticket.com` | `platforms.famiticket.nodriver_famiticket_main` | Supported: activity/date, ticket/area | Verification-question handler; not an image-captcha capability claim | Order/checkout/payment detection and notification supported | Keyword groups/AND configuration, fallback and configured ordering |
| FunOne | `tickets.funone.io` | `platforms.funone.nodriver_funone_main` | Supported: event/date, sales/ticket purchase steps | Base64 captcha detection, optional bounded OCR, manual fallback | Reservation/order/checkout/payment steps supported; payment remains manual | Keyword/fallback and configured ordering using FunOne step-specific APIs |
| FANSI GO | `go.fansi.me`, `fansidev.auth.ap-southeast-1.amazoncognito.com` | `platforms.fansigo.nodriver_fansigo_main` and sign-in handler | Supported: event/show/date/section/ticket | No purchase captcha handler claimed; Cognito authentication retained | Checkout and order-result/payment notification supported | Section keyword matching or configured ordering; quantity handled by show API/DOM |

## Shared safety, state and evidence

| Family | Refresh/reload eligibility | Submit/order protection and recovery | Alert/error classification | Runtime state | Validation and regression coverage |
|---|---|---|---|---|---|
| TixCraft family | Only classified activity/date/area; never queue, ticket, submit-in-flight, order, checkout, payment or unknown | Submit armed before automated Enter and before manual handoff; tab/attempt/generation/token checked; only affirmative failure evidence may recover | Captcha error resets; known sold-out/reselect alerts may recover; unknown alerts remain for manual action; soft block needs two matching positive observations | Per-tab PlatformEngine mapping plus per-attempt dataclass, submit/navigation tokens and bounded schedulers | Public host/route evidence, fixtures, core/navigation/refresh/soft-block/bounded/notification/100k soak tests, v0.4.7 matrix and 20-run gate |
| TicketPlus | Adapter safe pages plus platform-native inventory refresh; protected/unknown routes reject common reload | Repeated order dispatch uses module flags; failure popup must be positively detected, detection failure skips submission | Known order-failure popup is retryable; unavailable DOM is degraded/inconclusive | Per-tab PlatformEngine mapping; status flags reset on route/failure transitions | Source-reviewed host flow, protected-route fixtures, TicketPlus/KHAM hardening and platform regressions |
| KKTIX | Event/registration safe pages only; booking/order/unknown protected | Duplicate next/order actions guarded by state; sold-out recovery only after known alert/status | Dangerous cancel dialogs are dismissed; known sold-out/error alerts flag guarded reload; unknown content does not claim success | Per-tab mapping; alert callback explicitly rebound to its tab mapping | Source-reviewed flow, fixture-tested protection, adapter/registry/state/navigation tests |
| KHAM family | Product/performance safe routes; seat/order/checkout/payment/unknown protected | Existing platform submit ordering retained; guarded navigation and bounded reload; seat handlers remain family-specific | Captcha text errors reset captcha; missing DOM returns explicit non-success | Per-tab mapping shared by KHAM/ticket/UDN family; attempt flags are family-local | Public/source evidence, fixture route tests, TicketPlus/KHAM hardening and state tests |
| iBon | Activity/performance safe routes and explicit platform refresh paths only | Checkout/order/payment protected; sold-out return paths use guarded navigation | Global alert handler auto-dismisses known safe failures; critical checkout prompts remain manual | Per-tab mapping; early/global alert callback explicitly bound to tab state | Source-reviewed flow, fixture protection, adapter/registry/state/navigation tests |
| Cityline | Event/performance safe routes only | Basket/order/checkout/payment protected; homepage recovery is guarded and throttled | Modal/login/basket outcomes have explicit booleans; absent elements do not imply success | Per-tab mapping | Source-reviewed routes, fixture protection, adapter/registry/state/navigation tests |
| HKTicketing family | Event/performance safe routes only | Selection/order/payment protected; every redirect/recovery is guarded | Traffic overload, modal and content retry lists are explicit; unknown protected | Per-tab family mapping | Public/source evidence, fixture protection, adapter/registry/state/navigation tests |
| FamiTicket | Activity/ticket safe routes; order/checkout/payment protected | Verify/date/area/ticket progression remains idempotent through state flags | Verification failure and missing selections return explicit false/retry outcomes | Per-tab mapping | Source-reviewed flow, fixture protection, adapter/registry/state/navigation tests |
| FunOne | Event/sales safe routes; reserved/order/checkout/payment protected | Step flags prevent repeated captcha/order submission; guarded homepage recovery | Captcha/OCR attempts are bounded with manual fallback; sold-out/step detection is explicit | Per-tab mapping with per-step flags and bounded retry counters | Public/source evidence, fixture protection, adapter/registry/state/navigation tests |
| FANSI GO | Event/show safe routes; checkout/order-result/unknown protected | Quantity/checkout flags prevent duplicate actions; show navigation is guarded | API/DOM absence returns non-success; checkout and result are terminal protected states | Per-tab mapping | Public/source evidence, fixture protection, adapter/registry/state/navigation tests |

## Explicit non-capabilities and limits

- No family claims automated payment, captcha bypass, queue bypass, WAF/rate
  limit evasion, proxy/account pooling or bulk purchasing.
- `UNKNOWN`, queue and protected routes are never safe common-refresh pages.
- A hostname match selects ownership only; it does not upgrade validation
  evidence. Registry levels remain truthful and are tested independently from
  implementation capability flags.
- Real transactions were not executed. `PUBLIC_PAGE_TESTED` means read-only
  public route evidence, not a completed purchase. All other platform behavior
  in this release was exercised through source review and repeatable production
  handler fixtures.
