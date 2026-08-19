# HunterX v0.5.1 FINAL implementation diff

本文件說明最終版本每一項行為的來源。`BASE-V050` 是唯一產品基底；
`UPSTREAM-0817` 只提供行為參考。整合起點使用 `CODEX-V051` 的乾淨副本，
不代表其取代 HunterX v0.5.0 provenance。

## Source-of-truth map

| 最終區域 | 來源 | 判定 |
| --- | --- | --- |
| HunterX 平台架構、設定、多實例、dictionary | BASE-V050 | 原樣保留 |
| TixCraft/Ticketmaster/KKTIX production | BASE-V050 via CODEX-V051 | 無實質候選行為改寫 |
| TicketPlus outer-loop submission watcher | CODEX-V051 | 保留 deadline-before-probe 版本 |
| Queue soft/next hard-fuse cap | CHATGPT-V051 donor | 只採兩個 `min` invariant |
| 真實 order route fixture | CHATGPT-V051 donor + stronger assertions | 防止 vacuous pass |
| External waiting-room ownership | FINAL new design | 兩候選共同 P0 的第三方案 |
| Visible-body / raw multi-dialog veto | FINAL new design | 收窄 false queue |
| GitHub v0.5.0 baseline workflow | FINAL correction | 與既有 CODEX builder 同步 |
| Historical v0.5.0 docs | BASE-V050/CODEX-V051 | 保留，不採 CHATGPT 改寫 |
| FINAL audit/test/release reports | FINAL evidence | 新增並納入 package verifier |

## Production runtime changes after candidate selection

### `src/platforms/ticketplus.py`

- `_ticketplus_queue_evidence_is_active()`：除了 JS boolean，再逐一檢查 raw
  visible dialog text；任何 failure phrase 先否決 queue。
- `nodriver_ticketplus_check_queue_status()`：使用可見 `innerText`，只在瀏覽器
  不支援時 fallback 到 `textContent`；overlay 必須可見；回傳所有可見 dialog
  text；failure 時不回傳 queue dialog。
- `_ticketplus_handle_submission_watch()`：blocked next probe、queue soft deadline
  與 queue next probe 都不超過其 soft/hard boundary。既有 hard → soft →
  throttle → probe 順序未變。
- `nodriver_ticketplus_main()`：外部 waiting-room route 在已 armed submission
  時只執行 bounded watcher；不選票、不送單、不 reload、不導航。沒有 owner 時
 直接返回。

相對乾淨 CODEX 候選：`+52/-10`。沒有搬入 upstream 的 nested polling loop。

### `src/platform_engine.py`

- `DispatchDecision.platform_key` 讓 caller 使用 engine 經 ownership 判定後的
 平台，而不是再次只按目前 URL 計算。
- `_external_queue_owner(tab)` 只接受同一 tab、已有 previous provider URL，且
 恰好一個 `submission_pending` 或 `failure_retry_pending` owner。
- `before_dispatch()` 對 external queue 使用該唯一 owner 與 `PageClass.QUEUE`；
 零 owner 或多 owner 維持 `unsupported_host` 並 fail closed。

相對乾淨 CODEX 候選：`+39/-3`。

### `src/nodriver_tixcraft.py`

- main loop 從 `platform_decision.platform_key` dispatch。這一行是讓同 tab
  external queue 的 owner 可跨 outer-loop 延續的必要連接點。

相對乾淨 CODEX 候選：`+1/-1`。

## Release and build changes

### `.github/workflows/release.yml`

- v0.4.9 release tag、filename、SHA-256 與 builder argument 全部改為 verified
  v0.5.0 Windows runtime：
  `400fe2732a1289acab4035ba341511a7695b942eb11ba8d7622842c3d24b9d1b`。

### `scripts/build_windows_from_base.py`

- 既有 v0.5.0 hash-gated dual-runtime overlay/repack 流程保留。
- 有 Git metadata 時先建立安全的 `git archive HEAD` snapshot；Windows docs、
  assets、www 與兩個 `app_src` 全部取自 source release 相同 committed bytes，
  避免 Windows checkout mixed line endings 造成 byte provenance 分岔。無 Git
  metadata 的 uploaded source tree 維持 filesystem fallback。
- Windows package 的 top-level documents 加入四份 FINAL audit artifacts。

### `scripts/verify_release_archive.py`

- Windows archive 必須含有四份 FINAL audit artifacts，否則 fail closed。
- 既有 path safety、CRC、PE header、isolated runtime、embedded version 與
  frontend version gate 保留。

## Test changes

### `tests/test_v051_ticketplus_submission_liveness.py`

新增／強化：

- near-hard-fuse soft/next cap；
- 600 次 persistent queue absolute-fuse soak；
- 200 次 permanent blocked-dialog soft-deadline soak；
- 1,000 次 early throttle 與 1,000 次 external queue read-only loop；
- second visible dialog failure veto；
- JS all-dialog/visible-innerText contract；
- external queue read-only owner 與 hard-fuse-before-browser-work；
- confirmation clear、mode-aware failure retry、四種 pending outcome
  no-duplicate-submit matrix。

最終該檔 21/21 passed。

### `tests/test_platform_adapter_contract.py`

- 同 tab 已提交 TicketPlus → Queue-it 仍為 TicketPlus owner；
- unrelated tab Queue-it 仍 unsupported；
- failure retry 經 waiting room 與 provider return 不遺失。

### `tests/test_v050_ticketplus_refresh_and_outcome.py`

- 移除無效 `/tickets` suffix；新增 segment-count branch precondition、probe
  count 與 zero-submit assertion。

### `tests/test_workflows.py` / `tests/test_release_archive_verifier.py`

- 鎖定 v0.5.0 runtime filename/hash，明確拒絕 stale v0.4.9 reference；
- Windows fixture 強制包含 FINAL documents。

### `tests/test_windows_base_builder.py`

- 新增 commit snapshot regression：即使 checkout working-tree bytes 已變，
  release snapshot 必須精確等於 `git show HEAD:<path>`，不得靜默包入未提交或
  line-ending 不同的 bytes。

## Documentation changes

- 新增 `FINAL_CROSS_AUDIT_v0.5.1.md`。
- 新增 `TEST_REPORT_v0.5.1_FINAL.md`。
- 新增 `RELEASE_NOTES_v0.5.1_FINAL.md`。
- 新增本文件。
- 更新 v0.5.1 canonical changelog/release/test/build/README，使聲明與 final
  code、workflow、tests 一致。
- `BASELINE_PROVENANCE_v0.5.0.md`、`RELEASE_NOTES_v0.5.0.md`、
  `TEST_REPORT_v0.5.0.md` 等歷史文件沒有被重新定義。

## Deliberately not changed

- 沒有 wholesale copy upstream TicketPlus module。
- 沒有改 TixCraft/Ticketmaster/KKTIX production behavior。
- 沒有改 onsale/leak-watch user interval semantics 或 interval=0。
- 沒有增加 inner infinite loop、catch-up burst、busy polling。
- 沒有 queue/CAPTCHA/challenge/risk-control/payment bypass。
- 沒有 proxy rotation、account pooling、bulk-buy 或 resale functionality。
