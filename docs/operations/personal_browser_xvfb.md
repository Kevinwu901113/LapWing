# Personal Browser (Xvfb + VNC takeover)

This document covers operating the **personal browser profile** introduced
by the v1 BrowserAdapter (blueprint §6, §6.6). The personal profile is
headful Playwright with a persistent `user_data_dir` so that login state +
cookies + extensions survive across sessions, and so that Kevin can
**take over** the browser (solve a CAPTCHA, perform a 2FA, etc.) when the
agent hits an interrupt.

Because PVE servers do not have a physical display, the personal profile
runs against a virtual X server (Xvfb) on display `:99`. Kevin connects
to the same X display via a VNC client (x11vnc bridge) to perform takeover.

## Components

| Component | Purpose | Configuration |
|---|---|---|
| `Xvfb :99` | Virtual X display (1440×900x24) | `ops/systemd/lapwing-xvfb.service` |
| Playwright `launch_persistent_context(headless=False)` | Headful browser bound to `DISPLAY=:99` | `BrowserAdapter(profile="personal")` per blueprint §6.2 |
| `x11vnc` | Bridges display `:99` over the VNC protocol | manual launch, see below |
| Kevin's VNC client (TigerVNC / Tiger / RealVNC etc.) | Renders the browser; supports keyboard + mouse takeover | local desktop |

## Install + enable

```bash
sudo apt-get install -y xvfb x11vnc

# Install the service file
sudo cp ops/systemd/lapwing-xvfb.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lapwing-xvfb.service
sudo systemctl status lapwing-xvfb
```

Confirm Xvfb is running:

```bash
ps aux | grep Xvfb
ls /tmp/.X11-unix/   # should contain X99
DISPLAY=:99 xdpyinfo | head -3   # should report dimensions: 1440x900
```

## Lapwing-side wiring

`BrowserAdapter(profile="personal")` will set `DISPLAY=:99` in its
Playwright launch environment and use `user_data_dir =
/home/lapwing/.config/lapwing-browser` (per `config.toml` block in
blueprint §6.1). Confirm that directory exists and is owned by the same
user that runs the kernel.

## VNC takeover (Kevin's side)

When an Interrupt of kind `browser.captcha` / `browser.login_required` /
`browser.auth_2fa` / `browser.waf` appears in the Desktop
`/interrupts/pending` list, the Lapwing summary will tell Kevin which
page needs attention. Kevin then:

1. Start an x11vnc bridge **on the server**:
   ```bash
   x11vnc -display :99 -localhost -auth /home/lapwing/.Xauthority -nopw -forever &
   ```
   (or run as a systemd service if you want it always-on — see
   `--shared` if multiple operators need to connect simultaneously.)

2. From the laptop, open an SSH tunnel and connect a VNC client:
   ```bash
   ssh -L 5900:localhost:5900 lapwing@pve-home-01
   # then in your VNC client:
   vncviewer localhost:5900
   ```

3. Solve the CAPTCHA / log in / etc. in the visible browser window. The
   `user_data_dir` persists state automatically.

4. Approve the interrupt via the Desktop UI (`POST /interrupts/{id}/approve`).
   Lapwing's worker resumes from the suspension point — it does NOT
   restart the action or recreate the browser context (blueprint §15.1).

## Security notes

- `x11vnc -localhost` binds to 127.0.0.1 only; an SSH tunnel is required
  for remote access. Do not omit `-localhost`.
- The personal browser holds Kevin's logged-in identities. Treat
  `user_data_dir` as sensitive: filesystem permissions should be 700
  owned by the `lapwing` user.
- Lapwing's policy (`PolicyDecider._decide_browser` per blueprint §4.4)
  marks `login` / `download` / `form_submit` on the personal profile as
  `PolicyDecision.INTERRUPT` — owner approval is required for those
  verbs. CAPTCHA detection is also gated through interrupts; no
  auto-solve path exists (blueprint §15.2 I-3 invariant).

## Deferred to v2

- noVNC web bridge embedded in the Tauri Desktop app (blueprint §6.6).
- Multi-display support for parallel takeover sessions.
- Audio support (`-audio` on Xvfb).

## Troubleshooting

- "Cannot open display :99" — `lapwing-xvfb.service` is not running, or
  Lapwing is launched as a user that doesn't have access to the X
  authority. Check `systemctl status lapwing-xvfb` and the `User=`
  setting in the unit.
- Playwright launch hangs — install Playwright deps once:
  `playwright install chromium --with-deps`.
- "Authority file not readable" on VNC connect — make sure x11vnc has
  read access to `/home/lapwing/.Xauthority` (or run as the same user).
