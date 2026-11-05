#!/usr/bin/env bash
# EC2 Ubuntu 24.04 cloud-init user data. Contains no credentials.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io python3-venv git tmux
systemctl enable --now docker
usermod -aG docker ubuntu
python3 -m venv /opt/agentrec-tools
/opt/agentrec-tools/bin/pip install 'uv==0.11.6'
ln -sf /opt/agentrec-tools/bin/uv /usr/local/bin/uv
install -d -o ubuntu -g ubuntu /home/ubuntu/agent-recovery
printf 'Bootstrap complete\n' > /var/lib/agent-recovery-bootstrap-complete
