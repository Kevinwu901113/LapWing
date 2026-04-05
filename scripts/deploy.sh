#!/usr/bin/env bash
# Lapwing 部署脚本 — 确保单实例运行
set -euo pipefail

LAPWING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$LAPWING_DIR/data/lapwing.pid"
VENV_PYTHON="$LAPWING_DIR/venv/bin/python"
LOG_FILE="$LAPWING_DIR/data/logs/lapwing.log"

echo "[deploy] 停止旧进程..."
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID"
        # 等待最多 10 秒
        for i in $(seq 1 10); do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 1
        done
        # 如果还在，强制杀
        kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" || true
    fi
fi

# 兜底：杀掉所有残留的 main.py 进程
pkill -f "python.*main\.py" 2>/dev/null || true
sleep 1

echo "[deploy] 启动新进程..."
mkdir -p "$LAPWING_DIR/data/logs"
cd "$LAPWING_DIR"
nohup "$VENV_PYTHON" main.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "[deploy] Lapwing 已启动 (PID: $NEW_PID)"
