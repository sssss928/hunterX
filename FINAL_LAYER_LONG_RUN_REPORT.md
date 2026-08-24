# Final-Layer Long-Run Report

## Short actual-browser acceptance

Microsoft Edge was exercised against local synthetic pages through the same production `run_runtime_iteration()` implementation used by `_run_main()`.

- one named instance: requested 120 s, 123.78 s actual, 84 cycles, 0 errors, 0 CDP failures, 0 duplicate submissions, max tabs 1, task registry 0 -> 0, asyncio tasks 8 -> 8;
- three named instances: each approximately 120–123 s and 84 cycles, 0 errors, 0 CDP failures, 0 duplicate submissions, max tabs 1 per instance.

The rotation covered TicketPlus, TixCraft, and KKTIX synthetic onsale/login-like/fallback transitions. It did not navigate to a third-party ticketing service and did not test CAPTCHA, queue, challenge, checkout, or payment bypass.

The short run did not reach the harness target-replacement/reload-injection thresholds, so those two injections are not claimed by this run. Dedicated deterministic recovery tests cover those contracts.

## Final eligibility

**8H SOAK NOT VERIFIED**

- 8-hour single named instance actual-browser gate: not executed.
- 8-hour three named instances actual-browser gate: not executed.

Consequently RC3 is mandatory and FINAL branding is forbidden.

