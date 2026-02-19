#!/usr/bin/env bash
# cleanup.sh — tears down everything and resets for a fresh run

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }

CLUSTER_NAME="assessment"

info "Deleting k3d cluster..."
k3d cluster delete "${CLUSTER_NAME}" 2>/dev/null && success "Cluster deleted." || info "Cluster didn't exist, skipping."

info "Removing Docker images..."
docker rmi assessment/app-python:latest 2>/dev/null && success "Python image removed." || info "Python image not found, skipping."

info "Removing hosts entry..."
sudo sed -i '/assessment.local/d' /etc/hosts 2>/dev/null && success "Hosts entry removed." || info "Hosts entry not found, skipping."

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Clean! Run ./setup.sh to start fresh.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
