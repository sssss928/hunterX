# HunterX v0.5.1 FINAL-AUDITED release notes

HunterX v0.5.1 FINAL-AUDITED 是以官方 HunterX v0.5.0 為唯一產品基底的
TicketPlus liveness、ownership 與 release-quality 更新。tickets_hunter
v2026.08.17 及兩份 v0.5.1 候選只用於唯讀交叉比較。

## 使用者可見改善

- TicketPlus 送單後立即建立單一 transaction owner；pending、blocked、queue
  或暫時 unknown 都不會在原 order route 重複送單。
- 判斷優先序固定為 confirmation → failure → queue → pending。所有可見 dialog
  都會被檢查，第二個 dialog 的失敗訊息也能否決第一個 dialog 的排隊文字。
- hidden template 不再因 body queue keyword 造成誤判；generic overlay 本身也
  不構成 queue evidence。
- pending probe 0.20 秒、blocked probe 0.15 秒、queue probe 1.0 秒；未到期時
  完全不執行 DOM/CDP probe。
- blocked 狀態最長 30 秒；queue 具有固定、不滑動的 600 秒 hard fuse。soft
  deadline 與 next probe 均不會超過 hard fuse。
- TicketPlus 導向同一 tab 的外部 Queue-it waiting room 時，已存在的唯一
  owner 會被保留；waiting room 期間只讀監控，不 click、不 reload、不導航。
  新開或無 owner 的 Queue-it tab 不會被任意歸屬 TicketPlus。
- 到期後走既有 mode-aware、partial-refresh-first guarded recovery，仍尊重
  onsale/leak-watch interval；interval=0 的停用語意不變。

## 保留的 HunterX 能力

既有日期、區域、票種、張數、`allow_less_tickets`、user dictionary、通知、
profiles、多實例、TixCraft、Ticketmaster、KKTIX、CAPTCHA/manual fallback、
checkout/payment protection 全部保留。沒有以 upstream 較弱架構替換。

## Release reproducibility

- GitHub Actions、PowerShell wrapper 與 Python builder 統一使用官方
  `hunterX_windows_0.5.0.zip`，SHA-256：
  `400fe2732a1289acab4035ba341511a7695b942eb11ba8d7622842c3d24b9d1b`。
- Windows 成品是 final v0.5.1 source overlay 到 verified v0.5.0 isolated
  dual-executable runtime，不宣稱 from-scratch PyInstaller rebuild。Overlay
  先由 Git `HEAD` 建立 commit-exact snapshot，因此兩個 `app_src` 與 source
  ZIP 使用相同 bytes，不受 Windows checkout line endings 影響。
- source 與 Windows ZIP 都經 path safety、denylist、CRC、extractability、
  source/version parity 檢查。
- 最終 hashes 位於 deliverables 旁的 `SHA256SUMS_v0.5.1_FINAL.txt`。

## 驗證摘要

- BASE-V050：762/762。
- CODEX-V051：778/778。
- CHATGPT-V051：768/768。
- FINAL coverage suite：788/788。
- Repeat liveness：380/380。
- Refresh/100,000-iteration、cross-platform、TixCraft/Ticketmaster focused
  groups：84/84、118/118、103/103。
- Ruff、mypy、Node syntax、compile/AST/structured config、pip-audit、benchmark
  與 release verifiers 均完成；完整細節見 `TEST_REPORT_v0.5.1_FINAL.md`。

## Safety boundary

此版本沒有加入 CAPTCHA、Queue-it、風控、帳號限制或付款繞過，也沒有 proxy
rotation、account pooling、bulk purchase 或 resale 功能。未執行真實購票、登入、
付款或平台 queue admission；第三方 DOM、庫存與網路狀態仍可能變動，因此不可能
保證任何一場活動一定購票成功。
