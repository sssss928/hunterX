# HunterX v0.5.2 Final User-Dictionary Acceptance

## Decision

The reported broad failure that every known platform cannot read the custom dictionary was **NOT REPRODUCED** on the latest RC3 production source. The shared dictionary architecture was retained.

## Verified path

`settings` POST payload → `advanced.user_guess_string` canonical storage → runtime hot reload → shared `parse_user_dictionary_answers()` / `get_answer_list_from_user_guess_string()` → platform text-question handler.

## Verified consumers

- TixCraft and its registry family, including IndieVox/TeamEar/Ticketmaster mappings, through the production question handler.
- KKTIX with JSON-safe JavaScript encoding.
- TicketPlus order-page multi-field flow.
- FamiTicket verification input.
- iBon validation and multi-question flow.
- KHAM/Ticket.com/UDN multi-field resolution.
- HKTicketing-family entitlement/date password input.

Cityline, FunOne, and FansiGo do not currently expose a supported text-question handler and correctly receive no dictionary injection.

## Data and lifecycle cases

- Runtime hot reload applies changed answers without restart.
- Online dictionary input reads the complete multiline file, not the first line only.
- Commas, quotes, backslashes, literal newlines, ASCII semicolons, and full-width semicolons round-trip without silent loss.
- Local and online answers are deduplicated in stable order.
- Dictionary values are not routed to CAPTCHA, credentials, Queue-it, challenges, risk control, checkout, or payment.

## Evidence

- Dictionary-focused suites: 29/29 passed.
- Critical dictionary/lifecycle subset: passed in 20 fresh processes.
- Both full suites passed.
- Existing RC3 source-to-both-embedded-runtime parity passed for 79 runtime files, so packaged Python consumers match the tested source.
