# HunterX v0.5.1 release notes

HunterX v0.5.1 is a focused TicketPlus liveness and release-quality update
built directly from the official HunterX v0.5.0 source release.

## TicketPlus fixes

- A submitted order now owns the real `/order/<event>/<session>` route before
  the outer popup sanitizer, preventing a second submit while the first attempt
  is pending, blocked, queued or temporarily unobservable.
- Confirmation, visible purchase failure, positive queue evidence and pending
  state are resolved in strict precedence order. A generic Vuetify overlay is
  never sufficient queue evidence.
- All visible `[role=dialog]`, `.v-dialog`, `.v-dialog__content` and
  `.v-overlay__content` containers participate in failure detection. A failure
  dialog vetoes queue wording, body text and explicit queue-route evidence.
- Submission polling uses short monotonic probe deadlines without changing the
  user's on-sale or leak-watch refresh settings.
- Positive queue evidence may slide the 30-second soft deadline, while a fixed,
  non-sliding 600-second hard fuse bounds queue ownership. Soft and next-probe
  deadlines are capped at that hard fuse.
- A same-tab redirect to an external Queue-it waiting room retains exactly one
  already-established platform owner. The route is monitored read-only; a new,
  unrelated or ambiguous waiting-room tab is not claimed by TicketPlus.
- Queue body wording is read from visible `innerText`; hidden templates do not
  create queue evidence. Any failure phrase in any visible dialog vetoes queue
  classification.
- Blocked failure dialogs retain submit ownership only until the soft deadline.
  Expiry schedules the existing guarded, mode-aware inventory recovery and does
  not submit underneath the dialog.
- Inventory recovery continues to prefer TicketPlus's partial refresh control
  and falls back to the shared guarded full-page reload.

## Preserved behavior

Date, area, ticket and quantity selection; `allow_less_tickets`; notifications;
profiles; multi-instance state; TixCraft; Ticketmaster; KKTIX; and protected
confirmation/checkout/payment routes are unchanged. CAPTCHA, platform challenge,
queue admission and payment remain manual/platform-controlled boundaries.

## Packaging

The Windows archive is explicitly an overlay of the final v0.5.1 source on the
verified official v0.5.0 isolated dual-executable runtime. It retains directly
launchable `settings.exe` and `nodriver_tixcraft.exe`, verifies both external
`app_src` trees, archive paths, CRC and embedded version metadata. When built
from Git, the overlay first materializes the same commit-exact snapshot used by
the source ZIP, preventing checkout line endings from changing packaged bytes.

See `TEST_REPORT_v0.5.1_FINAL.md`, `FINAL_CROSS_AUDIT_v0.5.1.md` and
`BASELINE_PROVENANCE_v0.5.1.md` for reproducible validation and provenance. The
external deliverable manifest is `SHA256SUMS_v0.5.1_FINAL.txt`.
