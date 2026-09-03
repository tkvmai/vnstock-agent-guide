"""Kiểm tra server trước khi deploy Breakout Screener — chạy TRÊN SERVER:

    python deploy_check.py            # kiểm tra tất cả
    python deploy_check.py --ping     # kèm gửi tin Telegram thử

Mỗi mục in ✅/❌ kèm cách sửa. Thoát mã 0 nếu đủ điều kiện chạy."""

import importlib
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

OK, BAD = "✅", "❌"
fails = []


def check(name, ok, fix=""):
    print(f"{OK if ok else BAD} {name}" + ("" if ok else f"\n   → {fix}"))
    if not ok:
        fails.append(name)
    return ok


# 1. Python
check(f"Python {sys.version.split()[0]} (cần >= 3.11)", sys.version_info >= (3, 11),
      "Cài Python 3.11+ (khuyến nghị 3.14 cho khớp bản gốc)")

# 2. Gói public
for mod in ("pandas", "numpy", "panel", "schedule", "pyarrow", "requests"):
    try:
        m = importlib.import_module(mod)
        check(f"{mod} {getattr(m, '__version__', '?')}", True)
    except ImportError:
        check(mod, False, "pip install -r requirements.txt")

# 3. vnstock + gói sponsored
try:
    import vnstock
    check(f"vnstock {getattr(vnstock, '__version__', '?')}", True)
except ImportError:
    check("vnstock", False, "pip install -r requirements.txt")
try:
    from importlib.metadata import version
    check(f"vnstock_data {version('vnstock_data')} (sponsored)", True)
except Exception:
    check("vnstock_data (sponsored)", False,
          "Cài qua script headless: python sponsored_install.py — xem DEPLOY.md bước 3")
try:
    from importlib.metadata import version as _v
    check(f"vnii {_v('vnii')} (mở khóa rate-limit sponsor)", True)
except Exception:
    check("vnii (mở khóa rate-limit sponsor)", False,
          "THIẾU vnii → runtime bị coi là FREE 60 req/phút dù license golden! Cài: "
          "pip install --extra-index-url https://vnstocks.com/api/simple vnii "
          "(hoặc python sponsored_install.py vnii)")

# 4. License tier
auth = os.path.join(os.path.expanduser("~"), ".vnstock", "auth_state.json")
tier = None
try:
    with open(auth, encoding="utf-8") as f:
        tier = (json.load(f) or {}).get("tier")
except Exception:
    pass
check(f"License vnstock: tier = {tier}", tier in ("bronze", "silver", "golden", "diamond"),
      "Chưa đăng nhập license trên máy này — chạy bước kích hoạt trong DEPLOY.md bước 3")

# 5. Múi giờ — QUAN TRỌNG: schedule dùng giờ LOCAL của server
offset_h = -time.timezone / 3600 if not time.daylight else -time.altzone / 3600
check(f"Múi giờ server: UTC{offset_h:+.0f} (cần UTC+7)", abs(offset_h - 7) < 0.01,
      "Linux: sudo timedatectl set-timezone Asia/Ho_Chi_Minh · Windows: tzutil /s \"SE Asia Standard Time\". "
      "Không đổi được thì PHẢI dịch mọi giờ trong start_scheduler (08:00/15:30/h:00…) sang giờ local tương ứng")

# 6. Dữ liệu mang theo
here = os.path.dirname(os.path.abspath(__file__))
data = os.path.join(here, "data")
for f, why in (("screener.db", "toàn bộ lịch sử tín hiệu/outcome/registry"),
               ("telegram_config.json", "bot token + chat_id"),):
    check(f"data/{f} ({why})", os.path.exists(os.path.join(data, f)),
          f"Copy {f} từ máy cũ sang data/ (file này KHÔNG có trong git)")
for d, why in (("history", "kho OHLCV 10 năm — cần cho MCDX Banker & backtest"),
               ("flow_history", "kho dòng tiền 7.5 năm — cần cho nghiên cứu")):
    p = os.path.join(data, d)
    n = len(os.listdir(p)) if os.path.isdir(p) else 0
    check(f"data/{d}/ ({n} file — {why})", n > 0,
          f"Copy thư mục data/{d}/ từ máy cũ (không bắt buộc để app chạy, nhưng thiếu thì "
          f"Banker tính trên lịch sử ngắn và không chạy lại backtest được)")

# 7. Gọi API thử (mạng + license hoạt động thật)
try:
    sys.path.insert(0, here)
    from data import fetchers
    vn = fetchers.fetch_vnindex(days=10)
    check(f"API vnstock_data hoạt động (VNINDEX {len(vn)} phiên, mới nhất "
          f"{vn['time'].iloc[-1].date()})", vn is not None and len(vn) > 0)
except Exception as e:
    check("API vnstock_data", False, f"Lỗi: {type(e).__name__}: {e} — kiểm tra mạng/license")

# 8. Port
import socket
port = 5006
s = socket.socket()
try:
    s.bind(("0.0.0.0", port)); s.close(); free = True
except OSError:
    free = False
check(f"Port {port} trống", free, f"Đổi port: python run.py --port <khác>, hoặc tắt process đang chiếm")

# 9. Telegram (tùy chọn --ping)
if "--ping" in sys.argv:
    try:
        import notify
        ok = notify.send_telegram("🔧 Deploy check: server mới gửi tin thành công.")
        check("Gửi Telegram thử", ok, "Kiểm tra data/telegram_config.json + mạng ra ngoài")
    except Exception as e:
        check("Gửi Telegram thử", False, str(e))

print()
if fails:
    print(f"{BAD} CHƯA SẴN SÀNG — {len(fails)} mục cần sửa: {', '.join(fails)}")
    sys.exit(1)
print(f"{OK} SẴN SÀNG — chạy:  python run.py   (hoặc cài service theo DEPLOY.md bước 6)")
