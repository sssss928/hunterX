# Final-Layer User Dictionary Acceptance

## Outcome

The reported broad failure — that all known ticket platforms cannot read the user dictionary — was **NOT REPRODUCED** on immutable RC2. The original architecture was retained.

Immutable RC2 passed the existing 30-test dictionary acceptance set. RC3 passed that set plus Final-Layer production-boundary tests. Source tracing and dynamic tests prove that `advanced.user_guess_string` is normalized by settings, saved, reloaded by the running configuration path, decoded by the shared parser, and consumed by applicable production text-question handlers.

## Consumers

- TixCraft production text-question handler;
- KKTIX production custom-question handler with safe JSON encoding;
- TicketPlus order-page multi-field custom answers;
- FamiTicket verification question;
- iBon card/question field;
- KHAM, Ticket.com, and UDN question resolution;
- HKTicketing-family entitlement/date password fields.

Cityline, FunOne, and FansiGo currently have no product text-question handler; they correctly do not receive dictionary injection.

## Parser and lifecycle evidence

- hot reload applies changed dictionary content without restarting the bot;
- online response parsing consumes the complete multiline body;
- comma, quote, backslash, newline, ASCII semicolon, and full-width semicolon cases remain intact;
- KKTIX answers continue to use JSON encoding instead of JavaScript string concatenation;
- dictionary values are not routed to CAPTCHA, login credentials, Queue-it, challenges, risk control, checkout, or payment.

## Actual boundary defect found

RC2 injected the dictionary on the TicketPlus confirmation route, which the authoritative adapter classifies as CHECKOUT. A pre-fix dynamic negative control failed. RC3 removes that single confirmation-handler call while preserving the order-page custom multi-field call. The post-fix test passes.
