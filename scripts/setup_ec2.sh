#!/bin/bash
# ==============================================================================
# 🚀 1-Click AWS EC2 Setup Script for NIFTY Trading Engine
# Installs Docker, Docker Compose, Git, and configures user permissions.
# ==============================================================================
set -e

echo ">>> Updating packages..."
sudo apt-get update -y || sudo yum update -y

echo ">>> Installing Docker & Git..."
if command -v apt-get &> /dev/null; then
    sudo apt-get install -y curl git docker.io docker-compose-v2
else
    sudo yum install -y curl git docker
    sudo systemctl start docker
    sudo systemctl enable docker
fi

echo ">>> Enabling Docker service..."
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo ">>> Verifying Docker installation..."
docker --version
docker compose version

echo "======================================================================"
echo "✅ AWS EC2 Docker environment ready!"
echo "⚠️ Please log out and log back in (or run 'newgrp docker') to apply"
echo "   docker permissions without sudo."
echo "======================================================================"
