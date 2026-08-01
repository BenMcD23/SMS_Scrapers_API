#!/usr/bin/env bash
# Installed as /usr/local/bin/tailscale, which shadows the real binary at
# /usr/bin/tailscale for both plain and sudo invocations (sudo's default
# secure_path puts /usr/local/bin ahead of /usr/bin).
#
# Blocks the specific footgun that caused the lockout: running a subcommand that
# can drop the node off the tailnet from an interactive SSH session whose own
# connectivity depends on that node. Inside tmux the command survives the
# session dying, so you can reconnect and finish the login.
#
# This is a speed bump, not a wall — it only triggers for interactive SSH
# sessions, so scripts, systemd units and CI are unaffected.

set -uo pipefail

REAL=/usr/bin/tailscale

case "${1:-}" in
  login | logout | down | up | set)
    if [[ -n "${SSH_CONNECTION:-}" && -z "${TMUX:-}" && -t 0 ]]; then
      cat >&2 <<EOF
Refusing to run 'tailscale ${1}' from a bare SSH session.

This can drop the node off the tailnet and kill the connection you are using,
leaving no way back in. Run it inside tmux so it survives a dropped session:

    tmux new -s ts
    sudo tailscale ${*}

Then if the connection drops, reconnect and 'tmux attach -t ts' to finish.

To re-authenticate without a browser at all, prefer an auth key:

    sudo tailscale up --auth-key=tskey-auth-xxxxx --ssh

Override this guard for one command with: sudo $REAL ${*}
EOF
      exit 1
    fi
    ;;
esac

exec "$REAL" "$@"
