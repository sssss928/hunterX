# HunterX v0.5.1 vs v0.5.2 Performance Comparison

Release status: **FINAL delivery by explicit user waiver**
Long-run qualifier: **BOTH 8H ACTUAL-BROWSER GATES USER WAIVED / NOT VERIFIED**

## Method

The immutable v0.5.1 base and v0.5.2 candidate ran in fresh Python processes on the same machine. Final results combine five A→B rounds and five B→A rounds to balance order/thermal bias. Every round used 31 samples × 5,000 iterations for each of 12 scenarios. Values are nanoseconds per operation; negative delta means v0.5.2 is faster.

| Scenario | v0.5.1 median | v0.5.2 median | Median delta | v0.5.1 p95 | v0.5.2 p95 | p95 delta |
|---|---:|---:|---:|---:|---:|---:|
| Main-loop no-op | 154.86 | 156.68 | +1.18% | 213.69 | 206.85 | -3.20% |
| URL classification | 16,700.43 | 16,501.15 | -1.19% | 17,824.78 | 17,422.13 | -2.26% |
| Platform dispatch | 21,437.69 | 20,994.26 | -2.07% | 22,297.90 | 21,893.64 | -1.81% |
| Per-tab state lookup | 616.93 | 246.99 | -59.96% | 686.36 | 312.92 | -54.41% |
| Refresh coordinator idle | 1,636.60 | 1,631.33 | -0.32% | 1,734.68 | 1,779.14 | +2.56% |
| Due refresh decision | 3,023.84 | 3,022.75 | -0.04% | 3,213.57 | 3,182.75 | -0.96% |
| TicketPlus watcher before probe | 856.63 | 845.80 | -1.26% | 929.38 | 925.88 | -0.38% |
| Three-tab dispatch | 21,086.79 | 20,879.43 | -0.98% | 21,897.27 | 21,657.26 | -1.10% |
| Runtime health record-loop | 29.12 | 61.98 | +112.84% | 39.80 | 76.40 | +91.96% |
| Disabled logging | 48.20 | 47.71 | -1.02% | 63.33 | 65.45 | +3.35% |
| One-instance profile path | 9,844.57 | 9,946.50 | +1.04% | 10,417.17 | 10,427.75 | +0.10% |
| Three-instance profile path | 9,960.11 | 10,039.29 | +0.79% | 10,405.75 | 10,767.33 | +3.47% |

The health no-op percentage is large because the base is only 29.12 ns. The absolute median addition is 32.86 ns and performs no DOM, URL parsing or CDP work. Actual platform dispatch, URL classification, due-refresh decisions and TicketPlus pre-probe checks are unchanged or faster. The three-instance profile p95 paired-round median was +2.23%; the aggregate p95 difference is dominated by process scheduling noise.

## Failure and optimization loop

The first final A/B set reproduced platform dispatch +5.86% and three-tab +6.17%; this was rejected. Profiling localized the cost to candidate `before_dispatch` bookkeeping, not adapter classification, refresh or DOM/CDP. The fix changed `DispatchDecision` to an immutable tuple, moved safe/protected page sets to module constants, and skipped impossible transition work on a confirmed stable route. Direct lifecycle/router regressions passed before the balanced A/B rerun.

## Conclusion

No measurable hot-path regression remains in the final balanced results. Refresh interval semantics, interval=0, protected-page suppression and the existing platform click/selection algorithms were not changed for benchmark improvement.
