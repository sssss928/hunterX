# Final-Layer Browser Recovery Audit

The Final-Layer change preserves the existing failure taxonomy: transport closed, target closed, execution-context lost, clean/manual close, and ordinary programming/DOM errors remain distinct.

The authoritative ownership chain is:

`platform helper -> terminal classifier -> run_runtime_iteration propagation -> _run_main lifecycle owner -> RuntimeHealthSupervisor policy -> BrowserSessionManager action`.

Safety invariants reverified:

- no helper swallows terminal failures;
- non-terminal errors are not converted into browser recovery;
- manual close returns STOP and cannot trigger restart;
- protected and submission-sensitive states cannot restart or replay;
- `SUBMIT_OUTCOME_UNKNOWN` cannot submit again;
- reacquire rejects empty target, wrong event, protected-route mismatch, and duplicate exact ambiguity;
- transport success requires an active target-level CDP proof, not cached URL state;
- safe restart uses the full initial bootstrap and is bounded by the existing supervisor budget;
- failed/exhausted recovery becomes controlled fail-closed state;
- no second refresh, restart, ownership, or attempt system was added.

The real log does not identify whether the browser process itself died. RC3 therefore does not guess: it inspects `BrowserExitState` and only allows the existing safe restart in an eligible safe context.

