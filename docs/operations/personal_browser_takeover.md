# Personal Browser Takeover — PVE Runbook

> Post-v1 B authoritative runbook. Replaces `personal_browser_xvfb.md` as
> the operational source-of-truth; that file is retained as a deprecation
> stub.

The **personal browser profile** is the headful Playwright context that
Lapwing uses for signed-in operations (GitHub, Gmail, Weibo, etc.). Because
PVE has no GUI, the profile lives behind a virtual X display (`Xvfb :99`).
When Lapwing hits a CAPTCHA / login / 2FA / WAF challenge, it pauses on a
kernel `Interrupt` and Kevin **takes over** the browser through a VNC
bridge over an SSH tunnel.

## Architecture

```text
┌───────────────────────────┐
│  Kevin's workstation      │
│  ┌─────────────────────┐  │
│  │ VNC client          │──┼─── ssh -L 5900:127.0.0.1:5900 ──┐
│  └─────────────────────┘  │                                  │
└───────────────────────────┘                                  │
                                                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PVE home server (lapwing user)                                         │
│                                                                         │
│  lapwing-xvfb.service   →  Xvfb :99 (1440x900x24)                       │
│  lapwing-x11vnc.service →  x11vnc -display :99 -localhost (127.0.0.1)   │
│  lapwing.service        →  python main.py (DISPLAY=:99)                 │
│         │                                                               │
│         ├─ BrowserAdapter(profile=personal) ──► Playwright (headful)    │
│         │       user_data_dir = {data_dir}/browser_profiles/personal    │
│         │                                                               │
│         └─ Kernel.execute(Action) ──► Interrupt on challenge            │
│                       │                                                 │
│                       └─► /api/v2/interrupts/pending                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

Before running `install_pve.sh`:

1. Linux user `lapwing` exists with home `/home/lapwing/`.
2. Lapwing checkout is at `/home/lapwing/lapwing/` (or edit
   `WorkingDirectory=` in `ops/systemd/lapwing.service`).
3. A Python virtualenv at `/home/lapwing/lapwing/.venv/` with `pip install
   -e .` complete (or edit `ExecStart=` accordingly).
4. Playwright browser binaries installed:
   ```bash
   sudo -u lapwing /home/lapwing/lapwing/.venv/bin/playwright install chromium --with-deps
   ```
5. `config.toml` production tweaks:
   ```toml
   [browser]
   enabled = true        # default is false in repo
   headless = false      # default is true; MUST be false on PVE so Chrome
                         # binds the Xvfb display and Kevin can VNC in
   ```
6. Network reachable to whatever sites Lapwing will browse.

## Install

```bash
sudo /home/lapwing/lapwing/ops/scripts/install_pve.sh
```

The script:

- Installs `xvfb`, `x11vnc`, `x11-utils` if missing.
- Creates the personal profile dir at `{data_dir}/browser_profiles/personal`
  with `0700 lapwing:lapwing`.
- Symlinks `~lapwing/.config/lapwing-browser` to the profile dir for
  spec-literal-path compatibility.
- Drops three systemd units into `/etc/systemd/system/`.
- Enables + starts `lapwing-xvfb.service`.
- Enables (but does not start) `lapwing-x11vnc.service` and
  `lapwing.service` — operator launches them after verification.

## Bring-up sequence

```bash
# 1. Confirm Xvfb is up (already started by install)
sudo systemctl status lapwing-xvfb.service          # active (running)
DISPLAY=:99 xdpyinfo | head -3                       # dimensions 1440x900

# 2. Start the main process
sudo systemctl start lapwing.service
sudo systemctl status lapwing.service               # active (running)
sudo journalctl -u lapwing -f                       # confirm boot logs clean

# 3. Sanity-check the browser launches
#    Trigger a navigate via the resident_operator from the Desktop UI
#    or the Lapwing chat surface ("navigate https://example.com")
#    then verify a Chrome window exists on :99:
sudo -u lapwing DISPLAY=:99 xwininfo -root -children | grep -i chrome
```

## Takeover flow (when an Interrupt fires)

When Lapwing pauses on a challenge, an `Interrupt` row of kind
`browser.captcha` / `browser.login_required` / `browser.auth_2fa` /
`browser.waf` appears at `/api/v2/interrupts/pending`. Kevin gets a
notification (desktop SSE push or polling Desktop UI). Standard takeover:

```bash
# 1. Start the VNC bridge on the server (one-shot)
sudo systemctl start lapwing-x11vnc.service

# 2. From workstation: open SSH tunnel
ssh -L 5900:127.0.0.1:5900 lapwing@pve-home

# 3. From workstation: open VNC client to localhost:5900
#    macOS Screen Sharing:  vnc://localhost:5900
#    Linux Remmina / TigerVNC:  localhost:5900
#    Windows TigerVNC: localhost::5900

# 4. Solve the CAPTCHA / log in / complete 2FA in the browser window.
#    The user_data_dir persists cookies + localStorage automatically;
#    subsequent visits keep the session.

# 5. Close the VNC client + tear down the tunnel.
sudo systemctl stop lapwing-x11vnc.service           # optional — free port 5900

# 6. Approve the interrupt
INTERRUPT_ID=...   # from /api/v2/interrupts/pending
curl -X POST http://localhost:8001/api/v2/interrupts/$INTERRUPT_ID/approve \
     -H 'Content-Type: application/json' \
     -d '{"payload":{"ok":true}}'
# Expect: {"status":"resumed", "interrupt_id":"...", "continuation_ref":"..."}
# The worker retries the same Action from its suspension point. EventLog
# records: interrupt.created → interrupt.resolved → browser.ok.
```

## Security

- **x11vnc is bound to 127.0.0.1 only.** The `-localhost` flag and the
  systemd unit's `Requires=lapwing-xvfb` configuration together prevent
  LAN or public-IP exposure. Do not remove `-localhost` from
  `lapwing-x11vnc.service` without adding a real authentication layer
  (e.g. `-rfbauth /etc/lapwing/vnc.passwd`).
- **SSH carries authentication.** `-nopw` is acceptable only because the
  tunnel endpoint is reachable solely through an authenticated SSH
  session. If your threat model includes LAN-side compromise of any host
  that can SSH as `lapwing`, add a VNC password via `-rfbauth`.
- **Personal profile is sensitive.** Cookies, OAuth tokens, and session
  storage all live under the profile dir. `install_pve.sh` enforces
  `0700` perms. Back up to encrypted storage only; do not commit the
  directory or include in plain-text rsync to shared hosts.
- **No auto-solve.** Lapwing's policy explicitly marks
  `login` / `download` / `form_submit` on the personal profile as
  `PolicyDecision.INTERRUPT` (blueprint §4.4 invariant I-3). CAPTCHA
  detection routes through interrupts; there is no path that bypasses
  Kevin's approval.

## Profile data retention

- **Restart preserves state.** `systemctl restart lapwing` does not
  affect `{data_dir}/browser_profiles/personal/`. Cookies survive.
- **Backup.** No automated backup in v1. Suggested cadence: weekly
  `rsync -a --delete /home/lapwing/.config/lapwing-browser/
  user@backup-host:/srv/lapwing-backups/browser-profile-$(date +%F)/`.
- **Disk monitoring.** Watch profile growth via
  `du -sh /home/lapwing/.config/lapwing-browser/{Default/Cookies,Default/Local\ Storage,Default/Cache}`.
  Cache growing without bound is a Chrome bug — clear with a fresh
  profile launch if it exceeds a few hundred MB.

## Troubleshooting

| Symptom | First check | Likely cause |
|---|---|---|
| `lapwing.service` won't start | `journalctl -u lapwing -n 50` | DISPLAY missing, venv path wrong, port conflict |
| Browser launches but I see no window in VNC | `xwininfo -root -children -display :99` | Chrome crashed (check OOM in dmesg); restart `lapwing.service` |
| VNC connect refused | `ss -lnt sport = 5900` on PVE | `lapwing-x11vnc.service` not running |
| VNC connects but black screen | `xdpyinfo -display :99` | Xvfb running, but no client drew anything yet — open a browser to test |
| Approve returns 404 | `curl /api/v2/interrupts/pending` | Interrupt already resolved or main process restarted (interrupt state survives, but in-process `ContinuationRegistry` does not — see §15.4 of blueprint) |
| Profile lost cookies after restart | `ls -la ~lapwing/.config/lapwing-browser/Default/` | Profile path mounted on tmpfs/overlay; verify with `df -T` |
| OOM kill of lapwing.service | `dmesg | tail -50` | Tune `MemoryMax=` in unit; baseline ~500MB headful Chrome + Lapwing |

## Uninstall

```bash
sudo /home/lapwing/lapwing/ops/scripts/uninstall_pve.sh
```

This stops + disables + removes the three units. **Profile data and the
lapwing user are preserved.** See script comment block for full-wipe
commands.

## Verification (Post-v1 B §5)

| Acceptance | Command |
|---|---|
| V-B1 Xvfb up | `systemctl is-active lapwing-xvfb && DISPLAY=:99 xdpyinfo \| head -3` |
| V-B2 browser launches | `DISPLAY=:99 xwininfo -root -children \| grep -i chrome` after main process trigger |
| V-B3 takeover works | SSH tunnel + VNC client renders Chrome window, mouse + keyboard responsive |
| V-B4 navigate hits a challenge | Send `navigate https://github.com/login` to resident_operator |
| V-B5 interrupt visible | `curl localhost:8001/api/v2/interrupts/pending` returns the row |
| V-B6 approve resumes <1s | timed `curl -X POST .../approve` |
| V-B7 EventLog sequence | `sqlite3 data/kernel.db "SELECT type, summary FROM events ORDER BY id DESC LIMIT 10"` |
| V-B8 cookie persistence | Restart `lapwing.service`, re-navigate, no login prompt |

## See also

- `docs/architecture/lapwing_v1_blueprint.md` §6, §6.6, §15.1
- `docs/operations/personal_browser_xvfb.md` — deprecated; kept for link
  stability
- `ops/systemd/lapwing-xvfb.service`,
  `ops/systemd/lapwing-x11vnc.service`,
  `ops/systemd/lapwing.service`
