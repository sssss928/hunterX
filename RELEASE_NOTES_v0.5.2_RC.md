# HunterX v0.5.2 RC2 Release Notes

**8H SOAK NOT VERIFIED** — this build is a Round-2 release candidate, not a final long-run-verified release.

HunterX v0.5.2 RC fixes the lifecycle defect where a completed purchase could leave automation permanently disabled. Completion is now scoped to one tab, platform and purchase attempt. Remaining on protected pages cannot submit again, while returning to an adapter-confirmed `ACTIVITY`, `DATE` or `AREA` route starts a new attempt and resumes the configured formal or leak-watch flow. `PageClass.TICKET` remains protected; a path named `ticket` is safe only where that platform adapter explicitly classifies it as `AREA`.

TicketPlus no longer uses a process-global purchase-done latch. Its submission watcher claims an exact attempt, treats an ambiguous post-click transport result as fail-closed, and restores the configured activity target after a successful login with bounded retries.

Long-running browser handling now distinguishes a user closing the browser from a confirmed CDP transport or browser crash. User closure never reopens pages. Safe transient failures use bounded target reacquisition/rebind and circuit-limited restart; ambiguous transaction state never auto-resubmits.

The release also fixes the user-defined answer dictionary end to end. Legacy
quoted values, JSON arrays, newline, ASCII/full-width semicolon and list input
now normalize through one lossless parser; commas, quotes and backslashes are
preserved; the complete online file is merged with stable deduplication; and
settings save/hot reload use the same format. All registered platform families
that expose a text-question field now consume that shared parser, including
TixCraft/TeamEar/IndieVox/Ticketmaster, KKTIX, FamiTicket, iBon,
KHAM/ticket.com.tw/UDN, TicketPlus and HKTicketing/Galaxy/Ticketek.

Other Round-2 work adds tab-scoped SPA route generations, bounded owned async
tasks, low-frequency resource diagnostics, multi-instance regressions and local
actual-browser soak tooling. Windows diagnostics bind process APIs once, and
the Zendriver compatibility layer no longer retains write-only CDP event
objects indefinitely. Existing platform purchase selectors, refresh semantics,
interval=0 behavior, protected checkout/payment/queue rules, OCR/manual CAPTCHA
boundaries, notifications and user configuration remain intact.

Supported registry families include TixCraft/IndieVox/Ticketmaster, KKTIX, FamiTicket, iBon, KHAM/ticket.com.tw/UDN, TicketPlus, Cityline, HKTicketing/Galaxy Macau/Ticketek, FunOne and FANSI GO.

This RC does not implement CAPTCHA, Queue-it, challenge, risk-control or payment bypass. Live third-party purchases and payments were not executed.

See `ROUND2_TEST_REPORT_v0.5.2.md`,
`ROUND2_LONG_RUN_STABILITY_REPORT_v0.5.2.md`,
`ROUND2_PERFORMANCE_COMPARISON.md` and
`ROUND2_FINAL_CROSS_AUDIT_v0.5.2.md` for verified scope and limitations.
