# HunterX v0.5.0 release notes

HunterX v0.5.0 is based directly on the official v0.4.9 tag at commit
`b362df5d007d4cdb827e41ef98e100e6ffe3e4a6`. The existing date,
area, quantity, qualification, CAPTCHA and submit algorithms remain in place;
the changes are concentrated in the refresh, navigation, result handling and
liveness boundaries around those algorithms.

## Windows package

- The Windows ZIP uses the original dual-executable layout: run `settings.exe`
  directly and keep `_settings_internal` and `_nodriver_internal` beside it.
- Python does not need to be installed, and the Windows package does not use
  `run_settings.bat` or `run_hunterX.bat`.
- `settings.json` is intentionally generated in the package root on first
  launch, so a freshly extracted ZIP does not contain a stale user profile.

## TicketPlus (遠大售票)

- Treats TicketPlus `/order/...` as a pre-submit ticket-selection page, so the
  one-shot sale-time refresh and normal interval refresh can actually execute.
  `/confirm/...`, `/confirmseat/...`, checkout and payment routes remain
  protected from automatic reload.
- Uses a monotonic, mode-aware, non-blocking deadline for TicketPlus inventory
  refreshes. Onsale mode reads `auto_reload_page_interval`; leak-watch mode
  reads `leak_refresh_interval_seconds`. A delayed loop never sends a burst of
  catch-up refreshes, and a transient dispatch collision is retried promptly.
- Uses the site's inventory-refresh control when a visible, enabled refresh
  button is available and falls back to the shared guarded full-page reload.
- Detects the visible purchase-failure dialog, clicks `我知道了` and common
  equivalent controls, parses Zendriver's serialized return format, and
  verifies that the dialog really disappeared before it reports success.
- A generic Vuetify overlay is no longer treated as proof of a queue. Queue
  monitoring requires a queue route, queue dialog or queue-specific text, and
  purchase-failure text takes precedence.
- Replaces the post-submit random 5–10 second pause and unbounded inner queue
  loop with a single-submit outcome state machine. Confirmation, queue,
  rejection, transient CDP failures and timeout recovery are advanced from the
  main loop without submitting underneath a modal or duplicating an order.

## Shared platform reliability

- Resolves JavaScript/CDP URL disagreement by comparing purchase stages. A
  strictly advanced browser target wins during navigation; an equal-stage or
  older cached target cannot overwrite a valid live JavaScript URL.
- Registers supported Ticketmaster country domains using exact host-boundary
  matching. Look-alike domains such as `ticketmaster.com.attacker.invalid`
  remain rejected.
- Reload protection is platform-aware, blocks queue/unknown sensitive routes,
  and keeps a recovery-only escape hatch for explicit recovery code.
- The original v0.4.9 scheduled-refresh health evidence, fixed retry budget,
  soft-block detection and recovery re-probe state machine are retained.
- The PyInstaller specification now explicitly includes the shared runtime,
  registry, state, refresh, submit and Zendriver hardening modules.

## Validation performed for this source

- Python bytecode compilation of `src`, `tests` and `scripts`.
- Ruff syntax/undefined-name gate.
- Full inherited pytest regression suite plus TicketPlus v0.5.0 fixtures.
- Repeated refresh/outcome tests with fake browser tabs and a deterministic
  monotonic clock.
- Source and packaged ZIP path, CRC, contents and SHA-256 verification.

Fixture tests intentionally stop before any real transaction. A ticket cannot
be guaranteed: ticket inventory, site HTML, account eligibility, CAPTCHA,
queues, rate limits, network latency and platform rules are controlled by the
ticket provider. HunterX does not bypass those controls or automate payment.
