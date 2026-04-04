#!/bin/bash
# Deploy NEROCLAUDE on a fresh Ubuntu/Debian VPS
# Usage: ssh into your server, clone your repo, then run:
#   cd NEROCLAUDE/backend && bash deploy.sh

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="neroclaude"
PYTHON_VERSION="3.13"

echo "=== NEROCLAUDE Deploy ==="

# 1. Install Python + venv
echo "[1/5] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq python${PYTHON_VERSION} python${PYTHON_VERSION}-venv git

# 2. Create venv & install deps
echo "[2/5] Setting up Python environment..."
cd "$APP_DIR"
python${PYTHON_VERSION} -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 3. Check .env exists
if [ ! -f .env ]; then
    echo "ERROR: Copy .env.example to .env and fill in your keys first!"
    echo "  cp .env.example .env && nano .env"
    exit 1
fi

# 4. Install systemd service
echo "[3/5] Installing systemd services..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=NEROCLAUDE Polymarket Trading Bot
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python bot.py
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

# Logging
StandardOutput=append:${APP_DIR}/bot.log
StandardError=append:${APP_DIR}/bot.log

# Safety limits
MemoryMax=512M
CPUQuota=50%

[Install]
WantedBy=multi-user.target
EOF

# API server (for Vercel dashboard)
API_PORT=$(grep -oP 'API_PORT=\K[0-9]+' .env 2>/dev/null || echo 8080)
sudo tee /etc/systemd/system/${SERVICE_NAME}-api.service > /dev/null <<EOF
[Unit]
Description=NEROCLAUDE API Server
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/uvicorn api_server:app --host 0.0.0.0 --port ${API_PORT}
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

StandardOutput=append:${APP_DIR}/api.log
StandardError=append:${APP_DIR}/api.log

MemoryMax=256M
CPUQuota=25%

[Install]
WantedBy=multi-user.target
EOF

# 5. Enable & start
echo "[4/5] Starting services..."
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME} ${SERVICE_NAME}-api
sudo systemctl start ${SERVICE_NAME}
sudo systemctl start ${SERVICE_NAME}-api

echo "[5/5] Done! Bot + API are running."
echo ""
echo "=== Useful commands ==="
echo "  Bot status:  sudo systemctl status ${SERVICE_NAME}"
echo "  API status:  sudo systemctl status ${SERVICE_NAME}-api"
echo "  Bot logs:    tail -f ${APP_DIR}/bot.log"
echo "  API logs:    tail -f ${APP_DIR}/api.log"
echo "  Stop bot:    sudo systemctl stop ${SERVICE_NAME}"
echo "  Stop API:    sudo systemctl stop ${SERVICE_NAME}-api"
echo "  Restart:     sudo systemctl restart ${SERVICE_NAME} ${SERVICE_NAME}-api"
echo ""
echo "=== Oracle Cloud Firewall ==="
echo "  Make sure port ${API_PORT} is open in your VCN security list!"
echo "  sudo iptables -I INPUT -p tcp --dport ${API_PORT} -j ACCEPT"
echo ""
