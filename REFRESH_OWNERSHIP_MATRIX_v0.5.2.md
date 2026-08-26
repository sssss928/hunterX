# HunterX v0.5.2 Refresh Ownership Matrix

Release status: **RC**
Long-run qualifier: **8H SOAK NOT VERIFIED**

v0.5.2 does not introduce a second refresh loop or global state manager. `PlatformEngine`, `RefreshCoordinator`, `ReloadGuard` and `LeakWatchScheduler` remain the existing owners, with attempt lifecycle information added to their established decision path.

| Route/state | Formal purchase mode | Leak-watch mode | Owner | v0.5.2 invariant |
|---|---|---|---|---|
| Activity/date/area safe route | Existing platform cadence and configured scheduled reload | Existing leak-watch cadence and availability transition | Platform adapter through `RefreshCoordinator`; guarded by `ReloadGuard` | New attempt may be armed; one owner per tab; configured timing preserved |
| Ticket form (`PageClass.TICKET`) | No competing reload | No leak-watch reload | Existing attempt and protected-route guard | Never rearms by itself; a ticket-named URL is safe only when its adapter classifies it as `AREA` |
| Safe route, interval = 0 | No periodic reload | No periodic reload | `RefreshCoordinator` disabled path | Zero continues to mean disabled |
| Ticket/order submission active | No competing reload | No leak-watch reload | Submission owner for exact attempt | Refresh ownership is suspended while submit ownership is active |
| Confirm/order/checkout/payment | No reload | No reload | Protected-route guard | Never rearm and never duplicate submit on a protected page |
| Queue or challenge | No automation-driven queue reload | No automation-driven queue reload | External/platform-controlled page plus protected-route guard | Read-only observation only; no Queue-it/challenge bypass |
| Login redirect | No unrelated periodic reload | No unrelated leak reload | Canonical `NavigationIntent` plus existing platform login handler | Bounded restore to saved target; no global target state |
| Empty URL/transient target stale | No blind reload | No blind reload | `RuntimeSupervisor` and `BrowserSessionManager` | Bounded target reacquire/rebind; attempt identity preserved |
| Confirmed CDP transport/crash with safe context | No unbounded restart | No unbounded restart | Existing main lifecycle through `BrowserSessionManager` | Circuit-bounded recovery; restart only when transaction state is known safe |
| User closes browser cleanly or closure is ambiguous | Stop | Stop | `BrowserExitState` | Never reopen browser automatically |
| Submit outcome unknown | Stop automation for old attempt | Stop automation for old attempt | Attempt state `SUBMIT_OUTCOME_UNKNOWN` | Fail closed until a confirmed safe route starts a new attempt |

The TicketPlus bounded submission watcher observes submission outcome; it does not own or schedule general page refresh. Same-URL SPA DOM drift detection updates route generation without adding a body-wide `MutationObserver`.
