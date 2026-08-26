# HunterX v0.5.2 GitHub 正式發布指南

本指南適用於已在本機完成完整驗證的三個正式資產：

- `hunterX_windows_0.5.2_final.zip`
- `hunterX_source_0.5.2_final.zip`
- `SHA256SUMS_v0.5.2_FINAL.txt`

## 1. 更新 repository source

解壓縮 source ZIP 後，進入唯一的 `hunterX-0.5.2` 目錄。建議使用 Git
建立分支並提交整個目錄內容，不要只挑選 `src`：`.github`、`scripts`、
`tests`、`.gitattributes`、`.gitignore` 與發布文件都是正式 source 的一部分。

```powershell
git switch -c v0.5.2-final
git add --all
git commit -m "release: HunterX v0.5.2 final"
git push -u origin v0.5.2-final
```

建立 pull request 到 `main`，等待下列檢查完成：

- CI / Test, lint, audit
- CI / Windows runtime and release-contract smoke
- CodeQL / Analyze Python
- Dependency Review（只在 pull request 執行）

正常 CI 不依賴尚未發布的 RC3 GitHub Release asset，因此不會因找不到
`build-base-v0.5.2-rc3` 而出現紅叉。

## 2. 建立正式 GitHub Release

合併 pull request 後，在 GitHub Releases 選擇 **Draft a new release**：

1. 建立 tag `v0.5.2`，target 選擇剛合併並通過檢查的 `main` commit。
2. Release title 填寫 `HunterX v0.5.2`。
3. 貼上 `RELEASE_NOTES_v0.5.2_FINAL.md` 的內容。
4. 僅上傳上述三個正式資產；不要上傳解壓後的散檔或整個 Windows 資料夾。
5. 不勾選 prerelease，確認檔名與 SHA-256 後再按 **Publish release**。

GitHub 會另外自動產生 repository 的 Source code (zip/tar.gz)。它們是
GitHub 依 tag 生成的快照，不取代 HunterX 自己驗證過的
`hunterX_source_0.5.2_final.zip`。

## 3. 發布後核對

下載三個資產到新的空白資料夾，執行：

```powershell
Get-FileHash -Algorithm SHA256 .\hunterX_windows_0.5.2_final.zip
Get-FileHash -Algorithm SHA256 .\hunterX_source_0.5.2_final.zip
Get-Content .\SHA256SUMS_v0.5.2_FINAL.txt
```

兩個雜湊必須與 manifest 完全一致。Windows ZIP 必須完整解壓後執行，
不可只複製 EXE；兩個 `_internal` 目錄是 PyInstaller runtime 的必要內容。

`.github/workflows/release-final.yml` 是需要已發布、精確雜湊 RC3 base 的
選用自動重建流程，現在只允許手動啟動。使用上述已驗證資產進行一般手動
GitHub Release 時不需要執行它，也不會因建立 `v0.5.2` tag 自動觸發。

## 8H 資格聲明

兩項 8 小時 actual-browser gates 為使用者明確豁免、未完成且未宣稱 PASS；
此狀態保存在 `FINAL_8H_SOAK_WAIVER.json` 與 Windows provenance 中。
