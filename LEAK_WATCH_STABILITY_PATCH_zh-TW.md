# HunterX v0.4.7 撿漏模式穩定性修正

日期：2026-08-07

## 問題定位

實際執行紀錄顯示，撿漏刷新間隔雖設定為 5 秒，但 `/ticket/area/` 在兩次真正 reload 之間仍會由主迴圈反覆執行相同 DOM/CDP 掃描。長時間累積後，Chrome 可能進入「沒有回應」，最後可看到 JS/CDP 診斷 timeout，而 HunterX 主程式本身仍存活。

原本 scheduler 只限制「何時可以 reload」，`dom_scan_pending` 也只防止同一時間有兩個未完成的 DOM scan；一個 scan 完成後，下一個 main-loop iteration 仍可立刻再次掃描同一份 document，因此無法限制已完成 scan 的頻率。

## 修正原則

本修正只套用於：

- `run_mode == leak_watch`
- TixCraft family 的安全 AREA 頁面
- 撿漏刷新間隔大於 0
- 當前已載入 document 已完成一次 DOM scan

不改動以下流程：

- 正式搶票 `onsale` hot path
- 日期選擇
- 區域關鍵字、排除、排序與選區演算法
- 票數
- 驗證碼/OCR
- area click 與 navigation confirmation
- submit guard / order pending
- checkout / payment 保護
- Queue-it / Cloudflare 等既有處理

## 行為變更

修正後撿漏 AREA cycle 為：

1. 新鮮 document 立即掃描一次。
2. 若找到可用區域，立即走既有選區與購票流程，不等待 cooldown。
3. 若沒有可用區域，將目前 document 標記為已掃描。
4. 等待下一個既有 leak-watch reload deadline；等待期間不再重複讀同一份 AREA DOM。
5. deadline 到時只走既有 guarded reload。
6. reload 成功後解除「已掃描」標記；下一個 iteration 立即掃描新的 document。
7. reload 失敗時保留「已掃描」標記，避免對舊 document 回到 hot loop；依原 scheduler 到下一個 deadline 再重試。
8. `leak_refresh_interval_seconds = 0` 保留原本語意，不套用此 per-document scan gate。

## 修改檔案

- `src/leak_watch.py`
  - 新增 `dom_scan_completed_since_reload` 狀態。
  - `mark_dom_scan_end()` 標記目前 document 已完成 scan。
  - 新增 `should_wait_for_reload_before_dom_scan()`。
  - 成功 reload / recovery 才重新開放新 document scan。
- `src/platforms/tixcraft.py`
  - 新增 `WAITING_NEXT_CYCLE` outcome。
  - 已掃描 document 在 main-loop hot path 中不再重做 DOM/CDP query，而是回到既有 reload finalizer。
- `src/www/help-content.js`
  - 補充撿漏等待期間不會重複掃描同一份 AREA DOM。
- `src/www/settings.html`
  - 移除 19 個重複的 legacy `id="inputGroup-sizing-default"`；這些 id 沒有程式引用，但重複 id 是無效 HTML。
- `tests/test_v043_leak_watch_scheduler_liveness.py`
  - 增加成功/失敗 reload 與 interval=0 的 scan-gate regression。
- `tests/test_v043_tixcraft_refresh_liveness.py`
  - 增加實際 area hot-loop regression，驗證數百次 main-loop iteration 不會在同一 document 重複掃 DOM。
- `tests/test_settings_profile_race_static.py`
  - 增加 settings HTML id 唯一性檢查。

## 驗證原則與限制

測試不能證明未來所有售票網站/Chrome 版本永遠不會出現新問題。尤其真實站點 DOM、伺服器回應、瀏覽器 renderer、Queue/CDN 行為無法完全由離線 fixture 模擬。因此測試報告會區分「實際通過」與「環境無法執行」，不把跳過或缺依賴當作通過。
