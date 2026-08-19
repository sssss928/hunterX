# HunterX v0.5.1 FINAL-AUDITED test report

日期：2026-08-19（Asia/Taipei）
主機：Windows x64
Python：CPython 3.11.9
pytest：9.1.1

## Final status

`FINAL DEFINITION OF DONE` 的 deterministic code、liveness、regression、
performance、source archive 與 Windows archive gates 已完成。最終 full suite
為 **788 passed / 0 failed / 0 skipped**，coverage-enabled 執行時間 163.17 秒。

驗證範圍不包含真實購票、帳號登入、CAPTCHA、queue admission 或付款；詳細
boundary 見文末。

## Phase 1 — isolated READ-ONLY audit

四個 ZIP 分別解壓到隔離目錄。原始附件未修改；所有結論先從原 ZIP bytes、
diff 與 fresh tests 取得，再進入整合階段。

| Source | Focused | Full | Result |
| --- | ---: | ---: | --- |
| BASE-V050 | baseline focused | 762/762 | pass |
| CODEX-V051 | 23/23 | 778/778 | pass |
| CHATGPT-V051 | 15/15 | 768/768 | pass |

原始 source ZIP 都是單一安全 root、CRC 正常，且沒有 `.git`、pyc、cache 或
runtime log。測試會在 extraction tree 產生 coverage/log，因此所有 final build
都改用重新從原始 CODEX ZIP 解壓的乾淨 tree。

## Negative controls

### New v0.5.1 behavior against BASE-V050

- hard deadline before extra probe：如預期失敗；BASE 在 boundary 多 probe。
- blocked soft deadline before extra probe：如預期失敗；BASE 未在 30 秒結束。
- real order-route watcher-first ownership：如預期失敗；BASE 沒有同等 watcher。
- CHATGPT 的六個新增 cases 對 BASE：4 failed、2 passed；兩個 pass 是 BASE
  已具有的 hot-loop gate 與 helper-level failure veto，不當作新增偵測能力。

### FINAL-only regression cases against candidates

- CODEX-V051：4/4 如預期失敗，分別證實缺少 near-hard cap、second raw dialog
  veto、external hard fuse reachability、external dispatcher ownership。
- CHATGPT-V051：3/4 如預期失敗；soft cap 原本已通過，其餘三個缺口被偵測。

Negative controls 只在隔離候選 tree 執行，沒有將故意失敗的測試留在 final
branch。

## Final targeted and stress results

| Gate | Result | What it proves |
| --- | ---: | --- |
| `test_v051_ticketplus_submission_liveness.py` | 21/21 | submission/queue/blocked/DOM/external route |
| Pre-soak 19-case file × 20 runs | 380/380 | no intermittent liveness regression |
| Persistent queue deterministic soak | 600 probes + boundary | deadline fixed at 700.0; zero boundary probe |
| Permanent blocked deterministic soak | 200 probes + boundary | deadline fixed at 130.0; zero boundary probe |
| Early pending loop | 1,000 iterations | zero premature DOM/CDP probes |
| External waiting-room loop | 1,000 iterations | read-only owner; zero premature probe/action |
| Refresh/long-run group | 84/84 | includes 100,000-iteration scheduler/liveness tests |
| Cross-platform/multi-instance group | 118/118 | KKTIX, adapter, state/tab isolation |
| TixCraft/Ticketmaster core group | 103/103 | navigation, soft block, submit recovery |
| pytest-benchmark | 6/6 | pure hotpath benchmark execution |

Relevant commands used `-p no:cacheprovider -o addopts=""` for focused runs so
coverage artifacts did not alter the isolated evidence trees. The final full run
used repository-default strict markers/config and coverage settings.

## Full suite progression

1. Selected CODEX base after integration fixes: 785/785 passed.
2. Added explicit persistent queue and permanent blocked soak tests: dedicated
   file 21/21 passed.
3. Post-soak repository-default coverage run: 787/787 passed in 159.82 seconds.
4. Added the commit-snapshot byte-provenance regression and reran the final
   repository-default coverage suite: **788/788 passed** in 163.17 seconds.
5. Coverage total: 33% across the inherited 23,548-statement production scope.
   Low aggregate coverage is largely from inherited platform modules; release
   acceptance is based on deterministic focused/full regression, not a newly
   invented coverage threshold.

## Static, type, syntax and structured-data gates

| Command/gate | Result |
| --- | --- |
| `python -m compileall -q src tests scripts` | pass |
| AST parse all project Python | 112 files, pass |
| TOML / JSON / YAML parse | 1 / 2 / 7, pass |
| `python -m ruff check src tests scripts` | pass |
| `python -m mypy` | configured strict scope, 22 files, pass |
| bundled Node `--check` | 5 JavaScript files, pass |
| `git diff --check` | pass; CRLF warnings informational only |

Node was not on the normal `PATH`; the gate used the Codex bundled Node binary.
This is recorded as an executed PASS, not a skip.

## Security and dependency gates

- `python -m pip_audit -r requirements-lock-windows-py311.txt`：0 known
  vulnerabilities，exit 0。
- `python -m bandit -r src scripts -c pyproject.toml -ll`：0 high、1 medium、
  257 low in metrics。唯一 medium 是既有
  `scripts/repack_pyinstaller_entrypoint.py` 的 `marshal.loads`。該程式只處理
  filename + SHA-256 驗證後的官方 v0.5.0 PyInstaller archive，並在 repack 後
  檢查 entrypoint code object；此 finding 被揭露而非用 `nosec` 隱藏。
- High-only Bandit release threshold 為 0 high，符合 gate；medium finding 是
  封裝工具的已知 residual risk，不在購票 runtime 接收不受信任 marshal bytes。

## Performance evidence

`tests/benchmarks/audit_performance.py` 以 25 samples、每 sample 500 iterations
完成 18 個 benchmarks；pytest-benchmark 另完成 6/6。代表性結果：

| Benchmark | p50 wall time per 500 iterations |
| --- | ---: |
| `same_document_no_ticket` | 0.4282 ms |
| `interval_due` | 1.0050 ms |
| `url_classification` | 13.7916 ms |
| `refresh_coordinator_idle` | 8.2368 ms |
| `tab_state_intents_1000` | 43.7123 ms |

新 TicketPlus watcher 在未到 `next_probe_at` 時只做 monotonic/state 判斷；
1,000-iteration test 確認 DOM/CDP probe 為零。沒有 inner polling loop 或
catch-up burst。

## Refresh and ownership regression

- Central `refresh_datetime` gate 仍在 platform dispatch 前持有 single owner。
- TixCraft/Ticketmaster `RefreshCoordinator`、`LeakWatchScheduler` 與
  `ReloadGuard` production bytes/semantics 沿用 BASE-V050。
- KKTIX provider queue 不由 bot reload，safe retry 仍使用 active mode。
- TicketPlus inventory refresh 維持 partial-first、guarded full reload fallback。
- `auto_reload_page_interval=0` 與 leak interval=0 的停用語意未變。
- TicketPlus submitted/queue route 的 owner 是 submission watcher，不與 user
  refresh interval 共用 probe clock。

## Release/build verification

Windows baseline：`hunterX_windows_0.5.0.zip`，SHA-256
`400fe2732a1289acab4035ba341511a7695b942eb11ba8d7622842c3d24b9d1b`。

Release workflow、PowerShell wrapper、Python builder 三者使用同一 baseline。
有 Git metadata 時，builder 先建立安全的 commit-exact `git archive HEAD`
snapshot；Windows docs 與兩個 `app_src` 因此和 source ZIP 使用相同 bytes。
Final build/verifier gates：

- baseline filename/hash/required dual-runtime layout；
- CPython 3.11 PyInstaller entrypoint repack for both executables；
- archive safe paths、case-insensitive duplicate、denylist、CRC、extractability；
- `settings.exe` / `nodriver_tixcraft.exe` PE headers；
- `_settings_internal` / `_nodriver_internal` isolation；
- 兩份 external `app_src` 的 final source parity；
- embedded `APP_VERSION = "0.5.1"` 與 frontend `HunterX (0.5.1)`；
- source ZIP single root `hunterX-0.5.1/`，tracked-content exact match；
- final audit/release/test/diff documents present；
- packaged executable smoke launch on Windows。

成品 SHA-256 不寫回 source ZIP，以避免 source ZIP 包含自己 hash 的 circular
dependency；完整 manifest 與四份報告一起放在 output 目錄。

## Observed failures and fix loop

所有非 PASS 都有保留原因，不被改寫成成功：

1. 第一個 full-test orchestration 使用尚未建立 parent 的 `--basetemp`，造成
   90 個 setup errors；建立 parent 後相同來源 762/778/768 suites 全通過。
   根因是 test runner path，不是 production defect。
2. 第一個 candidate negative-control process 使用不存在 workdir，Windows
   `CreateProcess` 回傳 error 267；從穩定 cwd 重跑後得到預期 4/4 與 3/4
   candidate failures。根因是 runner cwd。
3. Bandit medium 如上揭露；沒有 suppress 或假裝 exit 0。
4. 初次完整 final suite 為 785 passed；新增兩個 master 要求的 explicit soak
   tests 後重新跑到 787 passed。
5. 第一個成功 Windows artifact 通過內建 verifier 與 smoke，但獨立 byte
   parity check 發現兩個 runtime 各三個 changed Python files 只有 LF/CRLF
   不同。這不是語意差異，但不接受為 commit-exact。Builder 改為由 Git
   `HEAD` snapshot overlay，新增 regression，並重新建立與驗證兩個 artifacts。

## Archive hygiene and cleanup

封裝輸入排除 `.git`、`__pycache__`、pyc/pyo、coverage、pytest/mypy/ruff cache、
`dist`、`build`、logs、instances、profiles、settings/config、cookies、credentials
與 private keys。測試生成的 coverage、bytecode 與 runtime log 在 final commit
及 source build 前清理；清除的是可重新產生的測試暫存，沒有刪除使用者資料。

## Limitations / not tested

沒有執行真實活動購票、真實帳號登入、庫存鎖定、送單、付款或網站 queue
admission。沒有測試或實作 CAPTCHA/challenge/Queue-it/risk-control bypass、proxy
rotation、account pooling、bulk purchase、resale 或 automated payment。

因此本報告證明的是 deterministic state ownership、deadline liveness、無重複
submit、refresh safety、cross-platform regression 與 packaging integrity；不保證
票券供應、網路品質、第三方 DOM 永久不變或任何特定場次一定成功。
