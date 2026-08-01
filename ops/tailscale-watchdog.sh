#!/usr/bin/env bash
# Re-authenticates Tailscale automatically if the node ever drops off the
# tailnet — the thing that would have prevented the July 2026 lockout, where an
# interactive `tailscale login` was run over the very SSH session that depended
# on it and never completed.
#
# Runs from a systemd timer every 2 minutes. Idempotent and silent when the node
# is healthy; only acts when the backend is in a state that needs a login.
#
# Deliberately calls /usr/bin/tailscale directly so it bypasses the interactive
# guard wrapper installed at /usr/local/bin/tailscale.

set -uo pipefail

TS=/usr/bin/tailscale
CONF_DIR=/etc/tailscale-recovery
KEY_FILE="$CONF_DIR/authkey"
PAUSE_FILE="$CONF_DIR/paused"

log() { logger -t tailscale-watchdog "$*"; }

# Escape hatch: `touch /etc/tailscale-recovery/paused` to stop the watchdog
# fighting you during deliberate maintenance.
if [[ -e "$PAUSE_FILE" ]]; then
  exit 0
fi

# Defaults; override in $CONF_DIR/config
TS_HOSTNAME="$(hostname -s)"
TS_TAGS="tag:server"
# shellcheck source=/dev/null
[[ -r "$CONF_DIR/config" ]] && source "$CONF_DIR/config"

if [[ ! -r "$KEY_FILE" ]]; then
  log "no auth key at $KEY_FILE — cannot self-recover"
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  log "jq is not installed — cannot read backend state, refusing to act"
  exit 1
fi

raw=$("$TS" status --json 2>/dev/null)
state=$(printf '%s' "$raw" | jq -r '.BackendState // empty' 2>/dev/null)

# Only ever re-authenticate on states we positively recognise as needing it.
# Anything else — including an unreadable status, a failed jq parse, or a state
# string from a future Tailscale release — must be treated as "cannot tell" and
# leave the node alone. Re-authing on an unknown state would churn the node key
# every time the timer fires on a perfectly healthy machine.
case "$state" in
  NeedsLogin | NoState | Stopped)
    log "backend state is '$state' — attempting non-interactive re-auth"
    ;;
  Running)
    exit 0
    ;;
  Starting)
    # Mid-handshake; let the next tick decide rather than interrupting it.
    exit 0
    ;;
  NeedsMachineAuth)
    # Device approval is on and this node is queued for it. An auth key minted
    # with ?preauthorized=true avoids this, but if we land here a human must
    # approve the node in the admin console; re-auth will not clear it.
    log "backend state is 'NeedsMachineAuth' — needs manual approval in the admin console"
    exit 1
    ;;
  "")
    log "could not read backend state (is tailscaled running?) — taking no action"
    exit 1
    ;;
  *)
    log "unrecognised backend state '$state' — taking no action"
    exit 1
    ;;
esac

key=$(<"$KEY_FILE")

if "$TS" up \
      --auth-key="${key}?preauthorized=true" \
      --advertise-tags="$TS_TAGS" \
      --hostname="$TS_HOSTNAME" \
      --ssh \
      --accept-dns=false 2>&1 | logger -t tailscale-watchdog; then
  log "re-auth completed"
else
  log "re-auth FAILED — check that the OAuth client is still valid and owns $TS_TAGS"
  exit 1
fi
