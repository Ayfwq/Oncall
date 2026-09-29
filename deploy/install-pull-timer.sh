#!/usr/bin/env bash
# Install a server-side timer that checks GitHub for updates every five minutes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root (sudo bash deploy/install-pull-timer.sh)." >&2
  exit 1
fi
if [ ! -d "$root/.git" ] || [ ! -f "$root/.env" ]; then
  echo "Run from a Git checkout with a server .env file." >&2
  exit 1
fi

cat > /etc/systemd/system/oncall-pull.service <<EOF
[Unit]
Description=Pull and deploy Oncall from GitHub
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=/usr/bin/bash $root/deploy/pull.sh
TimeoutStartSec=30min
EOF

cat > /etc/systemd/system/oncall-pull.timer <<'EOF'
[Unit]
Description=Check GitHub for Oncall updates

[Timer]
OnBootSec=2min
OnUnitInactiveSec=5min
Persistent=true
Unit=oncall-pull.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now oncall-pull.timer
echo "Installed oncall-pull.timer for $root"
echo "Run once now: systemctl start oncall-pull.service"
