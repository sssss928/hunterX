# HunterX v0.5.1 最終四方交叉稽核

稽核日期：2026-08-19（Asia/Taipei）
結論：以 `CODEX-V051` 作為最低風險候選基底，僅選擇性採用
`CHATGPT-V051` 的 queue deadline cap 與真實 route fixture，再加入兩候選
都缺少的 external waiting-room ownership 修正。最終版本不是 upstream、
不是整份搬用任一候選，也沒有削弱 HunterX v0.5.0 既有架構。

## 來源識別與不可混淆標籤

| 標籤 | 唯一用途 | SHA-256 |
| --- | --- | --- |
| `BASE-V050` | 唯一 HunterX 修改基底 | `7dbcf41ff6591afc9e2b43d52adf4970e69a84e10347aea2b1b4b81b4564f4c4` |
| `UPSTREAM-0817` | 唯讀功能／修正參考 | `c39d1645db44e0585ffc518279da7907b930d54ed56cede1f2899dd460c78b90` |
| `CODEX-V051` | 第一候選，唯讀比較後選為整合起點 | `718e7bfab6e449660de61614c335e9396a06b13cc140057f4e1db9d48ee9410b` |
| `CHATGPT-V051` | 第二候選，唯讀 donor | `f8aa81b6254928694980ff36430e114f1ba74eaedeba94ba6b860ca6340e7c2a` |

Master prompt SHA-256：
`dfad657c3a3104b2e2b4223bfadac2e14c5303937fe6763a5823d57e90ee939b`。
最終四方任務文字 SHA-256：
`78dc1295269853a3100e49818889906e5a5452c5991e36cc548a8ad615d4441f`。

四個原始 ZIP 在第一階段均保持唯讀；最終整合使用重新從原始
`CODEX-V051` ZIP 解壓的乾淨 tree，沒有使用測試後受污染的比較目錄。

## Executive decision

`CODEX-V051` 保留較好的 deadline-before-probe 順序、較完整的 liveness
tests、v0.5.0 Windows builder/verifier 與未改寫的歷史 v0.5.0 文件，因此是
最低風險起點。`CHATGPT-V051` 的優點只窄取兩項：

1. queue soft deadline 與 next probe 使用 `min(..., queue_deadline)`；
2. 將無效 `/order/event/session/tickets` 測試改成真實
   `/order/event/session`，並加入 branch-entry 證據。

下列修正是整合階段的新設計，不來自任一候選：

- same-tab external Queue-it route 的唯一既有 owner 綁定；
- 未擁有／跨 tab／多 owner 的 Queue-it 頁維持 unsupported；
- external waiting room 只允許 bounded read-only watcher；
- visible `innerText` 與 raw all-visible-dialog failure veto；
- GitHub workflow 與 v0.5.0 Windows builder SHA/filename 同步；
- final reports 成為 Windows package 的 verifier-required files。

## Winner matrix

| 維度 | BASE-V050 | UPSTREAM-0817 | CODEX-V051 | CHATGPT-V051 | FINAL-AUDITED winner |
| --- | --- | --- | --- | --- | --- |
| HunterX 架構保存 | 原始 | 不適合作 base | 完整 | 完整 | CODEX/FINAL |
| order route 單一 submit owner | 不完整 | 內層 loop | 完整 | 完整 | CODEX/FINAL |
| hard/soft deadline 在 probe 前 | 不完整 | 否 | 是 | non-queue 否 | CODEX/FINAL |
| queue 600s non-sliding fuse | 不完整 | bounded 內層 loop | 是 | 是 | FINAL |
| soft/next cap 到 hard fuse | 否 | 不適用 | 否 | 是 | CHAT donor/FINAL |
| blocked 30s 絕對界線 | 否 | 不同流程 | 是 | 是 | CODEX/FINAL |
| all visible dialogs | 否 | 有限 | 是 | 是 | FINAL |
| second-dialog failure veto | 未鎖定 | 不完整 | helper only | helper only | FINAL |
| hidden body template 排除 | 否 | 否 | 否 | 否 | FINAL |
| external Queue-it ownership | 被 reset | 無 HunterX owner | 被 reset | 被 reset | FINAL |
| unrelated Queue-it fail closed | unsupported | 不適用 | unsupported | unsupported | FINAL |
| outer-loop/no nested polling | 是 | 否 | 是 | 是 | CODEX/FINAL |
| monotonic clock | 部分 | `time.time` | 是 | 是 | CODEX/FINAL |
| refresh interval=0 | 是 | 可能 reload | 是 | 是 | HunterX/FINAL |
| per-tab multi-instance state | 是 | global dict | 是 | 是 | HunterX/FINAL |
| dictionary / allow-less 保留 | 是 | 缺少 HunterX 能力 | 是 | 是 | HunterX/FINAL |
| TixCraft/Ticketmaster/KKTIX | 原始 | 不同架構 | 未實質改動 | 未實質改動 | HunterX/FINAL |
| v0.5.0 Windows provenance | 基準 | 不適用 | builder 是、workflow 否 | 否 | FINAL |
| 歷史 v0.5.0 文件保存 | 原始 | 不適用 | 僅換行差異 | 實質改寫 | CODEX/FINAL |
| focused test 深度 | 13 baseline | 不可比 | 23 pass | 15 pass | FINAL 21-case file |
| release ZIP hygiene | 乾淨 | 乾淨 | 乾淨 | 乾淨但報告雜訊多 | FINAL verifier |

## 候選評分

以下採用主要候選稽核的統一九類評分；分數是在任何最終修正前所得。

| 類別 | CODEX-V051 | CHATGPT-V051 | FINAL-AUDITED | 主要證據 |
| --- | ---: | ---: | ---: | --- |
| Correctness | 8.4 | 7.5 | 9.6 | precedence、真實 route、external owner |
| Liveness | 8.0 | 7.0 | 9.7 | deadline-before-probe、30s/600s soak |
| Race safety | 8.8 | 6.5 | 9.6 | same-tab unique owner、no duplicate submit |
| Refresh precision | 9.3 | 9.0 | 9.5 | v0.5.0 scheduler/interval=0 保留 |
| Regression safety | 9.4 | 8.0 | 9.6 | 788 full、三個跨平台 focused groups |
| Maintainability | 8.4 | 8.0 | 9.2 | named state、最小差異、來源文件化 |
| Test quality | 7.6 | 7.0 | 9.6 | negative/repeat/soak/dispatcher/DOM fixture |
| Performance | 9.0 | 8.5 | 9.4 | throttle-before-probe、benchmark、無 nested loop |
| Architecture consistency | 8.5 | 6.5 | 9.5 | PlatformEngine owner + existing adapter/state |
| **Overall** | **86/100** | **76/100** | **96/100** | 所有 final gate 與 artifact verifier 通過 |

## P0 / P1 / P2 findings and disposition

### P0 — external waiting room 清除 transaction ownership

兩候選在 `nodriver_tixcraft.py` 以目前 URL 重算平台；`queue-it.net` 不在
registry，`PlatformEngine.before_dispatch()` 因此 reset TicketPlus
`platform_data`。返回 order route 後可再次進入 submit，形成 duplicate-submit
風險。FINAL 只在同一 tab 已存在且恰好一個 unresolved owner 時綁定 queue，
並由 `DispatchDecision.platform_key` 持續 dispatch 該 adapter。未擁有或有歧義
時仍 fail closed。

### P0 — release workflow 與 builder baseline 不一致

CODEX builder 要求 v0.5.0 與 SHA-256 `400fe...`，但 workflow 下載 v0.4.9，
Action 必然被 filename/hash gate 拒絕。CHATGPT workflow/builder 則仍以 v0.4.9
互相配合，但違反本任務 provenance。FINAL 將 workflow、PowerShell wrapper 與
Python builder 統一到 verified v0.5.0 archive。

### P1 — queue deadline invariant

CODEX 在接近 hard fuse 時可寫出超過 hard deadline 的 soft/next state；雖然
hard-first check 避免實際多 probe，state invariant 仍不精確。FINAL 採用
CHATGPT 的 `min` 寫法，同時保留 CODEX 的 hard → soft → throttle → probe
順序。

### P1 — vacuous route test

CODEX 舊測試使用多一段 `/tickets`，production segment-count gate 使測試沒有
進入 order branch。FINAL 改成真實 route，斷言 segment count、恰好一次 probe
與零 submit。

### P1 — source/Windows byte provenance

第一次成功 Windows build 的獨立 parity check 發現兩個 `app_src` 各有三個
檔案只有 mixed LF/CRLF bytes 不同；code diff 無語意差異，但不符合 commit-exact
provenance。根因是 source ZIP 使用 `git archive` bytes，而 Windows overlay 使用
checkout working-tree bytes。FINAL builder 先安全建立 Git `HEAD` snapshot，再由
同一份 committed bytes 建立 docs、assets、www 與兩個 `app_src`；無 `.git` 的
uploaded source tree 則維持安全 fallback。新增 regression 證明 working tree
與 commit 不同時，builder 仍使用 committed bytes。

### P1 — multi-dialog/hidden-body false queue

兩候選 production JS 已遍歷 visible dialogs，但原測試多為 fabricated helper
dict；整份 body `textContent` 也可讀到 hidden template。FINAL 使用 visible
`innerText`、回傳 raw visible dialog texts，並在 Python boundary 再做 failure
veto。第一個 dialog benign、第二個 dialog failure 的 fixture 已鎖定。

### P2 — release/test evidence consistency

CHATGPT 改寫歷史 v0.5.0 provenance/release/test 文件，canonical BUILD_INFO 與
builder 包含項目也不一致。FINAL 保留 CODEX/BASE 歷史文件，僅更新 v0.5.1
文件，並由 archive verifier 強制要求四份 FINAL 文件。

## Refresh ownership map

| 平台／route | Owner | Trigger / interval | Deadline / guard | FINAL 狀態 |
| --- | --- | --- | --- | --- |
| 全平台 `refresh_datetime` | central gate in `nodriver_tixcraft.py` | user target time | platform-aware protected-route guard | v0.5.0 原樣保留 |
| TixCraft/Ticketmaster onsale | platform + `RefreshCoordinator` | onsale interval | `ReloadGuard`; checkout/payment 禁止 | 原樣保留 |
| TixCraft/Ticketmaster leak-watch | `LeakWatchScheduler` per tab | leak interval | generation/deadline；no catch-up burst | 原樣保留 |
| KKTIX provider queue | provider page | provider controlled | bot 不 reload queue | 原樣保留 |
| KKTIX safe retry | KKTIX + guarded reload | active mode interval | protected route guard | 原樣保留 |
| TicketPlus inventory | `_ticketplus_refresh_when_due` | active mode interval | partial-first；interval=0 disabled | 原樣保留 |
| TicketPlus submitted | submission watcher | 0.20s pending / 0.15s blocked | 30s soft before probe | CODEX + final cap |
| TicketPlus queue | same watcher | 1.0s probe | fixed 600s hard fuse | FINAL |
| External waiting room | same-tab unique prior owner | read-only outer-loop watcher | no click/reload/navigation; same fuse | FINAL new design |
| TicketPlus failure retry | guarded retry owner | onsale/leak interval | partial-first + ReloadGuard | 原樣保留並補測 |

## Regression and safety conclusion

相對 `BASE-V050`，TixCraft、Ticketmaster、KKTIX、dictionary、profile hot reload、
multi-instance 與既有 refresh scheduler 沒有被候選的 wholesale code 取代。
FINAL 的 cross-platform runtime 變更只在 shared engine 加入受限的 same-tab
external queue ownership；118-case contract/multi-instance group、103-case
TixCraft/Ticketmaster group與 84-case refresh/100,000-iteration group全數通過。

本稽核不宣稱可保證第三方網站永遠不改版，也不宣稱購票一定成功。沒有新增
CAPTCHA、Queue-it、風控、帳號限制或付款繞過；外部 waiting room 明確保持
平台控制與 read-only。
