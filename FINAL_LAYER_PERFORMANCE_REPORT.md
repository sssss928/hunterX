# Final-Layer Performance Report

Balanced A/B measurements compared immutable RC2 and RC3 with alternating execution order.

## Lifecycle hot path

- terminal lifecycle healthy no-op median: -1.77%; p95: -3.86%.
- production iteration median: -2.70%; p95: -5.32%.

The new boundary adds no work to a healthy iteration beyond the existing `try` fast path.

## Noisy initial observations and focused repeats

An initial aggregate showed due-refresh median +4.08% and TicketPlus watcher p95 +7.43%. These were treated as investigation triggers, not accepted as regressions or ignored.

- focused due-refresh, 8 balanced pairs, 51 samples x 10,000: median -1.25%; p95 -3.15%.
- high-density TicketPlus watcher, 10 balanced pairs, 101 samples x 50,000: aggregate median -0.50%; aggregate p95 +1.25%; paired median-of-p95 -1.27%.

The observed initial variance did not reproduce at higher density. There is no supported claim of a Final-Layer performance regression.

No random jitter, global click delay, polling slowdown, blind reload, or refresh-cadence change was added.

