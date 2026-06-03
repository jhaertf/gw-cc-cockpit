#!/usr/bin/env bash
# cc-cockpit add-host — Copyright (C) 2026 GuniWeb moderne Medien GmbH — AGPL-3.0-or-later
# Onboards a remote machine running Claude Code:
#   1) authorizes the dedicated cc-cockpit key on it (uses your existing SSH access)
#   2) verifies `claude agents --json` works there
#   3) adds it to hosts.conf and reloads the service
#
# Usage:  ./scripts/add-host.sh <label> <user@host>
set -euo pipefail

LABEL="${1:-}"; TARGET="${2:-}"
if [ -z "$LABEL" ] || [ -z "$TARGET" ]; then
  echo "Usage: $0 <label> <user@host>"; exit 2
fi

CONF="$HOME/.config/cc-cockpit"
KEY="$CONF/id_cockpit"
HOSTS="$CONF/hosts.conf"
[ -f "$KEY" ] || { echo "No key at $KEY — run install.sh first." >&2; exit 1; }

echo "→ Authorizing the cc-cockpit key on $TARGET (uses your current SSH access)…"
ssh "$TARGET" 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys' < "$KEY.pub"

echo "→ Verifying Claude Code on $TARGET…"
OUT="$(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=6 \
       -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$CONF/known_hosts" \
       "$TARGET" 'bash -lc "claude agents --json"' 2>&1 || true)"
case "$OUT" in
  \[*) echo "  ok — Claude Code responds." ;;
  *) echo "⚠ Unexpected response from $TARGET:"; printf '%s\n' "$OUT" | head -3
     echo "  (Is Claude Code installed and on the login PATH there?)"; exit 1 ;;
esac

if grep -qE "^[^|#]*\|ssh\|$TARGET$" "$HOSTS" 2>/dev/null; then
  echo "→ $TARGET already in hosts.conf — skipping."
else
  printf '%s|ssh|%s\n' "$LABEL" "$TARGET" >> "$HOSTS"
  echo "→ Added: $LABEL|ssh|$TARGET"
fi

echo "→ Reloading service…"
if [ "$(uname -s)" = "Darwin" ]; then
  launchctl kickstart -k "gui/$(id -u)/com.cc-cockpit.server" 2>/dev/null || echo "  (please restart the service manually)"
else
  systemctl --user restart cc-cockpit.service 2>/dev/null || echo "  (please restart the service manually)"
fi

echo "✓ $LABEL ($TARGET) onboarded."
