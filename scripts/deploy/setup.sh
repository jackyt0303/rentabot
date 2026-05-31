#!/bin/bash
# =============================================================================
# RentaBot — GCP e2-micro setup script
# Run once on a fresh Debian/Ubuntu VM after SSH-ing in.
#
# Usage:
#   chmod +x setup.sh && sudo bash setup.sh
#
# What it does:
#   1. Updates the OS
#   2. Installs Python 3.11+ and git
#   3. Clones the repository
#   4. Creates a Python venv and installs dependencies
#   5. Creates the rentabot systemd service
#   6. Enables + starts the service
#
# After running this script you still need to:
#   - Copy .env to ~/rentabot/.env  (see deploy-secrets.sh)
#   - Copy credentials/ to ~/rentabot/credentials/
# =============================================================================

set -e  # Exit immediately on any error

# Detect real user — handles both:
#   sudo bash setup.sh  (typical GCP usage: whoami=root, SUDO_USER=tang_zhekhee)
#   bash setup.sh       (non-root: whoami=tang_zhekhee, SUDO_USER unset)
# Without this, $HOME=/root and whoami=root when run under sudo, which means
# the app installs to /root/rentabot and runs as root — breaking deploy-secrets.sh.
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
    SERVICE_USER="$SUDO_USER"
    APP_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    SERVICE_USER="$(whoami)"
    APP_HOME="$HOME"
fi

APP_DIR="$APP_HOME/rentabot"
REPO_URL="https://github.com/jackyt0303/rentabot.git"
PYTHON_BIN="$APP_DIR/venv/bin/python"
BOT_SCRIPT="$APP_DIR/bot.py"

echo "============================================================"
echo " RentaBot GCP e2-micro setup"
echo "============================================================"
echo " Service user : $SERVICE_USER"
echo " App directory: $APP_DIR"
echo "============================================================"

# ------------------------------------------------------------
# 1. System packages
# ------------------------------------------------------------
echo "[1/5] Updating system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv git curl

PYTHON_VERSION=$(python3 --version)
echo "      Python: $PYTHON_VERSION"

# ------------------------------------------------------------
# 2. Clone repository
# ------------------------------------------------------------
echo "[2/5] Cloning repository to $APP_DIR..."
if [ -d "$APP_DIR" ]; then
    echo "      Directory exists — pulling latest..."
    git -C "$APP_DIR" pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi

# ------------------------------------------------------------
# 3. Python virtual environment + dependencies
# ------------------------------------------------------------
echo "[3/5] Creating venv and installing dependencies..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "      Dependencies installed."

# ------------------------------------------------------------
# 4. Create systemd service file
# ------------------------------------------------------------
echo "[4/5] Writing systemd service file..."
SERVICE_FILE="/etc/systemd/system/rentabot.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=RentaBot Telegram Bot
Documentation=https://github.com/jackyt0303/rentabot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_BIN $BOT_SCRIPT
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rentabot

# Keep the process alive — restart up to 5 times within 60 seconds
# before systemd marks it as failed
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
EOF

echo "      Service file written to $SERVICE_FILE"

# ------------------------------------------------------------
# 5. Enable and start the service
# ------------------------------------------------------------
echo "[5/5] Enabling and starting rentabot service..."
sudo systemctl daemon-reload
sudo systemctl enable rentabot
sudo systemctl start rentabot

echo ""
echo "============================================================"
echo " Setup complete. IMPORTANT: copy your secrets first."
echo "============================================================"
echo ""
echo " Run on your LOCAL PC to copy secrets:"
echo "   bash scripts/deploy/deploy-secrets.sh YOUR_VM_IP"
echo ""
echo " Then restart the bot:"
echo "   sudo systemctl restart rentabot"
echo ""
echo " Check status:"
echo "   sudo systemctl status rentabot"
echo ""
echo " Live logs:"
echo "   journalctl -u rentabot -f"
echo ""
