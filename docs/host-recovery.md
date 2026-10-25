# Never getting locked out again

In July 2026 an interactive `tailscale login` was run on the host over the very
SSH session that depended on it. The session died before the browser flow was
completed, the node dropped off the tailnet, and — with Tailscale as the only
route in and no out-of-band console on that box — there was no way back.

Three separate things had to be true for that to be unrecoverable, so the fix
addresses all three.

## 1. Install the watchdog and guard (`ops/`)

Create a Tailscale **OAuth client** at
[login.tailscale.com/admin/settings/oauth](https://login.tailscale.com/admin/settings/oauth)
with scope `auth_keys` (write) and tag `tag:server`. Use an OAuth client rather
than a plain auth key — reusable auth keys expire after 90 days and would
silently rot; OAuth clients do not expire.

```bash
cd ~/sms-api/prod
sudo ./ops/install-recovery.sh tskey-client-XXXXXXXXXXXX
```

That installs:

- **`tailscale-watchdog.sh`** on a systemd timer, every 2 minutes and 30s after
  boot. If the node is in `NeedsLogin`, `NoState` or `Stopped` it re-authenticates
  non-interactively from the stored credential. Any state it does not positively
  recognise — including an unreadable status or a missing `jq` — is treated as
  "cannot tell" and it takes no action, so a healthy node is never churned.
- **A guard wrapper** at `/usr/local/bin/tailscale`, which shadows the real
  binary for both plain and `sudo` calls. It refuses `login`/`logout`/`down`/
  `up`/`set` from an interactive SSH session that is not inside tmux, and tells
  you what to run instead. Scripts, systemd and CI are unaffected.

Two manual follow-ups the installer cannot do:

- **ACLs.** Re-authenticating with a tag makes the node tailnet-owned rather
  than user-owned, so your existing personal grants stop applying. Add
  `tagOwners`, a `grants` entry and an `ssh` rule for `tag:server` — the exact
  snippet is printed at the end of the install.
- **Connect by MagicDNS name, not IP** (`ssh server317@server317`). Tagging can
  change the node's `100.x` address.

Pause the watchdog for deliberate maintenance with
`sudo touch /etc/tailscale-recovery/paused`, and check on it with:

```bash
systemctl list-timers tailscale-watchdog.timer
journalctl -t tailscale-watchdog -n 20
```

## 2. Set up a second, non-Tailscale way in

The watchdog only helps when Tailscale fails *accidentally*. It cannot help if
the credential is revoked, the ACLs lock you out, or the Tailscale control plane
itself is the problem. You need one path that does not involve Tailscale at all.

A Cloudflare Tunnel is the right fit here: it is **outbound-only**, so it needs
no port forward and survives anything that happens to the tailnet. Because
token-based tunnels are configured from the dashboard, you can also add or
repoint a route *without touching the server* — which is exactly the capability
that was missing during the lockout.

Install `cloudflared` on the host (not in a container — it needs the host's
port 22), add a public hostname routed to `ssh://localhost:22`, and put a
Cloudflare Access policy in front of it so it is not exposed to the world. Then:

```bash
ssh -o ProxyCommand="cloudflared access ssh --hostname ssh.317atc.co.uk" server317@localhost
```

`CLOUDFLARE_TUNNEL_TOKEN` is not used by the app or the cluster. It is a leftover. The tunnel belongs on the host as a systemd
service, not in the stack, so that a broken deploy cannot take your recovery
path down with it.

## 3. Keep a break-glass channel (optional)

Both paths above are inbound. If you want something that works even when every
inbound route is dead, a cron on the box that polls a private repo every few
minutes and runs a `recovery.sh` if present is fully outbound and cannot be
locked out.

Be deliberate about this one: it is effectively remote code execution on the
server for anyone with write access to that repo. Use a dedicated private repo
with tight permissions, or skip it.

## Day-to-day rules

- Never run `tailscale login`/`logout`/`down` from a bare SSH session. The guard
  now blocks it, but the habit matters more than the guard.
- Prefer `tailscale up --auth-key=...` over the interactive browser flow — it
  cannot be orphaned by a dropped session.
- Do long or risky host work inside `tmux` so a dropped connection never strands
  a half-finished command.
- Disable key expiry on the server node in the admin console.

