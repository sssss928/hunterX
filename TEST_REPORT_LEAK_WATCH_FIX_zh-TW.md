# HunterX v0.4.7 撿漏穩定性修正－測試與稽核報告

日期：2026-08-07

## 測試環境

- 原始碼基準：使用者提供的 `hunterX_source_0.4.7.zip`
- 執行環境：Linux container
- Python：3.13.5
- 專案宣告目標 Python：3.11.9
- Node.js：22.16.0

此環境無法取得專案指定的 `zendriver==0.15.3`、`pytest-benchmark`、`ruff`、`mypy`、`bandit` 套件，也無 Windows PowerShell / PyInstaller，因此以下結果明確區分「實際執行通過」與「環境無法直接驗證」。

## 1. 問題重現與修正回歸

新增 regression 驗證：

- 正常撿漏間隔 > 0 時，同一份已載入 AREA document 只做一次完整 DOM scan。
- 等待 reload deadline 期間，數百次 main-loop iteration 不再重複 `query_selector` / DOM scan。
- deadline 到時先走既有 guarded reload，不再先掃一次 stale DOM。
- reload 成功後，下一個 iteration 立即掃描 fresh document。
- reload 失敗後保留 consumed 狀態，避免舊 document 再次進 hot loop。
- `leak_refresh_interval_seconds = 0` 保留原有語意，不套用 per-document gate。

上述 3 個核心 regression 連續執行 5 次：每次 `3 passed`。

## 2. 主測試套件

因真實 Zendriver 套件在此環境不可安裝，測試時只使用「test-only import stub」提供 CDP 類別/namespace，目的是讓不需要真實瀏覽器的既有 unit/integration fixture 可以載入；stub 沒有加入最終 source。

排除：

- `tests/test_zendriver_hardening.py`：需要真實 `zendriver==0.15.3`。
- `tests/test_v043_tixcraft_long_run.py`：另行執行可完成的 soak case；其中一個 100,000 iteration async + tracemalloc case 在本環境超過 240 秒，未宣稱通過。
- `tests/benchmarks/`：本環境缺 `pytest-benchmark`，另以 smoke fixture 執行其實際 target functions。

主套件連續三輪結果：

1. `642 passed in 21.49s`
2. `642 passed in 20.09s`
3. `642 passed in 20.30s`

沒有 failure / error / xfail 被當成成功。

## 3. 長跑 / soak

實際執行：

- `test_scheduler_100000_iteration_soak_has_no_stuck_pending_or_growth`：PASS（100,000 iterations）。
- `test_real_area_zone_missing_reloads_across_fifty_intervals`：PASS（50+ refresh intervals）。
- `test_100000_iteration_failure_matrix_preserves_reload_liveness`：在本 container 執行超過 240 秒仍未完成，因此列為「未在此環境完成」，不是 PASS。

## 4. Benchmark target smoke

本環境沒有 `pytest-benchmark` fixture，因此使用只呼叫 target function 一次的 test-only smoke fixture；不偽裝成效能 benchmark。

結果：`6 passed`。

另外直接執行 `tests/benchmarks/audit_performance.py --samples 3 --iterations 100`，13 個 benchmark function 均完成，沒有 exception。

## 5. 語法與結構稽核

實際完成：

- `python -m compileall -q src tests scripts`：PASS。
- 93 個 Python 檔案 AST parse：0 parse error。
- bare `except:`：0。
- constant duplicate dict key：0。
- project-local import target 缺失：0。
- 所有 JSON：可解析。
- 所有 TOML：可解析。
- 所有 YAML：可解析。
- 兩個 PyInstaller `.spec`：Python AST parse PASS。
- 所有 `src/**/*.js`：`node --check` PASS。
- settings HTML static id：155 個、全部唯一（修正前有 19 個 legacy span 使用相同 id）。

靜態掃描看到 `src/platforms/common_async.py` 的 `bounded_poll` 名稱出現三次；檢查後為兩個 `@overload` 宣告 + 一個正式 implementation，屬合法且有意的 typing pattern，不是重複覆寫 bug。

靜態 config path 掃描列出的 legacy/missing path 經人工確認屬於：

- migration-only 舊欄位（如 `advanced.ocr_model_path`、`accounts.discount_code`）；
- runtime metadata（`_config_filepath`）；
- handler 建立的局部 config（`refresh_calibration`）；
- `normalize_time_calibration_config()` 的局部 nested config keys。

因此沒有把這些 false positive 當成 production default config 缺欄位。

settings.js 中 `refresh_platform_capability`、`theme_status` 對目前 HTML 沒有元素，但所有使用點都有 null guard；`#detected-question-alert .alert-heading` 是 descendant selector，不是缺少 id。它們目前不會導致 JS 執行中斷，因此未做無依據的 UI 功能增刪。

## 6. 維護風險（未大改）

繼承程式碼中存在大量 `except Exception: pass` / broad exception swallowing。這在瀏覽器自動化專案中部分用於容忍 transient DOM/CDP failure，但也會降低錯誤可觀測性。因使用者要求不改動原有購票核心邏輯，本修正沒有大規模改寫這些路徑；若一次性把它們改成 raise，反而可能改變既有購票容錯與流程時序。

## 7. 無法在此環境宣稱已驗證的項目

以下不是 PASS，也沒有被跳過後宣稱成功：

- 真實 `zendriver==0.15.3` 的 `tests/test_zendriver_hardening.py`。
- 真實 Windows Chrome renderer / CDP 長時間運行。
- Windows PyInstaller 重新封裝後 EXE 的啟動與 GUI/Chrome integration。
- 真實售票站 DOM、伺服器釋票、Queue/CDN/Cloudflare 行為。
- 100,000 iteration async failure-matrix + tracemalloc case（本環境 >240 秒未完成）。

因此本報告不使用「100% 完美、永遠不會再出問題」的說法。可以確認的是：本次定位到的 hot-loop 已有直接 regression 覆蓋，既有可執行的 unit/integration suite 連續三輪通過，且沒有發現修改造成的核心流程 regression。
