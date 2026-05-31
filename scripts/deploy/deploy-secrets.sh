#!/bin/bash
# =============================================================================
# RentaBot — deploy secrets to GCP VM
# Run from your LOCAL Windows PC (Git Bash / WSL / PowerShell with OpenSSH).
#
# Usage:
#   bash scripts/deploy/deploy-secrets.sh <VM_EXTERNAL_IP>
#
# Example:
#   bash scripts/deploy/deploy-secrets.sh 34.123.45.67
#
# Prerequisites:
#   - SSH key already configured for the VM (GCP sets this up automatically
#     when you create the VM via Console)
#   - .env file exists in project root
#   - credentials/ folder exists in project root
# =============================================================================

set -e

VM_IP="${1:?Usage: deploy-secrets.sh <VM_EXTERNAL_IP>}"
REMOTE_USER="${REMOTE_USER:-tang_zhekhee}"  # GCP VM username
REMOTE_DIR="~/rentabot"

echo "Deploying secrets to $REMOTE_USER@$VM_IP:$REMOTE_DIR"
echo ""

# Copy .env
echo "[1/2] Copying .env..."
scp .env "$REMOTE_USER@$VM_IP:$REMOTE_DIR/.env"

# Copy service account credentials
echo "[2/2] Copying credentials/..."
scp -r credentials/ "$REMOTE_USER@$VM_IP:$REMOTE_DIR/credentials/"

echo ""
echo "Done. Restart the bot on the VM:"
echo "  ssh $REMOTE_USER@$VM_IP 'sudo systemctl restart rentabot'"
echo ""
echo "Check it's running:"
echo "  ssh $REMOTE_USER@$VM_IP 'sudo systemctl status rentabot'"
