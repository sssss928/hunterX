# HunterX v0.5.2 最終完整開發任務
## Lifecycle / Long-Run Reliability / Cross-Platform Purchase Continuation / Performance-Preserving Upgrade

> 本文件是給 Codex 直接執行的「單次完整任務 Master Prompt」。
> 基準版本：HunterX v0.5.1 FINAL-AUDITED
> 目標版本：HunterX v0.5.2
> 核心原則：只能變好，不能變差；優先修正真實缺陷，不為了改版而重寫既有核心。

---

# 0. 任務執行模式：單次完整執行，不得中途草率停下

你現在是 HunterX v0.5.2 的 Senior Software Architect、Python/asyncio/browser automation engineer、test engineer 與 release engineer。

本任務不是「提供建議」，而是要實際完成：

1. READ-ONLY 稽核 v0.5.1。
2. 找出已知問題、使用者實際重現問題，以及 source 中尚未被使用者發現的相關缺陷。
3. 建立可證明的 root-cause matrix。
4. 以最小必要變更完成 v0.5.2。
5. 每完成一個功能段落立即進行 targeted tests、重複測試、負向控制與 regression tests。
6. 若失敗，立刻停止後續功能開發，修正到該階段全部必要 gate 通過，再繼續。
7. 不得把問題標成「已解決」後跳過失敗測試。
8. 不得用大量無關測試數量掩蓋核心 regression。
9. 不得透過刪除、弱化、skip、xfail、放寬 assertion、改 fixture 使其避開 production branch 等方式讓失敗看起來成功。
10. 完成全部核心功能後，再做 full suite、stress、real-browser local-harness soak、static/type/security/performance、Windows packaging verification。
11. 有問題就修，再全部重跑；一直循環到沒有 P0/P1 regression。
12. 最後才輸出 v0.5.2 source ZIP 與 Windows package。
13. 除非真的遇到無法自行解決的外部阻塞，否則不要在 Phase 1 audit 完成後停下來等使用者確認。
14. 若環境無法執行某項測試，必須明確標為 `UNAVAILABLE` / `NOT RUN` / `NOT VERIFIED`，不得寫 PASS。
15. 若最終長時間 browser gate 未達成，不得把該項寫成「已驗證 8 小時穩定」。

---

# 1. 輸入來源與信任順序

## 1.1 唯一產品基底

`BASE-V051 = https://github.com/sssss928/hunterX/releases/tag/v0.5.1`

HunterX v0.5.1 是唯一允許直接修改並升級成 v0.5.2 的產品基底。

第一階段必須先：
- checkout / extract v0.5.1；
- 記錄 commit / tag / ZIP SHA-256；
- 保留一份完全 READ-ONLY baseline；
- 所有 v0.5.2 工作在新 branch / clean working tree 進行。

不得直接覆寫原 v0.5.1 tag 或原始 artifact。

## 1.2 上游僅為參考，不是產品基底

參考：
- `https://github.com/bouob/tickets_hunter/releases/tag/v2026.08.07`
- 若 repo 可取得，也可閱讀 `v2026.08.17` 作為 TicketPlus regression reference。

上游只能用來：
- 比對處理方式；
- 尋找已知 bug fix；
- 理解平台 DOM / flow；
- 當作 regression evidence。

禁止 wholesale replace HunterX platform modules。
禁止把上游較簡單/較弱的架構直接覆蓋 HunterX 的既有：
- RefreshCoordinator
- LeakWatchScheduler
- ReloadGuard
- PlatformEngine
- per-tab platform state
- TicketPlus bounded submission watcher
- dictionary
- multi-instance
- notification
- hot reload
- onsale / leak-watch semantics

## 1.3 Gemini 建議只當 advisory evidence

Gemini 建議中可參考：
- 登入前保存目標活動 context；
- 登入後自動恢復目標活動；
- SPA/navigation lifecycle awareness；
- 成功或失敗後回到 area/ticket 要重新 arm automation；
- tab-specific state；
- click 前做 element stale/visible/enabled 驗證；
- additive lifecycle defense 而不是重寫平台核心。

Gemini 建議中「不得直接照做」：
- 不要對整個 `document.body` 掛高頻 MutationObserver 當主解法；
- 不要新增 random jitter 以模仿真人/WAF 規避；
- 不要偷偷拉長使用者 refresh interval；
- 不要所有 click 固定 +10ms；
- 不要背景打 API 宣稱「保證 session 永不過期」；
- 不要另造第二套與 PlatformEngine 競爭的 global StateManager；
- 不要使用 CAPTCHA / Queue-it / challenge / risk-control bypass。

---

# 2. 不可退讓的產品保護條款

v0.5.2 必須保留或改善 v0.5.1 的所有既有核心能力。

至少包含：

- TixCraft
- IndieVox
- Ticketmaster family
- KKTIX
- TicketPlus
- iBon
- KHAM / ticket.com.tw / UDN family
- FamiTicket
- FunOne
- FANSI GO
- Cityline
- HKTicketing / related family
- 目前 registry 中所有已支援 family

必須保留：
- 正式搶票模式；
- 撿漏模式；
- 使用者設定的 deterministic refresh cadence；
- `interval=0` = 禁用刷新語意；
- refresh_datetime；
- RefreshCoordinator ownership；
- LeakWatchScheduler；
- ReloadGuard；
- protected checkout/payment/queue safety；
- TicketPlus submission pending / blocked / queue bounded deadlines；
- custom user dictionary；
- ordinary field fill；
- OCR 現有行為；
- ticket quantity；
- allow_less_tickets；
- date / area selection；
- profile；
- multi-instance；
- notification；
- config hot reload；
- settings migration；
- existing Windows build/release architecture。

修改原則：

> 可以修改既有核心函式，但僅限於證據顯示「不修改就無法正確修復」的地方。
> 優先延伸既有 authoritative component，不建立重複 owner。

尤其禁止：
- 第二套 refresh scheduler；
- 第二套 platform state machine；
- 第二套 submission owner；
- 第二套 browser lifecycle owner；
- 多個模組同時 reload/navigation；
- success 後自動重複購票；
- queue 頁面自動 reload；
- payment/checkout 自動 reload；
- 不明 submit outcome 後盲目重新 submit。

---

# 3. 使用者已實際重現的 P0 問題

這些不是「可能」，而是需求必須解決的 user-reproduced defects。

## P0-U1：所有已知售票平台成功一次後，再回購票區域，automation 不會重新開始

使用者確認不是只有 TicketPlus。

在目前機器人已知/支援的售票網站：
- 一旦成功刷到票 / 進入訂單或成功狀態；
- 之後網站回到 area / ticket / price / date / activity，或使用者按網站合法的「繼續選購」、返回購票頁；
- 正式搶票模式與撿漏模式都可能不再：
  - 自動刷新；
  - 自動選日期；
  - 自動選區；
  - 自動選票價；
  - 自動選張數；
  - 自動進行後續正常購票流程。

必須把「完成」由 process/session sticky state 改成 **attempt-scoped completion**。

核心 invariant：

- 同一 attempt 成功後：絕對不得 duplicate submit。
- 使用者仍停在 confirm/order/checkout/payment：保持 protected，不重新購買。
- 使用者/網站真正回到 safe purchase route（activity/date/area 等），且 route/context 證明是新的購票 attempt：建立新的 attempt identity，重新 arm automation。
- 不得因為上一張票成功，就把整個 browser instance 永久關閉購票能力。
- 此規則要套用「所有 registry platform」，不能只修 TicketPlus。

## P0-U2：長時間使用後整個 automation 卡住

症狀：
- browser 視窗可能仍存在；
- HunterX process 可能仍存在；
- 所有購票自動化不再進展；
- refresh、area、ticket、click 全部像死掉；
- 有時最後 Chrome/browser 也會掛。

必須區分並處理：
- Python loop alive 但 browser/CDP dead；
- renderer stalled；
- CDP WebSocket closed；
- active target stale；
- URL read repeatedly fails；
- DOM evaluate repeatedly times out；
- stale single-flight action ownership；
- HunterX-owned asyncio task leak；
- browser process memory pressure；
- tab detached / target replaced；
- state deadlock；
- route drift / DOM drift；
- swallowed terminal browser exception。

## P0-U3：TicketPlus 登入成功後需要人工點活動頁

要求：
- 設定 homepage 是活動 URL 時；
- 為登入而暫時去首頁/login；
- 登入成功後必須自動恢復原始 target；
- navigation recovery 必須有 bounded retry；
- 失敗不可 silent pass；
- 多 tab target context 不得互相污染。

此能力也要稽核其他平台登入 flow：
KKTIX back_to、FamiTicket、KHAM、Ticket.com.tw、UDN、Cityline、HKTicketing 等，避免「登入後失去原 event target」。

## P0-U4：回到 area / price 後不刷新、不自動選擇、不繼續

要求不依賴 hard reload。
SPA route、browser back、網站 continue-shopping、server redirect 都要能重新 dispatch。

但不要盲目新增 full-body MutationObserver。
HunterX 已有 Python outer loop + PlatformEngine，優先讓現有 authoritative routing/state layer 正確處理 route transition。

---

# 4. v0.5.1 source 已確認、必須處理的具體風險

## P0-S1：TicketPlus process-level completion latch

檢查 `src/nodriver_tixcraft.py`：
- `ticketplus_purchase_done = False`
- dispatch 條件含 `and not ticketplus_purchase_done`
- success 後設為 True

這會造成 TicketPlus 成功後後續 dispatch 永久被外層 gate 擋住。

不得只做：
`ticketplus_purchase_done = False` everywhere。

正確方向：
- completion 綁 attempt_id / generation；
- PlatformEngine 判定從 protected page 回 safe page時，新 attempt 解除前一 attempt completion；
- protected route 不解除；
- ambiguous route 不解除；
- confirmation DOM rerender 不解除；
- back to safe route + valid target context 才解除。

同時找出其他平台所有等價 sticky completion gate：
搜尋但不限於：
`purchase_completed`
`purchase_done`
`is_finish`
`is_completed`
`is_success`
`ticket_assigned`
`order_done`
`stop_polling`
`automation_stop`
`done`
`closed`
以及 module/global/local state 中「成功後永不清」的欄位。

建立：
`PLATFORM_COMPLETION_LATCH_AUDIT.md`
列出每平台：
- flag 名稱；
- scope；
- set 條件；
- reset 條件；
- 是否 attempt-scoped；
- 是否會阻止新 attempt；
- 修正方式。

## P0-S2：empty URL 30 秒直接 safe-stop

`src/nodriver_tixcraft.py` 已有：
- `EMPTY_URL_SAFE_STOP_SECONDS = 30.0`
- persistent empty URL / CDP disconnect 後提示人工 restart 並 `break`

這是 v0.5.1「長時間無人值守」的明確缺口。

v0.5.2 不得簡單把 30 秒改成 60 秒或無限等待。

必須改成 state-aware recovery：

1. transient failure；
2. health probe；
3. reacquire existing target；
4. rebind existing browser/CDP；
5. 若 browser process alive，優先 reattach；
6. 僅在 safe pre-submit state 才允許 full browser restart；
7. restore safe target；
8. restore mode/config；
9. resume；
10. bounded retry + circuit breaker。

如果 state 是：
- SUBMIT_IN_FLIGHT
- ORDER_PENDING
- CHECKOUT
- PAYMENT
- QUEUE
- SUBMIT_OUTCOME_UNKNOWN

不得直接 restart + auto submit。

要 fail closed / read-only reconcile / notify。

## P0-S3：Browser/CDP closed 目前直接終止 instance

目前主 loop 遇到 terminal connection closed 會：
- log；
- `is_quit_bot = True`；
- clean stop。

v0.5.2 要新增分層 recovery，而不是把所有 disconnect 都當正常 quit。

但必須區分：
- 使用者真的手動關閉 tab/browser；
- renderer crash；
- CDP transport loss；
- browser process crash；
- target replaced during normal navigation。

使用者主動關閉不可被機器人無限重開。
異常 crash 才進 bounded recovery。

## P0-S4：BrowserSessionManager 只有 attach/stop，沒有 restart/rebind/restore

擴充現有 `BrowserSessionManager`。
不要新增另一個 lifecycle owner。

建議能力：
- `probe_process()`
- `probe_driver_transport()`
- `reacquire_target()`
- `reattach_if_possible()`
- `restart_safe_session()`
- `restore_safe_target()`
- `recovery_generation`
- bounded recovery attempts
- cooldown
- cleanup zombie process（僅自己擁有的 process）

不要殺掉使用者其他 Chrome。

## P0-S5：terminal browser errors 可能被平台 helper `except Exception: pass` 吞掉

全面 audit：
- `except Exception`
- bare `except`
- `pass`
- `return False/None` after browser/CDP exceptions

建立 exception taxonomy：

A. Normal DOM miss：
- selector not found
- stale expected DOM after route change
=> 可回 False/None。

B. Retryable browser operation：
- isolated timeout
=> 記錄 health failure，有限 retry。

C. Terminal browser transport：
- ConnectionClosed
- WebSocket unavailable
- target closed
- browser gone
- executor shutdown
=> 不得吞，必須向 lifecycle supervisor escalation。

D. Unknown programming error：
=> 不得假裝正常；log + re-raise or fail current phase。

所有平台 helper 都要遵守一致規則。

## P0-S6：heartbeat 只能證明 Python loop 在跑

現有 heartbeat 不得刪，但必須新增「真正的 progress health」。

至少追蹤：
- last_loop_tick
- last_url_success
- last_cdp_success
- last_dom_success
- last_platform_dispatch
- last_state_transition
- last_refresh_attempt
- last_refresh_success
- last_navigation_success
- last_click_success
- consecutive_url_failures
- consecutive_cdp_timeouts
- consecutive_dom_timeouts
- active_browser_action_age
- browser_pid_alive
- current page class
- current attempt id
- current generation

Health state：
`HEALTHY`
`DEGRADED`
`STALLED`
`DISCONNECTED`
`RECOVERING`
`FAILED`

避免每 50ms 做額外 CDP probe。
hot path 只更新 already-available timestamps。
只有超過 stale threshold 才做 active health probe。

## P0-S7：TicketPlus signin_form_filled 可能在 session expiry 後阻止重新登入

稽核 TicketPlus：
`signin_form_filled=True` 的 reset lifecycle。

Session 過期後：
- 必須允許新的 login attempt；
- 保存 tab-scoped target context；
- re-login；
- 恢復 target；
- 不使用「背景 API heartbeat 保證永不登出」當解法。

同樣稽核所有平台的：
- login_attempted
- form_filled
- signin_done
- authenticated
等 sticky flags。

## P1-S8：shared page_classifier 與 platform-specific route semantics 可能不一致

特別 audit：
- shared `classify_page("/order")`；
- TicketPlus adapter 將 `/order/<event>/<session>` 視為 AREA/select route；
- TixCraft `/ticket/order` 則是 protected order。

要求：
所有 safety-critical：
- reload；
- recovery；
- protected-page gating；
- attempt transition；
- refresh ownership；
優先使用 authoritative adapter classification。

不得讓 generic classifier 在已知 platform host 上覆蓋 platform-specific semantics。

新增 cross-platform route matrix tests。

## P1-S9：PlatformStateProxy fallback state 的跨 tab 污染風險

`PlatformStateProxy` 在有 active binding 時使用 per-tab PlatformEngine state，
無 binding 時使用 per-platform fallback dict。

必須 audit：
- production callback/task 是否可能在沒有 active binding 時呼叫 platform helper；
- `asyncio.create_task` / callbacks / hot reload 是否保留正確 context；
- fallback 是否可能讓同平台多 tab 共用 state。

production runtime 中：
- 任何 tab-specific flow state 必須來自 tab-scoped owner；
- fallback 僅允許 isolated tests / explicitly non-tab context；
- 若 production 無 binding，應 fail-safe/log，而不是默默共用 global platform state。

## P1-S10：AttemptRegistry 如果以 `id(tab)` 長存，需防 object-id reuse / stale state

確認 `attempt_lifecycle.py` 是否為 production authoritative component。

若 production 使用 plain `dict[id(tab)]`：
- tab 關閉後需 deterministic cleanup；
- 避免新 tab object 重用同一 id 取得舊 attempt；
- 優先 WeakKeyDictionary 或明確 stable tab identity；
- 不得因修此項導致 tab object 被不必要強引用造成 memory leak。

如果該 registry 是 dead/unused code：
- 不要為了「看起來完整」硬接入；
- 記錄為 dead-code architecture finding，評估移除或整合。

## P1-S11：single-flight browser action stale ownership

`runtime_health` 有 per-tab browser action single-flight。

必須證明：
- timeout；
- cancellation；
- exception；
- tab close；
- task cancellation during finally；
都能 release ownership。

加入：
- active action age；
- owner token；
- stale-lease diagnostics；
- stress cancellation tests。

不得粗暴 timeout 後直接 unlock 並讓兩個真實 navigation 同時跑。
若 outcome unknown，先 reconcile ownership。

`BROWSER_ACTION_CAPACITY` 不得因 stale slot 累積而最終讓全系統所有 action 永久 blocked。

## P1-S12：多 instance / 多 tab

目前 named instance profile isolation 要保留。

同時測兩種：
A. 三個獨立 HunterX instance / 三個 browser profile。
B. 同一 browser 內多個 tab。

必須證明：
- target URL 不互相覆寫；
- event/date/area/ticket count 不污染；
- attempt id 不污染；
- refresh owner 不污染；
- login context 不污染；
- completion state 不污染；
- queue ownership不污染。

不要把 shared localStorage 當 authoritative state。

---

# 5. v0.5.2 建議目標架構

不建立第二套 StateManager。

沿用並強化：

`PlatformEngine`
→ platform-specific adapter
→ per-tab PlatformRuntimeState
→ PurchaseAttempt / attempt generation
→ RefreshCoordinator / LeakWatchScheduler / ReloadGuard
→ BrowserSessionManager
→ RuntimeHealthSupervisor（新增/擴充）
→ Runtime trace

## 5.1 Purchase Attempt invariant

每個購票 attempt 必須有：
- stable attempt_id
- platform
- tab identity
- event identity
- session/date identity（可取得時）
- area identity（可取得時）
- generation
- created monotonic time
- state
- submit ownership
- completion state

建議狀態：
- IDLE
- LOGIN_REQUIRED
- LOGIN_IN_PROGRESS
- TARGET_RESTORE_PENDING
- ACTIVITY_READY
- DATE_READY
- AREA_READY
- AREA_SELECTED
- TICKET_FORM_ACTIVE
- SUBMIT_IN_FLIGHT
- ORDER_PENDING
- QUEUE
- CHECKOUT_REACHED
- PAYMENT_REACHED
- COMPLETED
- FAILED_RETRYABLE
- RECOVERING_TO_SAFE_ROUTE
- SUBMIT_OUTCOME_UNKNOWN
- CLOSED

不要為每平台硬套不適合的 route。
可以有 platform mapping，但 shared invariants 必須一致。

## 5.2 新 attempt 的唯一合法觸發

只有當：
- previous attempt 已完成/失敗/closed；
- current route 已離開 protected transaction；
- adapter 明確認定 current route 是 safe purchase route；
- target context 合法；
才 start new attempt。

不能因單純 DOM rerender 就新建 attempt。
不能因 confirmation URL query 變了就新建 attempt。
不能因一次 transient URL read failure 就新建 attempt。

## 5.3 成功後行為

成功後：
- 停止該 attempt 的所有 submit/poll/reload ownership；
- 保護 confirm/order/checkout/payment；
- 不自動開始第二筆；
- 使用者按「繼續選購」或網站導回 safe route後，自動建立 next attempt；
- 正式模式與撿漏模式重新 arm 正確 scheduler；
- 不要求手動 F5；
- 不要求重新啟動 exe。

---

# 6. Navigation / Login Context Recovery

建立既有 architecture 內的 `NavigationIntent` / `TargetContext`，不是 localStorage global hack。

至少：
- target_url
- normalized_target_url
- platform
- event_id if known
- mode
- instance_id
- tab identity
- created_at
- expires_at
- config generation
- reason

登入前：
- 保存 tab-scoped target。

登入後：
- 驗證仍在同一 platform；
- 驗證 session 已登入；
- 驗證 target 非過期；
- 使用 `guarded_get`/authoritative navigation owner restore；
- navigation failure bounded retry；
- 不能 `except Exception: pass`。

URL 比較必須 canonicalize：
- scheme/host case；
- trailing slash；
- fragment；
- platform-defined irrelevant query；
但不得錯把不同 event/session 當相同。

KKTIX 已有 `back_to` 的情況，保留原行為；
不要把通用 restore 強行疊加造成 double navigation。

---

# 7. SPA / route lifecycle

HunterX Python outer loop 已以約 50ms cadence 運作，因此：
- 優先 route/page-class transition detection；
- 不把全 body MutationObserver 當主要核心。

需要偵測：
- URL change；
- target replacement；
- PageClass change；
- same-URL DOM generation change（只有平台確實需要時）；
- browser back/forward；
- site continue-shopping；
- server redirect。

若必須 JS listener：
- 優先 `pushState` / `replaceState` / `popstate`；
- listener 必須 idempotent；
- 單次安裝；
- 有 teardown；
- 不每次 DOM mutation 做 heavy work。

---

# 8. Browser/CDP Recovery Engine

## 8.1 分級恢復

Level 0：normal retry
- 單次 DOM timeout
- 單次 URL read miss

Level 1：reacquire
- active target stale
- target object replaced
- transient execution-context loss

Level 2：transport rebind
- browser process alive
- CDP/websocket disconnected
- 嘗試重連現有 browser，不刷新頁面

Level 3：safe browser restart
僅在：
- current attempt 尚未 submit；
- current page 是 safe route；
- 不在 queue/checkout/payment；
- target context 足夠恢復；
才允許。

Level 4：fail closed
若：
- submit 已送出但 outcome unknown；
- queue ownership可能存在；
- payment/checkout；
- recovery 無法證明安全；
則：
- 不重複 submit；
- 不 reload；
- 保存 trace；
- 顯示/通知使用者；
- 等人工處理或 read-only reconcile。

## 8.2 禁止的 naive recovery

禁止：
`except ConnectionClosed -> restart -> homepage -> submit again`

因為 server 可能已接受第一筆 submit。

建立 `SUBMIT_OUTCOME_UNKNOWN` invariant：
任何 crash/disconnect 發生在 submit dispatch 後、確認結果前，
不得自動建立下一 submit。

---

# 9. Runtime stall / circuit breaker

不要只看 heartbeat。

設計 bounded circuit breaker。

例：
- 1 次 evaluate timeout：不處理；
- N 次連續 URL/CDP failure：DEGRADED；
- stale progress > threshold：active health probe；
- probe failure：RECOVERING；
- recovery attempt 有上限；
- cooldown；
- 同一 generation 不得 recovery storm。

threshold 要集中設定、有測試，不散落 magic number。

所有時間使用 monotonic clock。

---

# 10. Session expiry

不要新增為規避網站政策的 keep-alive API。

改成 passive detection + recovery：
- authenticated → expired；
- reset login attempt state；
- preserve safe target context；
- login recovery；
- restore target。

若登入需要人工 CAPTCHA/2FA：
- 不 bypass；
- pause/notify；
- 人工完成後自動接回 target。

---

# 11. Click / DOM race 改善

可採 Gemini「safe click」概念，但禁止固定 10ms 全域延遲。

click 前可驗證：
- element still attached；
- visible；
- enabled；
- page generation still current；
- target route unchanged；
- candidate still matches configured area/date/price。

如果 click 後沒有預期 state transition：
- bounded retry；
- reacquire element；
- 不重複 submit button；
- submit 類 click 必須 transaction-idempotent。

只有實際證據顯示 framework binding 未完成時，才使用 bounded readiness signal。
不要 blind sleep。

---

# 12. DOM drift / selector fallback

v0.5.2 建議加入，但不得變成亂點。

每個關鍵 selector：
- primary selector；
- known fallback selector；
- semantic/ARIA/text evidence；
- confidence；
- route restriction。

低 confidence：
- 不點；
- log `DOM_DRIFT`；
- 保留 debug snapshot（不得包含密碼/完整個資）。

目標：
網站小改版時「可診斷、可安全 fallback」，
不是為了任何 challenge bypass。

---

# 13. Refresh ownership 不得退步

保持：
- refresh_datetime central owner；
- platform periodic refresh；
- leak-watch scheduler；
- TicketPlus submit watcher；
- queue watcher；
互斥/優先級明確。

必須建立 `REFRESH_OWNERSHIP_MATRIX_v0.5.2.md`。

每一 page class 說明：
- 誰可以 refresh；
- 誰不能；
- interval；
- retry；
- protected behavior。

成功後回 area：
- old attempt refresh owner 關閉；
- new attempt scheduler re-arm；
- user interval 語意不變。

`interval=0` 必須仍然完全不刷新。

不得加入 random jitter 改變使用者 cadence。

---

# 14. 效能：速度不能變差

先在未修改 v0.5.1 建 baseline benchmark。
同一台機器、同一 Python、同一 test data。

至少測：
- main-loop no-op hot path；
- URL classification；
- PlatformEngine dispatch；
- per-tab state lookup；
- RefreshCoordinator idle；
- due refresh decision；
- TicketPlus submission watcher before next_probe；
- multi-tab state；
- runtime health no-op update；
- logging disabled / normal mode；
- 1 instance；
- 3 instances。

v0.5.2 原則：
- 50ms control cadence 不因方便而整體調慢；
- heavy DOM/CDP work deadline-gated；
- health supervisor正常時只做 in-memory/monotonic operations；
- telemetry 30~60 秒取樣，不放 hot path；
- 不因 safe click 全域增加 delay；
- 不因 anti-WAF jitter 增加延遲。

Performance acceptance：
- benchmark 至少 5 rounds；
- 若 v0.5.2 任一核心 hotpath median 比 v0.5.1 可重現地慢 >3%，必須調查；
- p95 可重現退步 >5% 必須調查；
- 若差異疑似 noise，重新 warm-up + repeat，不得直接忽略；
- correctness/liveness 不可為 benchmark 數字而犧牲；
- 目標是 equal-or-better within measurement noise，且降低無效 DOM/CDP calls。

---

# 15. Memory / Task / Resource Stability

新增低頻 diagnostics：
- HunterX process RSS；
- browser process RSS（可安全取得時）；
- CPU；
- tab count；
- HunterX-owned task count；
- active browser action count；
- CDP timeout count；
- reconnect count；
- recovery count；
- log queue/size。

不要依賴 `asyncio.all_tasks()` 數量直接 kill。
建立 HunterX-owned TaskRegistry：
- owner
- purpose
- created_at
- generation
- completion/cancellation
- cleanup

audit 所有 `asyncio.create_task`：
- 是否 bounded；
- 是否 strong reference 永留；
- 是否完成後移除；
- cancellation 是否 await；
- exception 是否被收集。

---

# 16. Runtime log 自身也要 benchmark

現有 log rotation 保留。

檢查：
- hot path 是否每 cycle sync open/write；
- 多 instance 是否造成大量 lock/I/O；
- log 是否有 unbounded growth；
- sensitive fields 是否已 redacted。

若實測有明顯負擔：
- 才做 bounded queue/batching；
- queue 必須有容量；
- crash 時至少保留最近 critical trace；
- 不為「優化」引入背景 thread leak。

---

# 17. 全平台必做的 lifecycle matrix

對 registry 內每個 platform family，逐一完成：

A. fresh start → safe page
B. login required → login → target restore
C. activity/date → area
D. no ticket → refresh/retry
E. area → ticket/form
F. retryable failure → safe route
G. success → protected route
H. success 後仍停 protected → no duplicate action
I. success → continue shopping/back → safe route
J. new attempt automatically armed
K. 正式模式
L. 撿漏模式
M. interval=0
N. SPA route transition
O. target changed to another event
P. tab close
Q. transient CDP timeout
R. terminal CDP disconnect

不是每個平台都有完全相同 URL，
要由 adapter semantics 建 fixture，不可硬套 TicketPlus path。

---

# 18. 測試策略：每個階段都要 Stop-the-Line

## Phase 0 — Baseline Freeze

執行 v0.5.1 原有 full suite。
記錄：
- total pass/fail/skip；
- benchmark；
- Windows build baseline；
- source hashes。

若 baseline 本身在目前環境失敗：
先判斷環境 vs source defect。
不得開始亂改 v0.5.2 來掩蓋 baseline runner 問題。

## Phase 1 — Universal Attempt Lifecycle

先寫直接 reproducer：
- success → protected → no duplicate；
- protected → safe route → new attempt；
- new attempt automation resumes；
- all platform fixture matrix。

先讓測試在 BASE-v0.5.1 對已知 bug 失敗，證明 test 有偵測力。

再實作。

完成後：
- focused tests；
- 連續 20 rounds；
- 1000 transition soak；
- cross-tab 3-way interleave；
全部過才進下一 Phase。

## Phase 2 — Login Target Recovery

測：
- login target save；
- restore；
- malformed target；
- expired target；
- cross-tab target isolation；
- double navigation suppression；
- login session expiry；
- human-required auth resume。

每 case 至少重跑 20 rounds。

## Phase 3 — Browser/CDP Recovery

建立 deterministic fault injection：
- one evaluate timeout；
- repeated evaluate timeout；
- URL empty；
- transport closed；
- target replaced；
- renderer gone；
- browser process gone；
- cancellation during navigation；
- stale browser-action ownership。

關鍵 negative test：
`submit dispatched -> connection lost -> recovery`
必須證明：
- zero automatic second submit。

## Phase 4 — Multi-tab / Multi-instance

至少：
- 3 tabs × 1000 state transitions；
- 3 instances × 1000；
- different event/date/area；
- mixed formal/leak mode；
- no cross-contamination。

## Phase 5 — DOM Drift / Router

Local synthetic SPA：
- pushState；
- replaceState；
- popstate；
- same URL rerender；
- selector primary missing；
- fallback present；
- unknown DOM。

確認：
- no full-body observer storm；
- no duplicate init/listener；
- no memory growth。

## Phase 6 — Performance

v0.5.1 vs v0.5.2 same-machine A/B。
至少 5 benchmark rounds。
輸出 raw results + median/p95 comparison。

## Phase 7 — Full Regression

跑完整 pytest。
然後至少再跑一次 fresh process full suite。

所有：
- failures
- errors
- unexpected skips
必須處理。

不得只報「大部分通過」。

## Phase 8 — Long-run Real Browser Local Harness

建立本地 synthetic ticket SPA，不做真實購票。
實際啟動 packaged/production browser stack。

必做至少：
- 1 instance long-run
- 3 instance long-run
- 正式模式 idle/transition
- 撿漏模式 idle/transition
- login redirect simulation
- success → continue shopping cycles
- periodic injected CDP/target failure

Release target：
- 8 hours 1-instance
- 8 hours 3-instance

若 Codex 執行環境有硬性時限不能完成 8h：
1. 至少完成可行的 30~60min actual-browser soak；
2. 加 deterministic accelerated 100,000+ lifecycle iterations；
3. 報告必須寫 `8H SOAK NOT VERIFIED`；
4. artifact 只能標 `RC`，不得宣稱 final long-run verified。

若環境允許完成 8h，才可標 FINAL。

監控：
- RSS start/end/max；
- task count；
- active browser actions；
- CDP errors；
- recovery count；
- stalled seconds；
- duplicate submits；
- state transition count。

不得有 unbounded monotonic growth。

## Phase 9 — Windows package

沿用 v0.5.1 已驗證 release architecture，除非 audit 證明必須改。

不要任意換 PyInstaller/runtime baseline。

Build 後驗證：
- PE headers；
- version=0.5.2；
- app_src parity；
- source parity；
- dual-runtime isolation；
- archive CRC；
- safe paths；
- no cache/log/settings/profile/credentials；
- packaged smoke launch；
- local synthetic browser scenario。

---

# 19. 負向控制 / Anti-fake testing

每個 P0 必須至少有一個 negative control。

要求：
- 新測試對 BASE-v0.5.1 必須能抓到原 bug；
- 若 test 在 BASE 也 pass，必須說明是已有能力，不能冒充新 regression；
- FINAL-only fix 可對 intermediate candidate 做 expected failure。

禁止：
- 只增加 500 個 util tests；
- 只測 helper return value卻沒進 production route；
- fixture URL 不符合 production branch；
- mock 掉真正出錯 layer；
- assertion 永遠為 true；
- monkeypatch 被測函式本身；
- 透過 sleep 讓 race「看起來不發生」。

建立：
`REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md`

欄位：
- Requirement ID
- production files
- direct test
- negative control
- stress test
- result
- evidence

測試數量本身不是品質指標。
只有能證明需求的 tests 才算。

---

# 20. 每階段 Failure Loop

任何 phase 發生 fail：

1. 保存 failure output。
2. 判斷：
   - production defect
   - test defect
   - fixture defect
   - runner/environment defect
3. 不得先改 assertion。
4. 若 production defect：修 production。
5. 若 test/fixture defect：必須證明 fixture 與 production contract 不一致才可修 test。
6. 重跑單一 failing test。
7. 重跑該 focused file。
8. 20 rounds repeated。
9. 重跑相鄰 regression group。
10. 才可繼續下一 phase。

如果同一錯誤修 3 次仍復發：
- 停止 patch-on-patch；
- 重新檢查 ownership/state architecture；
- 寫 root cause；
- 做最小一致性重構。

---

# 21. 不允許的「修法」

禁止：
- 全部平台重寫；
- 把 polling interval 拉慢掩蓋 CPU 問題；
- 把 timeout 拉超長掩蓋 deadlock；
- 無限 retry；
- 無限 restart browser；
- crash 後盲目 resubmit；
- queue reload；
- checkout/payment reload；
- CAPTCHA/Queue/risk-control bypass；
- proxy rotation；
- account pooling；
- automated payment；
- bulk purchase；
- hidden anti-bot evasion；
- random jitter 偽裝真人；
- silent `except Exception: pass` 吞 browser terminal failures；
- global localStorage 作為多 tab flow state；
- 第二套 refresh owner；
- 第二套 platform state manager。

---

# 22. 建議新增的 diagnostics

提供人類可讀 runtime trace：

每個 event：
- monotonic timestamp
- wall timestamp
- instance
- tab identity
- platform
- URL route（去 query secret）
- PageClass
- attempt_id
- generation
- previous state
- next state
- action
- result
- error class
- next deadline
- health state

保留最近 500~2000 critical events 的 bounded ring buffer。

提供一鍵 export：
`hunterX_debug_trace_<timestamp>.json`

必須 redaction：
- password
- token
- cookie
- SID
- auth code
- full identity fields

---

# 23. v0.5.2 設定與相容性

若新增：
- auto_recover_browser
- health diagnostics
- trace
- recovery limits

必須：
- config schema version；
- migration；
- v0.5.1 settings 無需人工重建；
- sensible defaults；
- UI 若不需要就不要增加一堆複雜選項。

核心 recovery 應有安全預設。

---

# 24. 靜態 / 型別 / 安全 gates

最終至少：
- `python -m compileall -q src tests scripts`
- AST parse all Python
- JSON/TOML/YAML parse
- Ruff
- configured Mypy
- JS syntax check
- `git diff --check`
- pip-audit
- Bandit high severity
- archive verifier

若工具 unavailable：
報 `UNAVAILABLE`，
不得寫 PASS。

---

# 25. 最終 Definition of Done

v0.5.2 只有在以下成立才可 Final：

1. 所有 user-reproduced P0 有直接 regression。
2. 所有 registry platform 完成 success→safe-route→new-attempt matrix。
3. 新 attempt 會重新自動化。
4. old attempt 不 duplicate submit。
5. formal mode / leak mode 都通過。
6. Login→target restore 通過。
7. session expiry recovery 通過。
8. transient browser errors 不致永久 stall。
9. terminal browser error 可 bounded recovery 或安全 fail-closed。
10. empty URL 不再固定 30s 一律人工 stop，而是 state-aware recovery。
11. submit outcome unknown 不自動 resubmit。
12. queue/payment/checkout 保護不退步。
13. multi-tab 無 state 污染。
14. multi-instance 無 state/profile/target 污染。
15. no task/action unbounded leak。
16. no measurable hot-path performance regression。
17. refresh cadence semantics 不變。
18. interval=0 不變。
19. dictionary/OCR/notifications/settings 不 regression。
20. TixCraft/Ticketmaster/KKTIX/TicketPlus 與其他 registry platforms existing tests pass。
21. full suite 0 unexpected failure。
22. negative controls 有實際偵測力。
23. source archive verified。
24. Windows archive verified。
25. release docs 與 hashes 完整。
26. 沒有把未執行測試寫成 PASS。
27. 沒有真實購票/付款/queue bypass claim。

---

# 26. 最終輸出檔案

若 FULL FINAL gates（包含要求的 actual-browser soak）完成：

1. `hunterX_source_0.5.2_final.zip`
2. `hunterX_windows_0.5.2_final.zip`
3. `FINAL_AUDIT_v0.5.2.md`
4. `TEST_REPORT_v0.5.2_FINAL.md`
5. `LONG_RUN_STABILITY_REPORT_v0.5.2.md`
6. `PERFORMANCE_COMPARISON_v0.5.1_vs_v0.5.2.md`
7. `PLATFORM_COMPLETION_LATCH_AUDIT.md`
8. `REFRESH_OWNERSHIP_MATRIX_v0.5.2.md`
9. `REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md`
10. `IMPLEMENTATION_DIFF_v0.5.2_FINAL.md`
11. `RELEASE_NOTES_v0.5.2_FINAL.md`
12. `SHA256SUMS_v0.5.2_FINAL.txt`

若 8h actual-browser soak 因執行環境硬性限制無法完成：
不得用 `_final`；
改輸出：
- `hunterX_source_0.5.2_rc.zip`
- `hunterX_windows_0.5.2_rc.zip`
並清楚標示：
`8H SOAK NOT VERIFIED`

---

# 27. 最終報告必須回答的問題

1. 使用者「成功一次後所有平台都不再自動化」的共同/各平台 root cause 是什麼？
2. 哪些是 common runtime bug，哪些是 platform-specific latch？
3. 每個平台 completion flag 如何改成 attempt scoped？
4. 為什麼新架構不會 duplicate submit？
5. 為什麼返回 area 後會重新 arm？
6. 正式模式與撿漏模式各自如何恢復 refresh ownership？
7. 長時間卡死的 root cause 找到哪些？
8. browser/CDP failure 如何分級？
9. 哪些情況會自動 recovery？
10. 哪些情況必須 fail closed？
11. 如何證明沒有 task leak / browser-action leak？
12. 三 tab / 三 instance 如何證明隔離？
13. v0.5.1 vs v0.5.2 performance 數據？
14. 哪些 Gemini 建議採納？
15. 哪些 Gemini 建議拒絕？為什麼？
16. 哪些 upstream idea 採納？
17. 哪些 core bytes 被修改？
18. 哪些 core 保持不動？
19. 所有 observed test failures 與修復循環。
20. 哪些項目 NOT TESTED / UNAVAILABLE。
21. source ZIP / Windows ZIP SHA-256。
22. Windows package 是否 native smoke verified。
23. actual-browser soak 的真實時間與結果。

---

# 28. 最重要的工程準則

不要追求「改很多」。

追求：

> 最少必要變更 + 唯一 owner + attempt-scoped state + bounded recovery + 可證明 regression safety。

不要把 v0.5.2 變成更複雜但更脆弱的系統。

若現有 v0.5.1 component 已能完成工作：
直接修它。

若功能其實已有，只是 lifecycle 沒接好：
接好 lifecycle。

若 Gemini / upstream 建議與 HunterX architecture 衝突：
保留 HunterX 較強 architecture，只取 behavior/fix idea。

最後的 v0.5.2 應該達成：

- 成功一次不會讓整個平台永久停機；
- 回到合法 safe purchase route 可開始下一 attempt；
- 不會 duplicate submit；
- 登入後回原 target；
- session 過期可安全接回；
- browser/CDP 短暫故障不會讓 bot 永久假死；
- 真正危險/不明 transaction 狀態會 fail closed；
- 長時間 1~3 instance 不會因 task/action/state 無界累積而愈跑愈慢；
- 正常 hot path 不增加無意義 DOM/CDP；
- 使用者原本的速度、refresh、正式/撿漏行為保持或改善；
- 不使用 anti-bot/queue/CAPTCHA/payment bypass。

現在開始執行。先完成 READ-ONLY audit 與 root-cause matrix，接著在沒有真正外部阻塞的情況下自動進入 implementation → phase tests → fix loop → full regression → soak → package → verification → final artifacts。
