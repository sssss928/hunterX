# Final-Layer Requirement Traceability

| Requirement | Evidence | Result |
|---|---|---|
| Immutable RC2 source/Windows base | SHA-256 freeze and RC2 provenance comparison | PASS |
| Real P0 at `_run_main()` boundary | `test_v052_final_layer_terminal_boundary.py` pre/post JUnit | PASS |
| Terminal classifier preserved | global terminal audit, 466/466 classified | PASS |
| Bounded safe recovery | terminal boundary, browser recovery, bootstrap suites | PASS |
| Manual close does not reopen | terminal boundary negative test | PASS |
| Protected/unknown submit never replayed | production iteration and attempt lifecycle suites | PASS |
| Wrong-target fix preserved | recovery and multitab suites | PASS |
| Full restart bootstrap preserved | browser bootstrap suite | PASS |
| TixCraft login transition | terminal-boundary and login recovery tests | PASS |
| TicketPlus lifecycle | 39 focused tests | PASS |
| Cross-platform completion/rearm | 349 cross-platform lifecycle tests | PASS |
| User dictionary settings/runtime path | immutable RC2 and RC3 dictionary acceptance | PASS |
| Applicable production consumers | AST plus dynamic production-handler tests | PASS |
| Hot reload | config save/reload/parser test | PASS |
| Online multiline and special characters | shared parser tests | PASS |
| KKTIX JSON safety | existing KKTIX dictionary regression | PASS |
| Dictionary excluded from protected flows | Final-Layer consumer audit and TicketPlus checkout negative control | PASS |
| Named-instance isolation | three-instance Edge run and multi-instance tests | PASS |
| Performance non-regression | balanced and high-density A/B | PASS |
| Two fresh full suites | post-release-engineering 1175/1175 twice | PASS |
| Short actual Edge integration | one instance plus three named instances | PASS with stated synthetic limitations |
| Exact source commit | clean-commit source verifier | REQUIRED/PASS in canonical build |
| Fresh-extract packaged smoke | canonical Windows builder | REQUIRED/PASS in canonical build |
| Source/Windows byte parity | joint pair verifier | REQUIRED/PASS in canonical build |
| Strict SHA-256 | exact two-asset manifest | REQUIRED/PASS in canonical build |
| 8-hour single instance | not executed | **NOT VERIFIED** |
| 8-hour three named instances | not executed | **NOT VERIFIED** |
| Release decision | production change required; 8-hour gates absent | **RC3** |
