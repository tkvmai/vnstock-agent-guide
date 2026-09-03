# Deploy Breakout Screener lên server

> Viết cho server Linux (Ubuntu/Debian); ghi chú Windows ở cuối mỗi bước khi khác.
> Kiểm tra tự động: sau bước 4 chạy `python deploy_check.py` — nó tự soi từng mục.

## 0. Yêu cầu tối thiểu

- Python **≥ 3.11** (khuyến nghị 3.14 — bản đang chạy ổn ở máy gốc), RAM ≥ 2GB.
- Mạng ra ngoài tới API vnstocks.com/VCI và api.telegram.org.
- **Múi giờ server phải là Asia/Ho_Chi_Minh (UTC+7)** — thư viện `schedule` đặt job theo
  giờ LOCAL; server UTC thì job 08:00/15:30/h:05 sẽ chạy lệch 7 tiếng. Đặt ngay:
  ```bash
  sudo timedatectl set-timezone Asia/Ho_Chi_Minh
  ```
  (Windows: `tzutil /s "SE Asia Standard Time"`.)

## 1. Đưa code + DỮ LIỆU lên server

Code lấy từ git, nhưng **thư mục `data/` không nằm trong git** — phải copy tay từ máy cũ:

```bash
# trên server
git clone <repo của bạn> ~/vnstock-agent-guide
# từ máy Windows cũ (PowerShell, có sẵn scp) — copy dữ liệu:
scp -r C:\Users\tkvmai\Documents\GitHub\vnstock-agent-guide\breakout_app\data user@server:~/vnstock-agent-guide/breakout_app/
```

Tối thiểu phải có: `data/screener.db` (toàn bộ lịch sử tín hiệu/outcome/registry),
`data/telegram_config.json`. Nên có thêm: `data/history/` (kho 10 năm — MCDX Banker cần
lịch sử dài, backtest cần nó), `data/flow_history/`, `data/backtest/`. KHÔNG copy:
`__pycache__`, `*.log`, `screener.db.bak-*` (tùy).

## 2. Môi trường Python + gói public

```bash
cd ~/vnstock-agent-guide/breakout_app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Gói SPONSORED (vnstock_data) — bước dễ vướng nhất

`vnstock_data` **không cài được bằng pip thường** — nó phân phối qua installer với license.
Trên server (trong venv đã activate):

```bash
python -c "from vnstock_installer import setup; setup()"   # đăng nhập license Golden của bạn
```

(Installer sẽ hỏi đăng nhập/kích hoạt — dùng đúng tài khoản Golden `maitrant@gmail.com`;
sau khi xong sẽ có `~/.vnstock/auth_state.json` với `"tier": "golden"`. Nếu lệnh trên
không đúng với phiên bản installer của bạn, xem hướng dẫn tại vnstocks.com — mục cài đặt
gói sponsored; hoặc hỏi mình kèm thông báo lỗi.)

> Lưu ý license theo MÁY: kích hoạt trên server không ảnh hưởng máy cũ, nhưng nếu gói của
> bạn giới hạn số máy thì có thể phải gỡ bớt thiết bị trong trang quản lý tài khoản.

## 4. Kiểm tra tự động

```bash
python deploy_check.py          # 9 mục: python, gói, license, múi giờ, dữ liệu, API, port
python deploy_check.py --ping   # kèm gửi 1 tin Telegram thử
```

Tất cả ✅ thì mới sang bước 5.

## 5. Chạy thử bằng tay

```bash
python run.py                   # mặc định port 5006; đổi: --port 8080
```

- Mở `http://<ip-server>:5006` — thấy dashboard, banner regime/health.
- Đợi 1-2 scan (10 phút) xem log console không lỗi.
- Firewall: mở port cho IP của bạn (`sudo ufw allow from <ip-của-bạn> to any port 5006`).
  **Đừng mở public cho cả internet** — dashboard không có đăng nhập.

## 6. Chạy như service (tự khởi động, tự hồi khi crash)

`/etc/systemd/system/breakout.service`:

```ini
[Unit]
Description=Breakout Screener
After=network-online.target

[Service]
User=<user-của-bạn>
WorkingDirectory=/home/<user>/vnstock-agent-guide/breakout_app
ExecStart=/home/<user>/vnstock-agent-guide/breakout_app/.venv/bin/python run.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:/home/<user>/vnstock-agent-guide/breakout_app/data/server_console.log
StandardError=inherit

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now breakout
journalctl -u breakout -f          # xem log sống
```

(Windows Server: dùng Task Scheduler — trigger "At startup", action chạy
`...\.venv\Scripts\python.exe run.py`, working directory = breakout_app, tick "Restart on
failure".)

## 7. Nghiệm thu sau 1 ngày giao dịch

- [ ] Sáng: log `morning warmup` 08:00, bản tin 💰 ĐẦY ĐỦ 08:45 về Telegram.
- [ ] Trong phiên: scan mỗi 5', alert breakout h:00, tin 💰 h:05 (từ 11:05).
- [ ] 15:30: EOD scan + bản tin 💰 EOD-live; outcomes/obs cập nhật trong db.
- [ ] Dashboard tab 🎯/💰 có dữ liệu mới.
- [ ] TẮT app ở máy cũ (2 instance cùng gửi Telegram = tin đôi; db mỗi bên một nẻo).

## 8. AUTO-DEPLOY: push GitHub → server tự cập nhật (CI/CD)

Cơ chế: push lên `main` (có thay đổi trong `breakout_app/`) → GitHub Actions SSH vào
server → chạy `breakout_app/deploy.sh` (pull code, cài deps nếu requirements đổi, kiểm
import, restart service, xác nhận service sống). File workflow:
`.github/workflows/deploy-breakout.yml` — đã có sẵn trong repo.

### Thiết lập MỘT LẦN

**Đặt tên key theo app** (server chạy nhiều app deploy cùng kiểu không giẫm nhau: deploy
key GitHub chỉ gắn được 1 repo, và `~/.ssh/config` chọn block `Host` đầu tiên khớp — nên
mỗi app một key + một host ALIAS riêng): app này dùng `breakout_pull`, `breakout_actions`,
alias `github-breakout`.

**B. Server kéo được repo private — Deploy key (chiều server → GitHub, làm TRƯỚC A):**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/breakout_pull -N "" -C "breakout-server-pull"
printf "
Host github-breakout
  HostName github.com
  User git
  IdentityFile ~/.ssh/breakout_pull
" >> ~/.ssh/config
cat ~/.ssh/breakout_pull.pub     # copy dòng này
```
→ GitHub repo → **Settings → Deploy keys → Add key** → dán, KHÔNG tick write access.
Thử: `ssh -T git@github-breakout` → "successfully authenticated".

**A. Clone repo qua alias + chuyển dữ liệu vào:**
```bash
git clone git@github-breakout:tkvmai/vnstock-agent-guide.git ~/vnstock-agent-guide
mv ~/breakout_app/data ~/vnstock-agent-guide/breakout_app/data    # chuyển DỮ LIỆU vào
mv ~/breakout_app/.venv ~/vnstock-agent-guide/breakout_app/.venv  # venv lỗi thì tạo lại
chmod +x ~/vnstock-agent-guide/breakout_app/deploy.sh
# cập nhật WorkingDirectory/ExecStart trong /etc/systemd/system/breakout.service sang path mới
systemctl daemon-reload && systemctl restart breakout
```
> ⚠️ venv chứa path tuyệt đối — nếu `mv` xong mà `python` trong venv lỗi, xóa và tạo lại
> venv tại chỗ mới (bước 2+3 của phần cài đặt).

**C. Actions SSH được vào server (chiều GitHub → server):** tạo cặp khóa RIÊNG:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/breakout_actions -N "" -C "breakout-github-actions"
cat ~/.ssh/breakout_actions.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/breakout_actions      # PRIVATE key — copy toàn bộ
```
→ GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Giá trị |
|---|---|
| `DEPLOY_HOST` | IP server |
| `DEPLOY_USER` | `root` (hoặc user chạy app) |
| `DEPLOY_SSH_KEY` | nội dung private key `~/.ssh/breakout_actions` (cả BEGIN/END) |
| `DEPLOY_PORT` | (tùy chọn) nếu SSH khác cổng 22 |

Sau đó xóa private key khỏi server cho sạch: `rm ~/.ssh/breakout_actions` (đã nằm trong
Secret rồi).

**D. Thử:** GitHub → tab **Actions → Deploy Breakout Screener → Run workflow** (chạy tay
lần đầu để kiểm), hoặc commit bất kỳ vào `breakout_app/` rồi push. Xem log từng bước ngay
trên GitHub; trên server kiểm `journalctl -u breakout -n 20`.

### Lưu ý vận hành
- Deploy restart service ngay cả trong giờ giao dịch — mất tối đa 1 nhịp scan 5', app tự
  warmup lại; muốn tránh thì push ngoài 9:15-15:35.
- `deploy.sh` dùng `git reset --hard origin/main`: mọi sửa tay TRỰC TIẾP trên server (ngoài
  data/) sẽ bị ghi đè — đúng chủ đích, code chỉ sửa qua git.
- Rollback: `git revert <commit>` rồi push — Actions tự deploy bản revert.

## 9. Vận hành sau này

- Cập nhật code: chỉ cần push lên main (mục 8); thủ công thì `git pull` + `systemctl restart breakout`.
- Backup: cron copy `data/screener.db` hằng tuần là đủ (mọi thứ quan trọng nằm trong đó).
- Dữ liệu tự lành: ohlcv/flow tự tích lũy mỗi ngày; không cần đồng bộ gì từ máy cũ nữa.
