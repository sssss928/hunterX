# HunterX v0.5.0 validation report

Date: 2026-08-16  
Base tag: `v0.4.9`  
Base commit: `b362df5d007d4cdb827e41ef98e100e6ffe3e4a6`

## Final gates

| Gate | Result |
|---|---:|
| Python compile (`src`, `tests`, `scripts`) | Passed |
| Ruff syntax / undefined-name gate | Passed |
| Strict mypy configured scope | Passed, 21 source files |
| Final full pytest suite | 758 passed, 1 skipped, 0 failed |
| TicketPlus v0.5.0 focused suite | 9 passed, 0 failed |
| TicketPlus repeat run | 20 runs × 9 tests = 180 passed, 0 failed |
| Settings/help JavaScript syntax (`node --check`) | Passed |

The single skipped inherited test checks the Windows Shell ZIP namespace and is
not available in the Linux validation environment. It is not counted as a pass.
The generated Windows ZIP is additionally checked for safe root-relative paths,
CRC integrity, required contents, isolated runtime layout and PE signatures.

## Regression found and corrected during this rebuild

The earlier v0.5.0 candidate had removed four v0.4.9 scheduled-refresh safety
behaviours while trying to handle an unavailable page-health probe. The original
v0.4.9 regression suite correctly failed on:

- one-time fail-open behaviour for an unavailable boundary health probe;
- confirmed soft-block protection;
- a fixed retry deadline that cannot extend forever; and
- invalidation and re-probing of old page-health evidence.

The official v0.4.9 implementations and tests were restored instead of deleting
or weakening those tests. The final complete suite then passed.

## TicketPlus coverage added

- Scheduled reload executes on the TicketPlus pre-submit `/order/...` route.
- Periodic refresh obeys one dispatch per configured interval.
- `/confirm/...`, `/confirmseat/...`, checkout/payment and unknown sensitive
  routes remain protected from automatic reload.
- Zendriver-serialized popup results are parsed; a dismiss click counts only
  after the dialog closure is observed.
- Missing popup dismissal controls block duplicate submission.
- A generic overlay alone is not classified as a queue; queue/failure evidence
  precedence is deterministic.
- Onsale and leak-watch intervals use separate monotonic deadlines.
- A submitted attempt retains ownership and prevents duplicate submit calls.
- A persistently unavailable CDP outcome expires into guarded inventory retry
  instead of creating an unbounded pending state.

## Honest validation boundary

No real ticket order, payment, CAPTCHA bypass or Queue-it bypass was performed.
The tests use deterministic browser fixtures and stop before a transaction.
Live behaviour can still change when a provider changes HTML, scripts, account
eligibility, queueing or rate limits, so no software can guarantee inventory or
a successful purchase.
