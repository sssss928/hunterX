# HunterX v0.5.2 browser ownership contract

HunterX supports **one automation-owned browser tab per named instance/profile**.
The production runtime has one authoritative tab owner, one `PlatformEngine`
attempt lifecycle and one refresh/submission owner for that instance.

To run three concurrent purchase attempts, start three named HunterX instances
with three independent profiles. Each instance owns its own browser process,
tab, state files, refresh coordinator and submission lifecycle.

HunterX does not claim concurrent automation of three tabs in one browser.
Additional manually opened tabs are diagnostic-only: HunterX reports matching
same-platform extras, but does not activate, close, refresh, click or dispatch
automation into them. This prevents an unrelated event tab from being adopted
as the recovery target and prevents duplicate submissions across tabs.

Test and soak evidence must keep these topologies separate:

- **Three named instances:** supported concurrent-purchase architecture.
- **Three tabs in one browser:** unsupported for concurrent automation; only
  ownership isolation and extra-tab diagnostics are tested.
- **Three tab objects from different platforms:** useful state-isolation unit
  coverage, but not evidence for either topology above.

Recovery may rebind only to a uniquely proven owned target: a unique canonical
target match, or the saved owned target identity. Same platform alone is never
sufficient. Ambiguous or wrong-event candidates fail closed and remain
untouched.
