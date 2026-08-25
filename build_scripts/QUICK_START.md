# 🚀 HunterX 打包快速開始

---

## 📦 一鍵打包與測試

### 使用方法

```batch
cd build_scripts
build_and_test.bat

REM 或者從專案根目錄執行
build_scripts\build_and_test.bat
```

**注意**：腳本要求 PATH 中的 Python 精確為 3.11.9，會安裝有版本上限的正式與開發依賴，並在任何檢查失敗時以非零狀態停止。

### 功能特點

✅ **自動化依賴管理**
- 自動檢測並安裝 requirement.txt 所有依賴
- 依 `requirements-dev.txt` 的版本範圍安裝 PyInstaller 與品質工具

✅ **完整打包流程**
- 打包 2 個 exe（nodriver_tixcraft, settings）
- 保留 `_nodriver_internal/`、`_settings_internal/` 兩個隔離依賴目錄，避免執行階段檔案互相覆寫
- 複製共用資源（assets/, www/）

✅ **自動化測試**
- 執行 compileall、Ruff、mypy、完整 pytest、pip-audit 與 Bandit
- 呼叫唯一正式建置入口 `scripts/build_windows.ps1`
- 對 ZIP 執行 CRC、路徑安全、denylist、必要檔案與雙 runtime 隔離驗證
- 測試摘要保存至 `dist/release/test_report_{VERSION}.txt`

✅ **發布 ZIP 生成**
- 自動打包成 `dist/release/hunterX_windows_{VERSION}_final.zip`
- 版本號讀自 `src/hunter_metadata.py`，且與建置參數不一致時拒絕建置

### 輸出檔案

- `dist/release/hunterX_windows_0.5.2_final.zip` - FINAL 發布 ZIP
- `dist/release/test_report_0.5.2.txt` - 本機品質檢查摘要

### 執行時間

約 10-20 分鐘（視硬體效能）

---

## 🧪 測試打包結果

### 方法 A：Windows Sandbox（推薦）

```batch
1. 啟動 Windows Sandbox
2. 複製 ZIP 到 Sandbox 桌面
3. 解壓縮並測試 2 個 exe
```

### 方法 B：開發機快速測試

```batch
cd dist\hunterX
settings.exe              # 測試網頁介面
```

---

## 📤 發布到 GitHub Release

### Step 1: 更新版本號

更新 `src/hunter_metadata.py` 的單一 `APP_VERSION`，並同步 README、CHANGELOG 與發布文件；建置腳本會拒絕版本不一致的成品。

### Step 2: 更新 CHANGELOG.md

記錄本次版本的更新內容。

### Step 3: 提交並推送 Tag

```batch
/gsave          # 提交變更
/gpush          # 推送到私人庫
/publicpr       # 建立 PR 到公開庫
/publicrelease  # 建立 Release Tag
```

### Step 4: GitHub Actions 自動執行

前往 GitHub → Actions，查看自動化打包進度（約 15-25 分鐘）。

### Step 5: 驗證 Release

前往 GitHub → Releases，下載並測試 ZIP 檔案。

---

## 📁 檔案結構參考

### 打包前（專案結構）
```
hunter/
├── src/                        原始碼
├── build_scripts/              打包腳本
│   ├── build_and_test.bat      ← 一鍵打包測試
│   ├── *.spec                  ← PyInstaller 配置（2 個）
│   ├── README_Release.txt      ← 使用者說明
│   └── QUICK_START.md          ← 本文件
├── requirement.txt             依賴清單
└── CHANGELOG.md                版本記錄
```

### 打包後（輸出結構）
```
dist/
├── hunterX/            整合目錄
│   ├── nodriver_tixcraft.exe
│   ├── settings.exe
│   ├── _nodriver_internal/     nodriver_tixcraft.exe 專用依賴
│   ├── _settings_internal/     settings.exe 專用依賴
│   ├── assets/
│   ├── www/
│   └── CHANGELOG.md
└── release/
    ├── hunterX_windows_0.5.2_final.zip  ← FINAL 發布 ZIP
    └── test_report_0.5.2.txt          ← 本機品質檢查摘要
```

---

## 🆘 常見問題

### Q1: 如何在虛擬機中測試？
**A**: 將發布 ZIP 複製到乾淨的 Windows Sandbox 或虛擬機，完整解壓後依序測試 `settings.exe` 與 `nodriver_tixcraft.exe --help`。

### Q2: 打包失敗怎麼辦？
**A**: 查看 `docs/07-deployment/pyinstaller_packaging_guide.md`，並從腳本第一個非零退出的檢查開始處理。

### Q3: 如何確保 exe 不依賴本地 Python？
**A**: 使用 Windows Sandbox 或虛擬機測試（沒有安裝 Python）。

### Q4: GitHub Actions 打包失敗？
**A**: 檢查 GitHub → Actions → Release → 查看錯誤 log。

### Q5: 使用者回報 exe 無法執行？
**A**:
1. 確認 `_nodriver_internal/` 與 `_settings_internal/` 都和 exe 在同一目錄，且未被合併
2. 檢查 Windows Defender 是否阻擋
3. 提供 `README_Release.txt` 給使用者

---

## 📚 詳細文件

- **開發者打包指南**：`docs/07-deployment/pyinstaller_packaging_guide.md`
- **乾淨環境測試**：本文件「測試打包結果」章節
- **使用者使用說明**：`README_Release.txt`（會包入 ZIP）

---

## ⚡ 快速參考表

| 目標 | 使用方法 | 時間 | 輸出 |
|------|---------|------|------|
| 本地打包與測試 | `build_and_test.bat` | 10-20 分鐘 | ZIP + 測試輸出 |
| 更新版本號 | 編輯 `src/hunter_metadata.py` 並同步發布文件 | < 1 分鐘 | 單一程式版本來源 |
| GitHub 自動發布 | 推送 tag | 15-25 分鐘 | GitHub Release |

---

**最後更新**：2026-08-10
