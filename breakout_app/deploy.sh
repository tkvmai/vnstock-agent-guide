#!/usr/bin/env bash
# Server-side deploy: được GitHub Actions gọi qua SSH sau mỗi push lên main.
# An toàn dữ liệu: data/screener.db, telegram_config, history/... đều nằm trong
# .gitignore nên git không bao giờ đụng tới.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/vnstock-agent-guide}"
APP_DIR="$REPO_DIR/breakout_app"
SERVICE="${SERVICE:-breakout}"

cd "$REPO_DIR"
echo "== [1/4] Cập nhật code =="
git fetch origin main
OLD=$(git rev-parse HEAD)
git reset --hard origin/main
NEW=$(git rev-parse HEAD)
echo "   $OLD -> $NEW"

echo "== [2/4] Dependencies =="
source "$APP_DIR/.venv/bin/activate"
if git diff --name-only "$OLD" "$NEW" | grep -q "breakout_app/requirements.txt"; then
    echo "   requirements.txt đổi -> pip install"
    pip install -r "$APP_DIR/requirements.txt"
else
    echo "   requirements.txt không đổi -> bỏ qua"
fi

echo "== [3/4] Sanity: import app =="
cd "$APP_DIR"
python -c "import sys; sys.path.insert(0,'.'); import scheduler, app; print('   import OK')"

echo "== [4/4] Restart service =="
systemctl restart "$SERVICE"
sleep 5
systemctl is-active --quiet "$SERVICE" && echo "   $SERVICE: RUNNING" || {
    echo "   $SERVICE: FAILED — log gần nhất:"; journalctl -u "$SERVICE" -n 20 --no-pager; exit 1; }
echo "== DEPLOY XONG =="
