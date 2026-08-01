#!/usr/bin/env bash
# One-shot installer for the Tailscale lockout protection. Run once on the
# server as root:
#
#   sudo ./ops/install-recovery.sh tskey-client-XXXXXXXXXXXX
#
# The argument is a Tailscale OAuth *client secret* (not a plain auth key).
# OAuth clients do not expire, so the watchdog keeps working indefinitely;
# reusable auth keys top out at 90 days and would silently rot.
#
# Create one at: https://login.tailscale.com/admin/settings/oauth
#   Scope: auth_keys (write)   Tags: tag:server

set -euo pipefail

CONF_DIR=/etc/tailscale-recovery
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS_TAGS="${TS_TAGS:-tag:server}"
TS_HOSTNAME="${TS_HOSTNAME:-$(hostname -s)}"

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root: sudo $0 <oauth-client-secret>" >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: sudo $0 <oauth-client-secret>" >&2
  exit 1
fi

secret="$1"
if [[ "$secret" != tskey-client-* ]]; then
  echo "Warning: expected an OAuth client secret starting 'tskey-client-'." >&2
  echo "A plain auth key will work but expires within 90 days." >&2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "==> Installing jq (required by the watchdog)"
  apt-get update -qq && apt-get install -y -qq jq
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "==> Installing tmux (required by the guard's suggested workflow)"
  apt-get install -y -qq tmux
fi

echo "==> Writing credential to $CONF_DIR/authkey"
install -d -m 700 "$CONF_DIR"
printf '%s' "$secret" > "$CONF_DIR/authkey"
chmod 600 "$CONF_DIR/authkey"

cat > "$CONF_DIR/config" <<EOF
# Consumed by tailscale-watchdog.sh
TS_HOSTNAME="$TS_HOSTNAME"
TS_TAGS="$TS_TAGS"
EOF
chmod 644 "$CONF_DIR/config"

echo "==> Installing watchdog to /usr/local/sbin/tailscale-watchdog.sh"
install -m 755 "$SRC_DIR/tailscale-watchdog.sh" /usr/local/sbin/tailscale-watchdog.sh

echo "==> Installing interactive guard to /usr/local/bin/tailscale"
if [[ ! -x /usr/bin/tailscale ]]; then
  echo "Expected the real tailscale binary at /usr/bin/tailscale; not found." >&2
  exit 1
fi
install -m 755 "$SRC_DIR/tailscale-guard.sh" /usr/local/bin/tailscale

echo "==> Installing systemd units"
install -m 644 "$SRC_DIR/tailscale-watchdog.service" /etc/systemd/system/
install -m 644 "$SRC_DIR/tailscale-watchdog.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tailscale-watchdog.timer

echo "==> Running the watchdog once to verify the credential works"
if /usr/local/sbin/tailscale-watchdog.sh; then
  echo "    ok"
else
  echo "    watchdog returned non-zero — check 'journalctl -t tailscale-watchdog'" >&2
fi

cat <<EOF

Installed. Remaining manual steps — the watchdog cannot do these for you:

  1. Tailscale ACLs must let you reach a *tagged* node. Tagged nodes are owned
     by the tailnet, not by you, so your existing user-owned grants do not
     apply. Add to your policy file:

       "tagOwners": { "tag:server": ["autogroup:admin"] },
       "grants": [
         { "src": ["autogroup:member"], "dst": ["tag:server"], "ip": ["*"] }
       ],
       "ssh": [
         { "action": "accept",
           "src":    ["autogroup:member"],
           "dst":    ["tag:server"],
           "users":  ["autogroup:nonroot", "$SUDO_USER"] }
       ]

  2. Set up a second, non-Tailscale way in (Cloudflare Tunnel) — see the
     "Never getting locked out again" section of the README. The watchdog
     protects against this failing accidentally; it does not help if the
     Tailscale control plane itself is the problem.

  3. Connect by MagicDNS name, not IP. Re-authenticating with a tag can change
     the node's 100.x address:

       ssh $SUDO_USER@$TS_HOSTNAME

Verify with:
  systemctl list-timers tailscale-watchdog.timer
  journalctl -t tailscale-watchdog -n 20
EOF
