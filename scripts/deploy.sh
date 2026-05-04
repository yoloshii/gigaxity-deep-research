#!/bin/bash
# Deploy research tool to VPS
# Usage: ./scripts/deploy.sh <vps-ip> [user] [remote-dir]

set -e

VPS_IP="${1:?Error: VPS IP required. Usage: ./scripts/deploy.sh <vps-ip> [user] [remote-dir]}"
VPS_USER="${2:-root}"
REMOTE_DIR="${3:-/opt/research-tool}"

echo "Deploying to $VPS_USER@$VPS_IP:$REMOTE_DIR"

# Sync files (excluding dev/local files)
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.venv' \
    --exclude '.pytest_cache' \
    --exclude 'tests' \
    --exclude '.env' \
    --exclude '*.pyc' \
    . "$VPS_USER@$VPS_IP:$REMOTE_DIR/"

# Build and start on VPS
ssh "$VPS_USER@$VPS_IP" "cd $REMOTE_DIR && docker compose up -d --build"

echo ""
echo "=== Deployment Complete ==="
echo "API:  http://$VPS_IP:8000"
echo "Docs: http://$VPS_IP:8000/docs"
echo "Health: http://$VPS_IP:8000/api/v1/health"
