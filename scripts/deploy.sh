#!/usr/bin/env bash
# Lapwing 部署脚本 — 通过 systemd 管理
set -euo pipefail

LAPWING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$LAPWING_DIR/data/lapwing.pid"

# 兜底：杀掉 nohup 遗留进程（systemd 接管后不会再有）
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[deploy] 停止 nohup 遗留进程 (PID: $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        for i in $(seq 1 10); do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" 2>/dev/null || true
    fi
fi

sudo systemctl restart lapwing
echo "[deploy] Lapwing 已重启 ($(systemctl is-active lapwing))"
