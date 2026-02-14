#!/usr/bin/env bash
set -euo pipefail

echo "Stopping and removing memoriesai-daemon service..."
sudo systemctl stop memoriesai-daemon 2>/dev/null || true
sudo systemctl disable memoriesai-daemon 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoriesai-daemon.service
sudo systemctl daemon-reload

echo "Removing data directory..."
rm -rf "$HOME/.openclaw-memoriesai"

echo "Done. To fully remove, also delete this repo and the mcporter config entry."
