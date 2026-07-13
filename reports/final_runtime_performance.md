# Final Runtime Performance

## Audit Command

`python tests/benchmarks/audit_performance.py --output work/final_performance_audit.json --markdown work/final_performance_audit.md --samples 5 --iterations 1000`

## Summary

The audit completed successfully after changes. Key timing functions remain in sub-millisecond to low-millisecond ranges per benchmark group at 1000 iterations. No new filesystem or network I/O was added to hot timing paths.

# Performance Benchmark

Generated: 2026-07-13T10:43:20
Python: 3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
Samples: 5
Iterations per sample: 1000

| Benchmark | p50 wall ms | p95 wall ms | p99 wall ms | mean wall ms | stdev | p50 CPU ms | max peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| default_config | 28.732700 | 29.547200 | 29.547200 | 28.886140 | 0.332420 | 31.250000 | 3192 |
| config_migration | 144.386300 | 145.342000 | 145.342000 | 144.483740 | 0.754228 | 140.625000 | 12557 |
| interval_parsing | 3.984800 | 4.326500 | 4.326500 | 3.962140 | 0.222576 | 0.000000 | 617 |
| interval_due | 2.337500 | 2.607900 | 2.607900 | 2.378400 | 0.115770 | 0.000000 | 601 |
| bounded_poll_immediate | 7.893300 | 8.092400 | 8.092400 | 7.904560 | 0.152109 | 15.625000 | 11245 |
| keyword_matching | 25.552200 | 25.675400 | 25.675400 | 25.506860 | 0.124110 | 31.250000 | 1706 |
| refresh_timing | 238.108100 | 244.032100 | 244.032100 | 239.502720 | 2.443494 | 234.375000 | 2546 |
| trigger_planner | 55.206700 | 56.845700 | 56.845700 | 55.518500 | 0.710818 | 46.875000 | 2581 |
| robust_estimate | 64.171900 | 64.780300 | 64.780300 | 64.228120 | 0.385239 | 62.500000 | 728 |
| remaining_calculation | 3.046800 | 3.266200 | 3.266200 | 3.089360 | 0.090277 | 0.000000 | 212 |
| source_selection | 25.475600 | 25.726100 | 25.726100 | 25.417280 | 0.234851 | 31.250000 | 1783 |
| platform_interval_helpers | 2.844000 | 2.897500 | 2.897500 | 2.815800 | 0.081655 | 0.000000 | 160 |
| url_classification | 17.025800 | 17.084300 | 17.084300 | 16.940560 | 0.139337 | 15.625000 | 1374 |


