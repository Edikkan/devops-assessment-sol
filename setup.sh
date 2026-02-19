#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  DevOps Assessment — Cluster Bootstrap Script (OPTIMIZED)
#  Run this ONCE after installing k3d and kubectl.
#
#  What it does:
#    1. Creates a k3d cluster with a local image registry
#    2. Builds the optimized Python app image
#    3. Imports images into the cluster
#    4. Applies all Kubernetes manifests in order
#    5. Waits for all pods to be ready
#    6. Prints access instructions
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

CLUSTER_NAME="assessment"
REGISTRY_NAME="registry.localhost"
REGISTRY_PORT="5000"
NAMESPACE="assessment"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────
command -v k3d     >/dev/null 2>&1 || die "k3d not found. Follow the install instructions in README.md."
command -v kubectl >/dev/null 2>&1 || die "kubectl not found. Follow the install instructions in README.md."
command -v docker  >/dev/null 2>&1 || die "docker not found. Docker must be running."

info "All prerequisites found."

# ── Create k3d cluster ────────────────────────────────────────────────────────
if k3d cluster list | grep -q "^${CLUSTER_NAME}"; then
  warn "Cluster '${CLUSTER_NAME}' already exists — skipping creation."
else
  info "Creating k3d cluster '${CLUSTER_NAME}'..."
  k3d cluster create "${CLUSTER_NAME}" \
    --port "80:80@loadbalancer" \
    --port "443:443@loadbalancer" \
    --agents 2 \
    --registry-create "${REGISTRY_NAME}:${REGISTRY_PORT}"
  success "Cluster created."
fi

# Set kubectl context
kubectl config use-context "k3d-${CLUSTER_NAME}"

# ── Build & push Docker images ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info "Building Python app image (optimized)..."
docker build -t "assessment/app-python:latest" "${SCRIPT_DIR}/app-python/"
k3d image import "assessment/app-python:latest" --cluster "${CLUSTER_NAME}"
success "Python image imported."

# ── Apply manifests ───────────────────────────────────────────────────────────
info "Applying Kubernetes manifests..."

kubectl apply -f "${SCRIPT_DIR}/k8s/base/namespace.yaml"
kubectl apply -f "${SCRIPT_DIR}/k8s/mongodb/"
kubectl apply -f "${SCRIPT_DIR}/k8s/redis/"
kubectl apply -f "${SCRIPT_DIR}/k8s/app/"

success "Manifests applied."

# ── Wait for pods ─────────────────────────────────────────────────────────────
info "Waiting for MongoDB to be ready (this may take ~60 s)..."
kubectl rollout status deployment/mongo -n "${NAMESPACE}" --timeout=180s

info "Waiting for Redis to be ready..."
kubectl rollout status deployment/redis -n "${NAMESPACE}" --timeout=120s

info "Waiting for Python app to be ready..."
kubectl rollout status deployment/app-python -n "${NAMESPACE}" --timeout=120s

success "All deployments are ready!"

# ── Apply HPA after metrics-server is available ───────────────────────────────
info "Applying HorizontalPodAutoscaler..."
kubectl apply -f "${SCRIPT_DIR}/k8s/app/hpa.yaml" || warn "HPA not applied (metrics-server may not be installed)"

success "All deployments are ready!"

# ── Print access instructions ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Assessment Environment Ready!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Add this to /etc/hosts (or C:\\Windows\\System32\\drivers\\etc\\hosts):"
echo ""
echo -e "    ${YELLOW}127.0.0.1  assessment.local${NC}"
echo ""
echo "  Endpoints:"
echo "    Health     : http://assessment.local/healthz"
echo "    Readiness  : http://assessment.local/readyz"
echo "    API        : http://assessment.local/api/data"
echo "    Stats      : http://assessment.local/api/stats"
echo "    Cache Status: http://assessment.local/api/cache/status"
echo ""
echo "  To run the stress test:"
echo "    k6 run stress-test/stress-test.js"
echo ""
echo "  Useful commands:"
echo "    kubectl get pods -n ${NAMESPACE}"
echo "    kubectl top pods -n ${NAMESPACE}"
echo "    kubectl logs -n ${NAMESPACE} deploy/app-python -f"
echo "    kubectl logs -n ${NAMESPACE} deploy/redis -f"
echo "    kubectl logs -n ${NAMESPACE} deploy/mongo -f"
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
