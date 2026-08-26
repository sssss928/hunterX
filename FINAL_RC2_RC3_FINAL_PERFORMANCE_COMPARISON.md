# RC2 / RC3 / Pre-Final Performance Comparison

## Product bytes under test

- RC2 source ZIP SHA-256: `3B4682DAB728C951475409BD2E46078456A074386487DF376A3EA98533138B36`.
- RC3 source ZIP SHA-256: `00B02901A90D4F4A71BA9F8645738306B2C224F0EB076ADC347DCEC9A23510CE`.
- This pre-final pass changes no `src/**` file, so its production hot-path implementation is byte-equivalent to RC3 after line-ending normalization.

## Results

The first three balanced pairs were intentionally treated as screening only because several results moved in opposite directions with execution order. Five higher-density balanced pairs reduced the investigated median differences to within +1.79%. A further 20-run wrong-target experiment reduced its prior p95 signal to +0.08%.

The evidence does not support a broad RC3 regression or a new production optimization. The final candidate should therefore preserve RC3 production bytes and improve only CI, release evidence, and reproducibility.

## Final eligibility note

These measurements satisfy the local A/B requirement but do not substitute for
long-run actual-browser gates. **BOTH 8H ACTUAL-BROWSER GATES USER WAIVED / NOT
VERIFIED.** FINAL naming follows the user's explicit waiver and is not a claim
that either duration gate passed.
