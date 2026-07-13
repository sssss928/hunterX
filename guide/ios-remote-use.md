# iOS 使用 HunterX 的正確方式

HunterX 是桌面端 Python / Windows 執行檔工具，iPhone 或 iPad 不能直接執行 `settings.exe`、`nodriver_tixcraft.exe`，也不能在 iOS Safari 內完成桌面瀏覽器自動化。iOS 可行的定位是「遠端控制」或「開設定頁查看狀態」。

## 方案 A：同一 Wi-Fi 內開設定頁

適合只想用手機看設定、暫停、繼續、停止實例。

1. 在 Windows 電腦解壓 HunterX，執行 `settings.exe`。
2. 設定頁啟動後，終端機會顯示類似：

   ```text
   server running on port: 16888
   goto url: http://127.0.0.1:16888/settings.html
   ```

3. 在 Windows 查電腦的區網 IP：

   ```powershell
   ipconfig
   ```

   找到目前 Wi-Fi 或乙太網路的 IPv4，例如 `192.168.1.23`。

4. iPhone 連到同一個 Wi-Fi，Safari 開：

   ```text
   http://192.168.1.23:16888/settings.html
   ```

5. 若打不開，檢查 Windows 防火牆是否允許 Python / `settings.exe` 在私人網路通訊。

注意：這個設定頁沒有登入密碼，不要把 port 開到公網，也不要在公共 Wi-Fi 使用。

## 方案 B：用遠端桌面控制電腦

適合要完整操作 HunterX 開出的桌面瀏覽器。

可用工具：

- Microsoft Remote Desktop
- Chrome Remote Desktop
- AnyDesk
- RustDesk
- Tailscale + Windows 遠端桌面

這種方式實際執行仍在 Windows 電腦上，iPhone 只是螢幕與鍵鼠控制端。穩定性通常比只用手機瀏覽設定頁更好，因為你能看到 HunterX 開出的瀏覽器、驗證碼、跳頁與錯誤訊息。

## 方案 C：外網安全連回家中電腦

如果不在同一個 Wi-Fi，建議用 VPN，不要直接 port forwarding。

推薦做法：

1. 電腦與 iPhone 都安裝 Tailscale。
2. 兩台裝置登入同一個 Tailscale 帳號。
3. iPhone 用 Tailscale IP 開：

   ```text
   http://<電腦的 Tailscale IP>:16888/settings.html
   ```

這比把 `16888` 直接開到網際網路安全很多。

## 不建議的做法

- 不建議嘗試在 iOS 上直接跑 Windows exe。
- 不建議把 HunterX 設定頁暴露到公網。
- 不建議在 iOS 捷徑、瀏覽器腳本或第三方 App 中重做自動購票流程。
- 不建議用同帳號在手機與電腦同時操作同一活動，這可能造成 session 被踢或跳回詳情頁。

## 實務建議

最穩定的使用方式是：Windows 電腦負責執行 HunterX 與桌面瀏覽器；iPhone 只負責遠端查看、暫停、停止、確認狀態。若要處理驗證碼或付款，使用遠端桌面看電腦畫面會比手機 Safari 開設定頁更完整。

