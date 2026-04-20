#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="lapwing-sandbox"

if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "[OK] Network '$NETWORK_NAME' already exists."
else
    docker network create \
        --driver bridge \
        --opt com.docker.network.bridge.enable_icc=false \
        "$NETWORK_NAME"
    echo "[OK] Created network '$NETWORK_NAME' (bridge, ICC disabled)."
fi
