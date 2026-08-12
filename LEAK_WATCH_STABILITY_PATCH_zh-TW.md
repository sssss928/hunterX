# HunterX v0.4.7 撿漏模式穩定性修正

日期：2026-08-07
原始碼基準：使用者最新上傳的 `hunterX_source_0.4.7.zip`

## 1. 問題定位

這次實機紀錄顯示撿漏模式存在兩層彼此獨立的 renderer/CDP 壓力來源。

第一層是 AREA 同一份 document 的重複 DOM 掃描：撿漏刷新間隔雖設定數秒，原本 scheduler 只限制真正的 reload；一次無票 scan 結束後，下一個 50 ms 主迴圈仍可立刻重新查詢 `.zone`、區域連結與頁面 JavaScript。這一層已由目前 source 內的 per-document DOM scan gate 修正。

第二層是本次最新 log 暴露的 URL polling hot path：主迴圈每約 50 ms 會呼叫 `nodriver_current_url()`，一般路徑透過 `tab.js_dumps("window.location.href")` 進入頁面 JavaScript execution context。即使 AREA document 已經掃完、只是在等待下一次 leak-watch reload，外層仍可能持續執行這個 URL probe。實機最後一次失敗發生在 `Leak-watch reload starting` 之後、fresh AREA 尚未完成第一次掃描之前，接著 `js_dumps` 5 秒 timeout；因此只節流「掃描後等待」仍不完整，成功 reload 後 renderer settling 到第一次 scan 前也必須避免不必要的 page-JS URL probe。

## 2. 修正邊界

本次只在以下安全狀態使用 CDP cached `TargetInfo.url`，不執行 `window.location.href` JavaScript probe：

- `run_mode == leak_watch`；
- TixCraft family 的安全 AREA 頁面；
- 撿漏刷新間隔大於 0；
- 已知成功 reload / recovery 後、fresh AREA document 第一次 DOM scan 尚未開始；或
- 當前 AREA document 已完成一次 DOM scan，正在等下一個既有 reload deadline；
- 且沒有 area click、pending navigation、navigation retry、submit、manual intervention、purchase attempt 等 transition state。

任一購票/導頁 transition 出現時，fast path 立即失效，恢復原本 JavaScript URL detection。

## 3. 明確未改動的購票核心

- 正式搶票 `onsale` 一般流程；
- 日期選擇；
- 區域關鍵字、排除、random/top/bottom/most-remaining 等選區邏輯；
- 票數；
- 驗證碼與 OCR；
- area click 與 navigation confirmation；
- ticket form；
- submit guard / order pending；
- checkout / payment 保護；
- Queue / Cloudflare /既有 protected-route handling；
- 通知條件與通知內容。

## 4. 修正後的 leak-watch AREA cycle

1. guarded reload / recovery 成功，scheduler 標記為「已知 fresh document」。
2. renderer settling 到第一次 scan 前，外層 URL polling 只讀 CDP `TargetInfo.url`，不進頁面 JavaScript。
3. fresh document 第一次 DOM scan 開始時，fresh flag 清除。
4. 若找到可用區域，立即走原本的 area click / navigation / purchase 流程；任何 transition 都會關閉 cached-URL fast path，恢復原本 JS URL 判斷。
5. 若沒有票，當前 document 標記為已掃描。
6. 等待下一個既有 reload deadline 期間，不再掃同一份 DOM，也不反覆用 `js_dumps(window.location.href)` 讀網址。
7. deadline 到時仍只走既有 guarded reload。
8. reload 成功後開啟下一份 fresh document cycle；reload 失敗時不虛構 fresh document，避免錯誤地套用 fast path。
9. `leak_refresh_interval_seconds = 0` 保留原本語意，不套用這組 interval-driven safe cycle fast path。

## 5. Runtime 修改檔案

### `src/leak_watch.py`

- 保留既有 `dom_scan_completed_since_reload` per-document gate。
- 新增 `fresh_document_after_reload`，只代表「已知成功 reload/recovery 後、第一次 fresh DOM scan 尚未開始」。
- `mark_recovery_landed()` / 成功 `finish_reload_cycle()` 才建立 fresh flag。
- `mark_dom_scan_start()` 立即清除 fresh flag。
- 新增 `can_use_cached_url_for_safe_area_cycle()`，並檢查 reload / DOM scan / area click / ticket form / submit 等 pending state。
- failed reload 不會建立 fresh flag。

### `src/platforms/tixcraft.py`

- 新增 `should_prefer_cached_url_during_leak_wait()`。
- 僅允許 safe AREA + leak-watch + scheduler 已知 safe document state。
- pending area navigation、retry、submit in flight、manual intervention、purchase attempt 任一存在即 fail closed，恢復原 URL probe。

### `src/nodriver_tixcraft.py`

- 新增 `_should_prefer_cached_runtime_url()`。
- 保留既有正式整點 refresh-datetime cached URL fast path。
- leak-watch 只額外接上上述 TixCraft safe-AREA helper。
- `nodriver_current_url()` 本身沒有改寫，既有 fallback / error / target-close 行為保留。

## 6. 文件與測試修改

- `CHANGELOG.md`：記錄 per-document DOM scan 與 safe cached-URL 修正。
- `RELEASE_NOTES_v0.4.7.md`：增加 Leak-watch renderer/CDP stability 說明。
- `src/www/help-content.js`：說明 AREA document 只掃一次，以及 safe cycle 使用 CDP cached URL。
- `tests/test_v047_leak_watch_url_polling.py`：新增 13 個 URL polling / transition-boundary regression tests。
- `TEST_REPORT_LEAK_WATCH_FIX_zh-TW.md`：記錄實際執行的測試、未完成項目與環境限制。

## 7. 設計原則

這不是「讓所有 URL 永遠只讀 cache」。`TargetInfo.url` 在導頁瞬間可能短暫落後，因此只有在沒有任何購票 transition 的 safe AREA idle state 才能跳過 JavaScript。這個限制是刻意的，目的是降低 renderer/CDP 壓力，而不是犧牲購票導頁偵測正確性。

測試也不能證明未來所有 Chrome、網站 DOM、CDN/Queue 或伺服器回應永遠不會出現新問題。最終報告會把「實際通過」和「此環境無法直接驗證」分開，不以跳過或缺依賴冒充成功。
