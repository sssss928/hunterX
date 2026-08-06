# HunterX v0.4.7 Zendriver Listener 修正套用說明

## 修正內容

此修正只處理 Zendriver CDP Listener 收到「已取消或已完成交易的遲到回應」時，
`asyncio.InvalidStateError` 讓 Listener task 結束的問題。

沒有修改以下內容：

- `src/nodriver_tixcraft.py` 主迴圈與購票流程。
- `src/platforms/*` 各售票平台日期、區域、票數、驗證碼或送出邏輯。
- TixCraft、TicketPlus、KKTIX、KHAM 的平台處理器。
- Discord／Telegram 通知條件、內容或傳送流程。
- `requirement.txt`；仍使用已驗證的 `zendriver==0.15.3`。

## 直接覆蓋方式

先關閉 HunterX 與它啟動的瀏覽器，再把本修正包內檔案依照相同相對路徑覆蓋到
HunterX v0.4.7 原始碼根目錄：

1. 新增 `src/zendriver_hardening.py`。
2. 覆蓋 `src/browser_session.py`。
3. 覆蓋 `build_scripts/nodriver_tixcraft.spec`。
4. 新增 `tests/test_zendriver_hardening.py`。
5. 覆蓋 `CHANGELOG.md`。
6. 覆蓋 `RELEASE_NOTES_v0.4.7.md`。

也可以在原始碼根目錄直接套用隨附 patch：

```powershell
git apply --check hunterX_v0.4.7_zendriver_listener_fix.patch
git apply hunterX_v0.4.7_zendriver_listener_fix.patch
```

不要直接修改 `.venv/Lib/site-packages/zendriver`；重建虛擬環境或 Windows 套件時會遺失。

## 驗證

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q tests/test_zendriver_hardening.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirement.txt
git diff --check
```

## 重新建立 Windows 程式

只修改原始碼不會改變已存在的 `nodriver_tixcraft.exe`，必須重新封裝：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -Version 0.4.7
```

封裝後請使用專案既有的 `scripts/verify_release_archive.py` 驗證 Windows ZIP。

## 修正原理

`BrowserSessionManager.build_config()` 會在 `uc.start()` 建立 CDP Listener 前安裝一次冪等 guard。
guard 只在 Zendriver Transaction 的 Future 已經 `done()` 或 `cancelled()` 時丟棄遲到回應；
仍在 pending 的正常結果、Zendriver `ProtocolException` 與其他 `InvalidStateError` 都維持原行為。
