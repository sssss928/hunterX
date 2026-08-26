# HunterX v0.5.2 Final Performance Report

RC2 and RC3 immutable source trees were run on the same Windows host and CPython 3.11.9 in alternating order. No test, package verification, or browser soak ran concurrently with the reported high-density measurements.

| Hot path | RC2 median ns/op | RC3 median ns/op | Median delta | p95 delta |
| --- | ---: | ---: | ---: | ---: |
| main-loop no-op | 158.38 | 159.66 | +0.81% | -1.32% |
| URL classification | 16,907.46 | 16,993.68 | +0.51% | -0.76% |
| platform dispatch | 21,451.92 | 21,836.40 | +1.79% | +0.89% |
| TicketPlus watcher before probe | 784.94 | 789.18 | +0.54% | -2.25% |
| wrong-target rejection | 33,426.60 | 33,656.35 | +0.69% | +6.43% |

The wrong-target p95 was investigated with 10 additional balanced runs per candidate, 51 samples per run. It converged to median -0.26% and p95 +0.08%.

The low-density 20-path sweep also showed production-iteration median -23.09% and terminal-lifecycle healthy median -2.21% for RC3, but those favorable numbers are treated as environment-sensitive rather than advertised speedups.

Conclusion: the reported large slowdown was **NOT REPRODUCED**. No random jitter, fixed click delay, polling slowdown, refresh change, or speculative performance patch was added.

## FINAL delivery snapshot

A post-waiver, isolated 21-sample snapshot confirmed the preserved RC3 hot
paths: main-loop no-op 156.00 ns/op, URL classification 16,704.05 ns/op,
platform dispatch 21,655.05 ns/op, per-tab lookup 245.00 ns/op, refresh idle
1,699.95 ns/op, due-refresh decision 2,987.35 ns/op, dictionary parse
18,065.75 ns/op, and production-iteration no-op 172,448.20 ns/op. This is a
sanity snapshot, not a replacement for the balanced RC2/RC3 A/B table above.

The FINAL packaging layer changes no `src/**` file, so there is no new product
hot-path implementation to optimize or compare against RC3.
