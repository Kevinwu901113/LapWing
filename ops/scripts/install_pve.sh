#!/usr/bin/env bash
# install_pve.sh — install + enable Lapwing systemd stack on a PVE host.
#
# Idempotent: safe to re-run after editing units.
# Requires: root (sudo). Target user: lapwing (must exist).
#
# Post-v1 B §6.1 deliverable. Installs:
#   - lapwing-xvfb.service   (Xvfb :99 virtual display)
#   - lapwing-x11vnc.service (VNC bridge over 127.0.0.1:5900)
#   - lapwing.service        (main process, DISPLAY=:99 inherited)
#
# Does NOT install lapwing's Python deps or browser binaries — that's
# operator responsibility (see takeover.md §Prerequisites).

set -euo pipefail

# ── Preflight ────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (sudo)" >&2
    exit 1
fi

# Identify the lapwing checkout root (this script lives at <root>/ops/scripts/).
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/../.. && pwd)"
UNIT_DIR="$ROOT/ops/systemd"
TARGET_DIR=/etc/systemd/system

LAPWING_USER="${LAPWING_USER:-lapwing}"

if ! id "$LAPWING_USER" &>/dev/null; then
    echo "user '$LAPWING_USER' does not exist — create it first:" >&2
    echo "  sudo useradd -m -s /bin/bash $LAPWING_USER" >&2
    exit 1
fi

# Required system packages (Debian / Ubuntu / PVE). Skip if already present.
NEED_PKGS=()
command -v Xvfb    >/dev/null || NEED_PKGS+=(xvfb)
command -v x11vnc  >/dev/null || NEED_PKGS+=(x11vnc)
command -v xdpyinfo >/dev/null || NEED_PKGS+=(x11-utils)
if [[ ${#NEED_PKGS[@]} -gt 0 ]]; then
    echo "installing missing packages: ${NEED_PKGS[*]}"
    apt-get update -qq
    apt-get install -y "${NEED_PKGS[@]}"
fi

# ── Personal-profile directory (persistent, 700 owned by lapwing) ───────────
# Code path: {data_dir}/browser_profiles/personal (see container.py).
# We materialize the dir at the path the runtime will use; operator overrides
# via $LAPWING_DATA_DIR if data_dir is non-default.

LAPWING_HOME=$(getent passwd "$LAPWING_USER" | cut -d: -f6)
DATA_DIR="${LAPWING_DATA_DIR:-$ROOT/data}"
PROFILE_DIR="$DATA_DIR/browser_profiles/personal"
install -d -m 700 -o "$LAPWING_USER" -g "$LAPWING_USER" "$PROFILE_DIR"
echo "personal profile dir: $PROFILE_DIR (700, $LAPWING_USER)"

# Convenience symlink so the spec literal path resolves (Post-v1 B §2.3).
LINK="$LAPWING_HOME/.config/lapwing-browser"
if [[ ! -e "$LINK" && ! -L "$LINK" ]]; then
    install -d -m 755 -o "$LAPWING_USER" -g "$LAPWING_USER" "$(dirname "$LINK")"
    sudo -u "$LAPWING_USER" ln -s "$PROFILE_DIR" "$LINK"
    echo "symlinked $LINK -> $PROFILE_DIR"
fi

# ── Install unit files ──────────────────────────────────────────────────────

for unit in lapwing-xvfb.service lapwing-x11vnc.service lapwing.service; do
    install -m 644 "$UNIT_DIR/$unit" "$TARGET_DIR/$unit"
    echo "installed $TARGET_DIR/$unit"
done

systemctl daemon-reload

# ── Enable + start in dependency order ──────────────────────────────────────
# lapwing-xvfb first (lapwing.service Requires= it), then x11vnc (optional —
# only running when Kevin needs takeover), then lapwing main.
# We do NOT auto-start lapwing.service from this script — operator decides
# when to flip the main process on after verifying Xvfb + browser deps.

systemctl enable --now lapwing-xvfb.service
echo "lapwing-xvfb.service enabled + started"

systemctl enable lapwing-x11vnc.service
echo "lapwing-x11vnc.service enabled (start on demand: 'systemctl start lapwing-x11vnc')"

systemctl enable lapwing.service
echo "lapwing.service enabled (NOT started — operator launches via: 'systemctl start lapwing')"

# ── Sanity check ────────────────────────────────────────────────────────────

echo
echo "── verification (sanity) ────────────────────────────────────────────"
systemctl is-active lapwing-xvfb.service && echo "Xvfb: active" || echo "Xvfb: NOT active"
DISPLAY=:99 sudo -u "$LAPWING_USER" xdpyinfo 2>&1 | head -3 || echo "xdpyinfo failed — see journalctl -u lapwing-xvfb"
echo "─────────────────────────────────────────────────────────────────────"
echo
echo "Next: see docs/operations/personal_browser_takeover.md §Bring-up"
