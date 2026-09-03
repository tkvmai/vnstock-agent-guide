"""Đồng bộ screener.db giữa SERVER (bản chính, từ 03/09/2026) và máy dev local
— phục vụ quy trình review mã thua / mã bỏ sót chạy trong phiên Claude Code.

    python sync_db.py pull    # kéo db server về local (backup bản local trước)
    python sync_db.py push    # đẩy các bản ghi REVIEW (loss_reviews, miss_reviews)
                              # từ local lên server (INSERT OR REPLACE — idempotent)

Quy trình review chuẩn từ khi app chạy server:
    pull → review như cũ trên local → push

An toàn: `pull` ghi đè db local (mọi outcome/obs mới nhất từ server); `push` CHỈ đụng
2 bảng registry mà app server không bao giờ ghi (chỉ đọc) nên không có xung đột.
SSH dùng host alias `maitt` (cấu hình trong ~/.ssh/config, port 2222); chưa cài key
thì mỗi lệnh sẽ hỏi mật khẩu root một lần.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

HOST = os.environ.get("BREAKOUT_SSH_HOST", "maitt")
REMOTE_DB = "~/vnstock-agent-guide/breakout_app/data/screener.db"
REMOTE_PY = "~/vnstock-agent-guide/breakout_app/.venv/bin/python"
HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB = os.path.join(HERE, "data", "screener.db")
REVIEW_TABLES = {"loss_reviews": ("symbol", "reco_date", "cause", "reviewed_ts"),
                 "miss_reviews": ("symbol", "obs_date", "cause", "reviewed_ts")}


def run(cmd, **kw):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def pull():
    if os.path.exists(LOCAL_DB):
        bak = LOCAL_DB + ".pre-pull"
        shutil.copy2(LOCAL_DB, bak)
        print(f"backup local -> {bak}")
    # snapshot nhất quán trên server (sqlite backup API, tránh copy giữa lúc ghi)
    run(["ssh", HOST,
         f"{REMOTE_PY} -c \"import sqlite3; s=sqlite3.connect('vnstock-agent-guide/"
         f"breakout_app/data/screener.db'); d=sqlite3.connect('/tmp/screener_snapshot.db');"
         f" s.backup(d); d.close(); print('snapshot ok')\""])
    run(["scp", f"{HOST}:/tmp/screener_snapshot.db", LOCAL_DB])
    con = sqlite3.connect(LOCAL_DB)
    n = con.execute("SELECT COUNT(*), MAX(reco_date) FROM tracked_signals").fetchone()
    con.close()
    print(f"✅ pull xong — tracked_signals: {n[0]} dòng, mới nhất {n[1]}")


def push():
    con = sqlite3.connect(LOCAL_DB)
    payload = {}
    for table, cols in REVIEW_TABLES.items():
        rows = con.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        payload[table] = {"cols": cols, "rows": rows}
        print(f"{table}: {len(rows)} dòng local")
    con.close()
    tmp = os.path.join(tempfile.gettempdir(), "review_sync.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    run(["scp", tmp, f"{HOST}:/tmp/review_sync.json"])
    apply_code = (
        "import json,sqlite3;"
        "d=json.load(open('/tmp/review_sync.json',encoding='utf-8'));"
        "c=sqlite3.connect('vnstock-agent-guide/breakout_app/data/screener.db');"
        "[c.executemany('INSERT OR REPLACE INTO '+t+'('+','.join(v['cols'])+') VALUES ("
        "'+','.join(['?']*len(v['cols']))+')', v['rows']) for t,v in d.items()];"
        "c.commit();"
        "print('server:', {t: c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in d})"
    )
    run(["ssh", HOST, f"{REMOTE_PY} -c \"{apply_code}\""])
    print("✅ push xong — registry trên server đã có đủ các ca đã review")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "pull":
        pull()
    elif mode == "push":
        push()
    else:
        print(__doc__)
        sys.exit(1)
