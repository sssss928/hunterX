# HunterX v0.5.2 Final Root-Cause Report

## CI syntax failure

The latest GitHub edit moved most of `test_settings_server_shutdown_closes_listener` to module scope while leaving the final assertions indented. This produced one deterministic `IndentationError`, causing Ruff and pytest collection to fail. The fix restores the entire helper, startup, synchronization, request, shutdown, and listener assertions to the test function scope.

## Windows/Release failure

The workflows correctly fail closed around an immutable base: they download `hunterX_windows_0.5.2_rc2.zip` from tag `v0.5.2-rc2` and require SHA-256 `47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48`. The public repository has no such release. This is missing repository state, not a PyInstaller or purchasing-engine defect. The hash was not weakened and no fallback base was introduced.

## Missing release reports

GitHub web uploads after the clean RC3 commit omitted Round-1/Round-2 documents that `overlay_release_files()` still stages. An actual Windows build would therefore fail before packaging. The omitted documents were restored from immutable commit `86436fb55a94e779578fd520f03a5d9efff95011` and remain claims subject to current tests.

## Dictionary report

The alleged all-platform inability to read `advanced.user_guess_string` was not reproducible. Settings save/reload, the shared parser, seven text-question-capable handler families, complete online multiline input, and special-character cases passed. Therefore no production dictionary architecture was rewritten.

## Performance report

Initial low-density variance included several positive deltas. High-density paired repeats reduced the five investigated median deltas to +0.51% through +1.79%; a separate 10-pair wrong-target run measured median -0.26% and p95 +0.08%. No actionable production regression was demonstrated.
