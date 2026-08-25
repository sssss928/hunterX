# HunterX v0.5.2 RC2 — Round 2 Performance Comparison

## Method

The final investigation used five fresh processes per side, 21 samples per
scenario and higher iteration counts with balanced A/B and B/A ordering on the
same Windows machine. It compares the candidate with both the Round-1 v0.5.2 RC
development base and the original v0.5.1 product base.

The table reports pooled sample median and p95. Positive percentages are slower;
negative percentages are faster.

## Round-1 RC versus RC2 candidate

| Scenario | Round-1 median ns | RC2 median ns | Median Δ | Round-1 p95 ns | RC2 p95 ns | p95 Δ |
|---|---:|---:|---:|---:|---:|---:|
| Main-loop no-op | 153.97 | 155.11 | +0.74% | 168.84 | 163.64 | -3.08% |
| Platform dispatch | 19,954.70 | 20,489.94 | +2.68% | 20,885.68 | 21,447.00 | +2.69% |
| Per-tab state lookup | 244.03 | 243.45 | -0.24% | 281.59 | 280.71 | -0.31% |
| TicketPlus watcher before probe | 762.05 | 768.74 | +0.88% | 805.81 | 823.52 | +2.20% |
| Multi-tab state | 19,751.66 | 19,769.20 | +0.09% | 20,823.36 | 20,474.80 | -1.67% |
| User-dictionary parse | 14,320.08 | 16,322.20 | +13.98% | 15,255.18 | 17,004.42 | +11.47% |
| Disabled logging | 48.40 | 48.35 | -0.11% | 52.48 | 50.60 | -3.57% |

## v0.5.1 versus RC2 candidate

| Scenario | v0.5.1 median ns | RC2 median ns | Median Δ | v0.5.1 p95 ns | RC2 p95 ns | p95 Δ |
|---|---:|---:|---:|---:|---:|---:|
| Main-loop no-op | 162.39 | 164.22 | +1.13% | 183.77 | 175.80 | -4.34% |
| Platform dispatch | 21,628.80 | 21,449.72 | -0.83% | 23,382.64 | 23,223.04 | -0.68% |
| Per-tab state lookup | 616.12 | 252.13 | -59.08% | 666.53 | 285.60 | -57.15% |
| TicketPlus watcher before probe | 826.49 | 788.73 | -4.57% | 932.79 | 880.06 | -5.65% |
| Multi-tab state | 21,171.66 | 20,997.94 | -0.82% | 22,339.28 | 22,385.42 | +0.21% |
| User-dictionary parse | 14,832.66 | 17,278.00 | +16.49% | 16,175.76 | 18,603.20 | +15.01% |
| Disabled logging | 50.40 | 50.30 | -0.19% | 54.58 | 57.43 | +5.23% |

## Interpretation

All normal 50 ms-loop hot paths remain within +3% median and +5% p95 against
the Round-1 RC. The dictionary parser performs lossless normalization and stable
deduplication; it is about 2.0–2.4 microseconds slower per call, but runs only on
a text-question page, not on every normal iteration. A canonical legacy-format
fast path was added and all focused, adjacent, repeated and full tests were
rerun after it.

Exact target reacquisition and target transport proof are intentionally slower
than the unsafe Round-1 behavior because they now perform a bounded identity and
live-CDP proof. They are recovery-only paths, measured at roughly 0.05–0.09 ms,
and do not execute on the normal loop.

Cross-version scenarios that do not exist in a baseline, such as the unified
production iteration and wrong-target rejection, are treated as correctness
features rather than fabricated performance comparisons.

## Decision

Normal-path performance is retained. The only material percentage increase is
the dictionary parser's microsecond-scale, page-specific correctness work. No
random jitter, global click delay or cadence change was used.
