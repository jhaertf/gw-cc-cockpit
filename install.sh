#!/usr/bin/env bash
# cc-cockpit installer — Copyright (C) 2026 GuniWeb moderne Medien GmbH — AGPL-3.0-or-later
# Sets up cc-cockpit as an autostarting local service (launchd on macOS, systemd --user on Linux).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CONF="$HOME/.config/cc-cockpit"
SHARE="$HOME/.local/share/cc-cockpit"
PORT="${CC_PORT:-8910}"
PY="$(command -v python3 || true)"

[ -n "$PY" ] || { echo "python3 not found in PATH." >&2; exit 1; }
command -v claude >/dev/null || echo "WARN: 'claude' not in PATH — the local source will be empty until Claude Code is installed."

echo "→ Creating directories"
mkdir -p "$CONF" "$SHARE/web" "$SHARE/data" "$SHARE/run"

echo "→ Installing files"
cp "$SRC/server.py" "$SRC/enrich.py" "$SHARE/"
cp "$SRC/web/index.html" "$SHARE/web/"

if [ ! -f "$CONF/hosts.conf" ]; then
  cp "$SRC/hosts.conf.example" "$CONF/hosts.conf"
  echo "  hosts.conf created (local sessions enabled)"
fi

if [ ! -f "$CONF/id_cockpit" ]; then
  echo "→ Generating dedicated SSH key (used only to read remote sessions)"
  ssh-keygen -t ed25519 -N "" -C "cc-cockpit" -f "$CONF/id_cockpit" >/dev/null
  echo "  key: $CONF/id_cockpit"
fi

OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.cc-cockpit.server.plist"
  sed -e "s#__PYTHON__#$PY#g" -e "s#__SERVER__#$SHARE/server.py#g" \
      -e "s#__LOG__#$SHARE/server.log#g" -e "s#__PORT__#$PORT#g" \
      "$SRC/dist/com.cc-cockpit.server.plist.template" > "$PLIST"
  launchctl bootout "gui/$(id -u)/com.cc-cockpit.server" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "→ launchd service started (autostarts at login)"
else
  UNIT="$HOME/.config/systemd/user/cc-cockpit.service"
  mkdir -p "$(dirname "$UNIT")"
  sed -e "s#__PYTHON__#$PY#g" -e "s#__SERVER__#$SHARE/server.py#g" -e "s#__PORT__#$PORT#g" \
      "$SRC/dist/cc-cockpit.service.template" > "$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now cc-cockpit.service
  echo "→ systemd --user service started (run 'loginctl enable-linger \$USER' to keep it running after logout)"
fi

echo
echo "✓ Done.  Dashboard:  http://127.0.0.1:$PORT"
echo "  Add a remote host:  $SRC/scripts/add-host.sh <label> <user@host>"
