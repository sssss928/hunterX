# HunterX v0.5.1 validation report

Validation date: 2026-08-19 (Asia/Taipei)  
Environment: Windows, CPython 3.11.9 x64

This canonical summary reflects the completed FINAL-AUDITED integration. Exact
commands, candidate negative controls, observed intermediate failures and
packaging checks are in `TEST_REPORT_v0.5.1_FINAL.md`.

## Provenance

The only modification base was the official HunterX v0.5.0 source:

- commit: `a68a8fefd67420afd1925eb6f62e89bfe791ec45`
- source ZIP SHA-256:
  `7dbcf41ff6591afc9e2b43d52adf4970e69a84e10347aea2b1b4b81b4564f4c4`
- Windows ZIP SHA-256:
  `400fe2732a1289acab4035ba341511a7695b942eb11ba8d7622842c3d24b9d1b`
- master instruction SHA-256:
  `dfad657c3a3104b2e2b4223bfadac2e14c5303937fe6763a5823d57e90ee939b`

Tickets Hunter v2026.08.17 and the two v0.5.1 candidates were read-only
comparison sources. Neither candidate nor upstream became the HunterX base.

## Final behavior

- The real TicketPlus `/order/<event>/<session>` route has exactly one active
  submission owner before popup sanitation or ticket selection can run.
- Deadline order is hard fuse, soft deadline, throttle, then DOM/CDP probe.
- Queue ownership has a fixed non-sliding 600-second hard fuse; its soft and
  next-probe deadlines cannot exceed that fuse.
- All visible dialogs participate in failure detection; failure vetoes queue.
  Hidden body templates do not create queue evidence.
- A same-tab external Queue-it redirect preserves exactly one established
  platform owner. It stays read-only and bounded. Unowned or ambiguous external
  waiting rooms stay unsupported.
- Guarded recovery remains mode-aware and partial-refresh-first. User on-sale
  and leak-watch intervals are not reused as submission probe intervals.
- TixCraft, Ticketmaster and KKTIX production logic remains inherited from the
  HunterX v0.5.0 base.

## Deterministic results

- BASE-V050 full suite: 762/762 passed.
- CODEX-V051 full suite: 778/778 passed.
- CHATGPT-V051 full suite: 768/768 passed.
- FINAL TicketPlus liveness file: 21/21 passed.
- FINAL complete suite with coverage: 788/788 passed in 163.17 seconds.
- Pre-soak 19-case liveness suite repeated 20 times: 380/380 passed.
- Persistent queue soak: 600 positive probes, fixed deadline, zero boundary
  probe at 700.0 seconds.
- Permanent blocked-dialog soak: 200 probes before 130.0 seconds, zero boundary
  probe.
- Early throttle and external-queue loops: 1,000 iterations each with zero
  premature outcome probes.
- Refresh/100,000-iteration soak group: 84/84 passed.
- Cross-platform/multi-instance/KKTIX group: 118/118 passed.
- TixCraft/Ticketmaster core group: 103/103 passed.
- pytest-benchmark: 6/6 passed.

## Quality and security gates

- `compileall`, AST (112 Python files), JSON (2), TOML (1), YAML (7): passed.
- Ruff: passed.
- Configured strict mypy scope: 22 source files, passed.
- Node.js syntax: 5 JavaScript files, passed.
- `git diff --check`: passed; Windows line-ending warnings were informational.
- pip-audit against the locked Windows runtime dependencies: no known
  vulnerabilities.
- Bandit high-severity gate: no high findings. A stricter medium scan reports
  the inherited, intentional `marshal.loads` in the hash-verified PyInstaller
  repacker; this is disclosed rather than suppressed.

## Release gates

The Windows package is produced by overlaying this source on the exact verified
v0.5.0 dual-runtime archive. The release workflow, Python builder and PowerShell
wrapper all use the same v0.5.0 filename and SHA-256. When Git metadata is
available, the overlay uses a commit-exact `git archive HEAD` snapshot so both
`app_src` trees match the source ZIP bytes. Source and Windows ZIPs
are checked for path safety, denylisted runtime state, CRC, extractability,
embedded 0.5.1 metadata and source parity. Final hashes are in the external
`SHA256SUMS_v0.5.1_FINAL.txt` manifest.

## Boundary of validation

No real purchase, login, inventory submission, CAPTCHA solution, queue bypass
or payment was performed. Tests validate deterministic ownership, liveness,
refresh safety and packaging; they cannot guarantee availability or a future
third-party DOM. CAPTCHA, challenge, waiting-room admission and payment remain
manual/platform-controlled boundaries.
