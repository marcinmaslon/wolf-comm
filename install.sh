#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run this script as root or with sudo."
  exit 1
fi

WORKDIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${1:-${WORKDIR}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found at ${PYTHON_BIN}. Create or activate your virtual environment first."
  exit 1
fi
USER="${SUDO_USER:-root}"
GROUP="$(id -gn "$USER" 2>/dev/null || echo "$USER")"

SERVICE_PATH="/etc/systemd/system/wolf.service"
RESTART_SERVICE_PATH="/etc/systemd/system/wolf-restart.service"
TIMER_PATH="/etc/systemd/system/wolf-restart.timer"

cat <<EOF >"$SERVICE_PATH"
[Unit]
Description=Wolf SmartSet monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
Group=${GROUP}
WorkingDirectory=${WORKDIR}
Environment="PATH=${WORKDIR}/.venv/bin"
ExecStart=${PYTHON_BIN} wolf.py --refresh_interval 60
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF >"$RESTART_SERVICE_PATH"
[Unit]
Description=Restart wolf.service every 12 hours

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart wolf.service
EOF

cat <<EOF >"$TIMER_PATH"
[Unit]
Description=Timer to restart wolf.service every 12 hours

[Timer]
OnBootSec=5min
OnUnitActiveSec=12h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now wolf.service
systemctl enable --now wolf-restart.timer

echo "Daemon installed. Check 'systemctl status wolf.service' for runtime info."
