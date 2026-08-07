# HunterX v0.4.7 撿漏模式穩定性修正－測試與程式碼稽核報告

日期：2026-08-07
原始碼基準：使用者最新上傳的 `hunterX_source_0.4.7.zip`

## 1. 測試環境與誠信邊界

- Linux container
- Python 3.13.5
- 專案宣告目標 Python：3.11.9
- Node.js 22.16.0

此環境無法取得專案指定的真實 `zendriver==0.15.3`、`pytest-benchmark`、`ruff`、`mypy`、`bandit`，也沒有可用的 Windows Chrome + PowerShell/PyInstaller production build 環境。

為了讓與真實瀏覽器無關的 unit/integration fixture 能載入，測試期間使用一份 **test-only Zendriver import stub**。它只提供 import 所需 namespace / dummy CDP 類別，不加入最終 source ZIP，也不被當成真 Zendriver 驗證。

## 2. 最新實機 log 對應的問題

最新執行紀錄已顯示先前「同一 AREA document 每秒重複大量 DOM scan」的問題消失：一般 cycle 變成 reload → 一次 AREA scan → 倒數等待 → 下一次 reload。

但最後仍發生：成功進入 `Leak-watch reload starting` 後，fresh AREA 尚未進第一次 scan，外層 URL probe 即卡住，最後出現 `js_dumps timed out (5s); fallback_available=True`。因此本次新增第二層修正：safe AREA cycle 在成功 reload/recovery 後的 settling 階段，以及已掃描 document 等待下一輪期間，都使用 CDP cached `TargetInfo.url`，避免高頻進入頁面 JavaScript execution context。

## 3. 新增 URL polling regression

`tests/test_v047_leak_watch_url_polling.py` 共 13 個測試，驗證：

- AREA document 尚未進入已知 safe state 時，不使用 cached fast path。
- 已掃描 AREA document 等待下一輪時可使用 cached URL。
- 2,000 次 runtime URL polling：`js_dumps` 呼叫數必須為 0。
- 成功 reload 後、第一次 fresh DOM scan 之前，500 次 URL polling：`js_dumps` 呼叫數必須為 0。
- fresh DOM scan 一開始，post-reload fresh flag 必須立即清除。
- scan 結束後可重新進入 waiting cached fast path。
- failed reload 不得虛構 fresh document fast path。
- pending area navigation 必須恢復正常 JavaScript URL probe，並能看到 TICKET URL transition。
- area click pending、reload pending、submit pending、navigation retry、submit in flight、manual intervention、purchase attempt 任一存在，都禁止 cached fast path。
- `onsale`、interval=0、protected/non-AREA URL 不套用這個 leak-watch fast path。

結果：`13 passed`，另外獨立連續執行 5 輪，每輪都是 `13 passed`。

## 4. 相關核心回歸

包含 URL fallback、leak-watch scheduler、TixCraft refresh liveness、bounded operations、navigation confirmation、soft-block recovery、refresh timing arbiter、v0.4.7 submit/recovery hardening、cross-platform state/navigation 與新 URL polling regression。

結果：**236 passed**。

沒有把 failure / error / xfail 當成成功。

## 5. 主 unit / integration suite

排除以下需要獨立處理的項目：

- `tests/test_zendriver_hardening.py`：需要真實 `zendriver==0.15.3`；
- `tests/test_v043_tixcraft_long_run.py`：soak case 分開執行；
- `tests/benchmarks/`：本環境沒有 `pytest-benchmark` fixture。

其餘主套件獨立連跑三輪：

1. **655 passed in 20.59s**
2. **655 passed in 20.34s**
3. **655 passed in 20.62s**

另外 coverage run：**655 passed in 23.74s**。

離線 fixture 的總 coverage 約 28%；TixCraft 約 55%、`leak_watch.py` 約 79%、refresh timing 約 81%、reload guard 約 89%、run modes 約 93%。Coverage 只代表這套離線測試實際覆蓋到的行數，不代表真實網站/瀏覽器 100% 被驗證。

## 6. 長跑 / soak

實際執行：

- `test_scheduler_100000_iteration_soak_has_no_stuck_pending_or_growth`：**PASS**，100,000 iterations，約 17.48 秒。
- `test_real_area_zone_missing_reloads_across_fifty_intervals`：**PASS**，50+ refresh intervals。
- `test_100000_iteration_failure_matrix_preserves_reload_liveness`：在本 container 超過 120 秒仍未完成，因此明確列為 **未在此環境完成，不是 PASS**。

另以同一 failure-matrix 邏輯建立 test-only 20,000 iteration clone（不加入最終 source），實際完成並 PASS：

- reload attempts：309
- reload successes：264
- max active reload：1
- unsafe/protected reload：0
- pending flags 結束時均清除
- scheduler history 維持 bounded
- task set 無成長
- tracemalloc peak delta 約 426,516 bytes，低於該 smoke 所設 5 MB 邊界

這個 20k smoke 不能取代原本 100k case，因此原 100k failure-matrix 仍保持「未完成」狀態。

## 7. Benchmark / performance target

本環境沒有 `pytest-benchmark`，因此沒有偽造 benchmark 數字。

使用 test-only smoke fixture 只呼叫 6 個 benchmark target function 一次，結果：**6 passed in 0.08s**；這只是功能 smoke，不是效能 benchmark。

另外實際執行：

`python tests/benchmarks/audit_performance.py --samples 3 --iterations 100`

回傳碼 0，13 個 performance audit function 均完成，沒有 exception；環境僅提示 `ddddocr` 不可用。

## 8. 語法、設定與結構稽核

實際完成：

- `python -m compileall -q src tests scripts`：PASS。
- 全部專案 Python 檔案 AST parse：0 parse error。
- bare `except:`：0。
- constant duplicate dict key：0。
- project-local import target 缺失：0。
- async function 內 `time.sleep()`：0。
- JSON：全部可解析。
- TOML：全部可解析。
- YAML：全部可解析。
- `build_scripts/nodriver_tixcraft.spec`、`settings.spec`：Python AST parse PASS。
- `src/www/*.js`：全部 `node --check` PASS。
- `src/www/settings.html`：155 個 static ID，全部唯一。
- `settings.js` 的 `getElementById` static references：20 個，對 `settings.html` 無缺失。
- `python scripts/release_utils.py validate-project-version --version 0.4.7 --metadata src/hunter_metadata.py`：PASS，輸出 `0.4.7`。
- `git diff --check`：PASS。

靜態掃描看到 `src/platforms/common_async.py` 的 `bounded_poll` 名稱出現三次；人工檢查為兩個 `@overload` 宣告 + 一個正式 implementation，屬合法 typing pattern，不是重複覆寫 bug。

專案仍有一些 broad `except Exception` 容錯路徑。瀏覽器自動化中部分用於 transient DOM/CDP failure；使用者要求不改動原有購票核心，因此沒有為了靜態「變乾淨」而全面改成 raise，避免改變既有流程時序與容錯語意。

## 9. 真實 Zendriver 驗證狀態

`zendriver==0.15.3` 無法在此環境取得，因此 `tests/test_zendriver_hardening.py` **不能宣稱用真實 Zendriver 通過**。

用 test-only import stub 試跑時，8 個 case 可通過、3 個因 stub 沒有實作真實 Transaction result mapping / ProtocolException parsing / Listener constructor 語意而失敗。這 3 個是 stub 能力限制，不能拿來證明產品 bug，也不能拿來宣稱真 Zendriver PASS。

最終 source ZIP 會完全移除該 stub。

## 10. 無法在此環境宣稱已驗證的項目

以下明確不是 PASS：

- 真實 `zendriver==0.15.3` integration。
- Windows PyInstaller 重新封裝後 EXE。
- 真實 Windows Chrome renderer/CDP 長時間執行。
- 真實 TixCraft DOM、實際釋票、伺服器/CDN/Queue/Cloudflare 行為。
- 100,000 iteration async failure-matrix + tracemalloc 原始 heavy case（本環境 >120 秒未完成）。

因此不能合理宣稱「100% 完美、永遠不會再有任何問題」。可以確認的是：這次兩層已定位的 hot path 都有直接 regression 覆蓋；所有在目前環境可可靠執行的主 unit/integration suite 連續三輪通過；沒有發現本次修改造成選區、navigation、submit/recovery 或 cross-platform state 的 regression。
