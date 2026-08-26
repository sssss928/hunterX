# HunterX v0.5.2 GitHub 正式發布指南

本專案的 v0.5.2 正式版採用 `source_native` 發行模式：Windows 執行檔與
source ZIP 必須由同一個乾淨 Git commit 建立。正式流程不下載、不要求、
也不繼承任何 RC2／RC3 GitHub Release 資產。

## 1. 唯一正式 workflow

`.github/workflows/release.yml` 是唯一 active release workflow。
舊的 RC3 workflow 與「從 RC3 疊出 FINAL」workflow 已移除；Git 歷史仍保留
其內容，因此不需要在 `.github/workflows/` 同時放置兩個容易誤觸的 release。

正式 workflow 只允許手動啟動，輸入如下：

- `version`：預設 `0.5.2`，必須是沒有前導 `v` 的嚴格 SemVer。
- `publish`：預設 `false`。`false` 只建置和驗證候選資產；`true` 才建立
  immutable GitHub Release `v0.5.2`。

## 2. 建置前準備

建立隔離分支，不要用網頁 Upload files 覆蓋 repository：

```powershell
git switch main
git pull --ff-only origin main
git switch -c release/v0.5.2-source-native
```

提交前確認沒有意外修改購票核心：

```powershell
git diff --exit-code origin/main...HEAD -- src
git status --short
```

本次發行工程的正常預期是 `src/**` 無差異。

## 3. 本機完整建置

環境必須使用：

- CPython 3.11.9 x64
- PyInstaller 6.21.0
- `requirements-lock-windows-py311.txt` 的 hash-locked runtime dependencies

一鍵執行：

```batch
build_scripts\build_and_test.bat
```

腳本依序執行 compileall、Ruff、mypy、完整 pytest、coverage threshold、
pip-audit、Bandit、雙 PyInstaller runtime 建置、native EXE smoke、Windows/source
archive 驗證、逐位元 parity 及 SHA-256 manifest 驗證。任何一步失敗都會以非零
狀態停止。

若只需要呼叫 Windows builder：

```powershell
$Commit = (git rev-parse HEAD).Trim()
./scripts/build_windows_final.ps1 -Version 0.5.2 -Commit $Commit
```

builder 只接受完整 40 字元 HEAD commit，且 working tree 必須乾淨。它會從
`git archive` snapshot 執行兩份 PyInstaller spec，保留：

```text
nodriver_tixcraft.exe + _nodriver_internal/
settings.exe          + _settings_internal/
```

兩套 runtime 不得合併成 `_internal/`。

## 4. 正式資產

完整流程只產生以下三個可發布資產：

```text
hunterX_windows_0.5.2_final.zip
hunterX_source_0.5.2_final.zip
SHA256SUMS_v0.5.2_FINAL.txt
```

`FINAL_BUILD_PROVENANCE.json` 必須顯示：

```json
{
  "schema": 2,
  "build_mode": "source_native",
  "version": "0.5.2",
  "qualifier": "final",
  "python_version": "3.11.9",
  "pyinstaller_version": "6.21.0",
  "windows_base_name": null,
  "windows_base_sha256": null
}
```

此外，`source_commit` 與 `runtime_source_commit` 必須相同，`runtime_src_tree`
必須等於該 commit 的 `src` tree，`requirements_lock_sha256` 必須等於 source ZIP
內 lock file 的 SHA-256。

## 5. Pull request 與 CI

推送發行分支後建立 PR 到 `main`：

```powershell
git push -u origin release/v0.5.2-source-native
```

PR 至少必須通過：

- CI / Test, lint, audit
- CI / Windows runtime and release-contract smoke
- 真實 source-native Windows package smoke
- CodeQL / Analyze Python
- Dependency Review

不得以 `continue-on-error`、刪除失敗測試、降低驗證範圍或加入無關測試來取得
綠燈。

## 6. Dry run

PR 合併且 main 全綠後：

1. GitHub → Actions → `Release v0.5.2`。
2. 選擇 `Run workflow`。
3. `version` 填 `0.5.2`。
4. `publish` 保持 `false`。
5. 下載 `verified-final-release-0.5.2` artifact。
6. 在乾淨 Windows Sandbox 完整解壓並執行兩個 EXE 的 `--version` 與設定 smoke。
7. 重新核對 checksum、provenance 和 source/Windows parity。

Dry run 不建立 tag，也不建立 GitHub Release。

## 7. 正式發布

完成 dry run 與人工驗收後，再執行相同 workflow，將 `publish` 設為 `true`。

publish job 會：

1. 重新下載已驗證的三項資產。
2. 再驗一次 checksum 和 pair parity。
3. 拒絕覆蓋任何既有 `v0.5.2` tag 或 Release。
4. 建立非 prerelease 的 `v0.5.2`。
5. 僅上傳三個 canonical assets。

建議在 repository Settings 建立 `production-release` Environment 並指定 required
reviewer，讓正式發布必須人工核准。

## 8. 發布後驗證

不要信任本機原檔。從 GitHub Release 重新下載三項資產到空白資料夾，再執行：

```powershell
Get-FileHash -Algorithm SHA256 .\hunterX_windows_0.5.2_final.zip
Get-FileHash -Algorithm SHA256 .\hunterX_source_0.5.2_final.zip
Get-Content .\SHA256SUMS_v0.5.2_FINAL.txt
```

Windows ZIP 必須完整解壓；不可只複製 EXE。確認設定介面、靜態資源、設定儲存、
瀏覽器 bootstrap 與安全的非提交流程正常後，才把 Release 視為完成。

## 9. 8 小時資格聲明

兩項 8 小時 actual-browser gates 目前是使用者明確豁免，沒有執行完成，也不得
宣稱 PASS。此狀態保存在 `FINAL_8H_SOAK_WAIVER.json` 與 schema 2 provenance；
其餘 regression、封裝 smoke、source/Windows parity 和 checksum 仍是硬性門檻。
