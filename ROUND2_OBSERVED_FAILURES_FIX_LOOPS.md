# HunterX v0.5.2 RC2 — Observed Failures and Fix Loops

Release status: **RC2 — 8H SOAK NOT VERIFIED**

This report lists observed failures. None was hidden with skip, xfail, test
deletion, assertion weakening, unrelated test-count inflation or an infinite
retry/restart.

## Immutable Round-1 negative controls

Round 2 used the unpacked, read-only Round-1 v0.5.2 RC as its development base.
The baseline passed 842 tests. P0/P1 negative-control JUnit and input hashes are
retained under `work/v052/round2/evidence/r2-0`. The candidate was not rebuilt
from v0.5.1.

## Production Stop-the-Line loops

| Observed failure | Root cause | Production correction | Post-fix evidence |
|---|---|---|---|
| Readable URL could remain “healthy” while an expected action never progressed | Only loop/readability timestamps existed; no exact expected transition | Passive expectation keyed by tab, attempt/generation, owner/token, transition and deadline | 14 focused observer cases, adjacent lifecycle/production suites, fresh-process repeats |
| Wrong/empty/ambiguous tab could be reacquired; dead cached target could report rebind success | Same-platform fallback and browser-control-only proof | Unique canonical or exact saved target ID; bounded target-level CDP proof before committing active tab | 20 focused recovery, 20/20 critical repeats, 56 adjacent |
| Restart skipped initial prerequisites | Reduced restart factory bypassed block/homepage/platform/attach/resize sequence | One full bootstrap owner shared by initial launch and safe restart | 44 focused/bootstrap and adjacent recovery/login/resource suites |
| Production and soak exercised different lifecycle paths | Soak manually drove a synthetic PlatformEngine lifecycle | One `run_runtime_iteration` used by application and soak | 25 focused, 122 adjacent, 75/75 fresh invocations, actual Edge loopback |
| TicketPlus watcher N mutated attempt N+1 after an await | Shared state reread without entry owner snapshot | Exact attempt/token snapshot and post-await fence; stale path diagnoses and returns | 39 focused; deterministic race 20/20 fresh processes |
| TixCraft explicit tabs shared process fallback state | Explicit-tab dispatch could select `_default_state` | Explicit tab always resolves its PlatformEngine-owned mapping | 219 focused TixCraft, 110 repeated, 92 adjacent |
| TixCraft local reset left central submit claim stuck | Local watcher was cleared without exact central release | Exact attempt/generation/token rejected-submit release and matching proof removal | 56 rejection/bridge/recovery; race 20/20; 260 adjacent |
| Delayed TixCraft captcha/unknown callback released or contaminated N+1 | Async callback reread current shared submit/flag state | Call-time immutable submit snapshot; all reset/flags fenced to exact context | Production callback regression, 56 focused, 20/20 fresh, 260 adjacent |
| Cityline popup continued on new tab but state remained bound to old tab | Tab adoption did not rebind PlatformEngine dispatch state | Adopted tab becomes the exact dispatch owner before state mutation | Cityline/tab-transition and cross-platform adjacent suites |
| Terminal disconnects were swallowed as ordinary DOM fallbacks | Broad browser handlers lacked terminal-first classification | Terminal classifier is first effective action in every audited handler | 466/466 classified, zero manual-review; global and per-platform audits |

## Cross-platform user-dictionary loop

Initial candidate negative control produced **18 failed, 3 passed**. The defects
were independently reproducible:

1. the common parser accepted only the legacy quoted JSON fragment;
2. newline/semicolon/full-width-semicolon/list input, quotes, backslashes and
   `None` were mishandled;
3. only the first line of the online answer file was read;
4. frontend display/save stripped or corrupted delimiters/escaping;
5. FamiTicket read a nonexistent `area_auto_select.area_answer` key;
6. HKTicketing and iBon split raw storage syntax and could type quote marks;
7. KKTIX interpolated raw answer text into JavaScript;
8. TeamEar and Ticketek Australia host dispatch was missing.

The correction established one shared lossless parse/serialize/merge contract,
applied it at settings migration/save/hot-reload boundaries, used it in every
text-question-capable production family and JSON-encoded all JavaScript-bound
answers. Actual platform-path tests were then expanded to 26 cases.

Post-fix results: 26 focused; 20 fresh processes = 520/520; after parser
fast-path optimization another 520/520; adjacent suite 441 passed twice; final
full suites 1150 passed twice. Cityline, FunOne and FANSI GO have no production
text-question dictionary field, so no dictionary value was injected into
unrelated CAPTCHA/login/queue/risk/payment behavior.

## Integration/test-contract failures

- Two legacy TixCraft tests assumed an AREA URL string alone cleared submit.
  They now first assert route-only protection, then provide positive interactive
  AREA health through the production reconcile path. Production was not
  weakened.
- The first pre-dictionary Round-2 full run stopped at 1122 passed, 2 failed
  because embedded direct scripts seeded the retired process fallback. Tests
  were corrected to seed the real explicit-tab state and the full suite was
  restarted.
- After dictionary production tests were added, ten legacy TicketPlus tests
  failed only when run after the adapter contract file. Isolation proved that
  the test file leaked PlatformEngine's task-local ContextVar. A file-local
  autouse fixture now clears the test engine before/after each adapter test;
  production code and assertions were unchanged. The combined set passed 65
  and both final 1150-test processes passed.
- During the terminal audit, an ambiguous zero-context patch briefly appended
  guards after KKTIX EOF and produced `IndentationError`. Work stopped, the two
  files were restored byte-for-byte, and changes were reapplied per unique AST
  handler with compile/audit after each batch.

## Runner failures and corrections

- A system `python` alias and later an absent PATH `node` were rejected as
  evidence; formal commands use recorded absolute Python 3.11 and Node paths.
- A focused command named a nonexistent TicketPlus test file; it was corrected
  to the actual test modules and rerun.
- An exception-auditor invocation used unsupported `--json`; it was rerun with
  the supported no-argument interface and reported 466/466.
- The first final PowerShell parallel gate omitted the call operator before
  quoted executable paths and produced four parser errors. No product test ran;
  the commands were corrected with `&` and all four gates passed.
- A documentation scan passed a Unix-style `*.md` positional glob to Windows
  `rg` and exited before scanning. It was rerun with `-g '*.md'`; the only FINAL
  artifact names found were historical Master Prompt requirements, while the
  active RC2 traceability correctly remained pending until artifact build.
- Scanning the entire temporary Python environment reported 17 advisories in
  the runner's own old pip/setuptools/unrelated cryptography. The release
  workflow audits the product lock with `pip-audit -r requirement.txt`; that
  authoritative command passed with no known vulnerabilities.

## Remaining limitation

Local actual-browser evidence uses Edge against loopback synthetic pages. Live
third-party purchase/payment/queue/CAPTCHA/challenge/risk-control behavior and
the required eight-hour soaks were not executed.

> **8H SOAK NOT VERIFIED — RC2 ONLY**
