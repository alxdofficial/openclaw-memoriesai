#!/usr/bin/env bash
set -euo pipefail

echo "Stopping and removing detm-daemon service..."
sudo systemctl stop detm-daemon 2>/dev/null || true
sudo systemctl disable detm-daemon 2>/dev/null || true
sudo rm -f /etc/systemd/system/detm-daemon.service
sudo systemctl daemon-reload

echo "Removing data directory..."
rm -rf "$HOME/.agentic-computer-use"

echo "Done. To fully remove, also delete this repo and the mcporter config entry."
