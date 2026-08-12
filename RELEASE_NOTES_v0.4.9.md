# HunterX v0.4.9 release notes

HunterX v0.4.9 is a reliability and release-hardening update based on remote
`main` commit `982d766561aa7f37d2f7810a928b69d9f164d568`. It preserves the
existing platform-specific selection and purchase handlers while tightening the
shared attempt, refresh, browser and packaging boundaries.

The Windows package now uses canonical root-relative ZIP member names. The
release verifier rejects synthetic `./` roots and other non-portable paths that
some ZIP readers normalize but Windows File Explorer can display as an empty
archive. The release gate also opens the completed asset through the same
Windows Shell namespace used by File Explorer and rejects a zero-item view.

## TixCraft-family recovery

Activity-detail pages now use the configured mode interval while waiting for a
real purchase entry. Link and `data-href` variants are detected; once present,
the guarded flow enters the game/date page and continues automatically. AREA
dispatch accepts root/event route variants and applies the configured keyword
and ordering mode in both on-sale and leak-watch. Remaining inventory is parsed
numerically, so a request for two tickets correctly accepts nine remaining but
rejects one.

The existing `TixCraftPurchaseAttempt` is now the sole purchase-attempt identity.
Ticket count and OCR completion evidence includes that identity, so returning to
the same AREA and TICKET URL creates a clean second attempt. Submit ownership is
not expired by a short timer: it is retained through slow or unknown transitions
and released only by impossible identity evidence or a confirmed interactive
AREA recovery. ORDER, CHECKOUT and PAYMENT remain protected.

Verification input readiness uses bounded polling and explicit states. A selector
that returns `None` no longer starts OCR. Blocking CAPTCHA retrieval and OCR
classification run outside the main asyncio loop; the expensive model cache is a
bounded 16-entry LRU. `force_submit=false` still detects and fills without an
automatic submit, while `force_submit=true` keeps the original guarded submit.

## Refresh timing and modes

The v0.4.8 monotonic, millisecond one-shot timing and per-tab
`RefreshCoordinator` remain authoritative. A value such as
`2026/08/10 11:00:00.000` is parsed in Asia/Taipei and armed once against a
monotonic deadline. Periodic intents near that deadline are coalesced; delayed
loops do not perform catch-up bursts. The configured interval remains a
deterministic minimum between dispatch starts. Queue, challenge, ticket, order,
checkout and payment states cancel or deny background refresh.

On-sale and leak-watch modes continue through the same real purchase handlers.
Only target timing, scan cadence and backoff policy differ. Leak-watch scans a
completed document generation once and rearms only after a new generation.

## Security and reproducibility

Sensitive localhost control requests now require a per-launch random secret
carried by an HttpOnly, SameSite=Strict session cookie and compared in constant
time. Existing loopback and same-origin protections remain. Static assets and
the version probe remain available for startup discovery.

Runtime packages have a hashed Windows CPython 3.11 lock. GitHub Actions are
pinned by immutable commit, the release build runs without write permission,
and the separate publish job re-verifies the checksum manifest before gaining
`contents: write` access.

Persisted settings still use the compatible plaintext schema. Migrating all
profiles/backups to Windows DPAPI was not performed because a safe atomic
migration and non-Windows round trip could not be proven in this release; no
home-grown encryption was substituted. API masking and diagnostic redaction
remain in force. Protect `settings.json`, instance profiles and backups as
sensitive files.

## Limits

Tests use source, deterministic scheduler simulations and synthetic DOM/CDP
fixtures. No real ticket order, CAPTCHA/queue bypass or payment was attempted.
External DOM changes, inventory, account state, network/browser latency and
server-side opening time can still affect live results; no software can
guarantee tickets.
