#!/usr/bin/env bash
# uninstall_pve.sh — remove the Lapwing systemd stack from a PVE host.
#
# Does NOT remove:
#   - the lapwing user
#   - the persistent personal-browser profile directory (cookies / sessions)
#   - the lapwing.db / kernel.db / data directory
#   - apt-installed packages (xvfb, x11vnc, x11-utils)
#
# If you want a full wipe, do those steps by hand after running this.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (sudo)" >&2
    exit 1
fi

TARGET_DIR=/etc/systemd/system

for unit in lapwing.service lapwing-x11vnc.service lapwing-xvfb.service; do
    if systemctl is-enabled "$unit" &>/dev/null; then
        systemctl disable --now "$unit" || true
    elif systemctl is-active "$unit" &>/dev/null; then
        systemctl stop "$unit" || true
    fi
    if [[ -f "$TARGET_DIR/$unit" ]]; then
        rm -f "$TARGET_DIR/$unit"
        echo "removed $TARGET_DIR/$unit"
    fi
done

systemctl daemon-reload

echo
echo "Lapwing systemd units removed."
echo "Persistent data retained (profile dir, db, lapwing user)."
echo "To fully wipe:"
echo "  sudo rm -rf ~lapwing/.config/lapwing-browser  ~lapwing/lapwing/data"
echo "  sudo userdel -r lapwing"
