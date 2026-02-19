#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  DevOps Assessment — Cluster Bootstrap Script (HIGH PERFORMANCE)
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
command -v k3d     >/dev/null 2>&1 || die "k3d not found. Follow README.md."
command -v kubectl >/dev/null 2>&1 || die "kubectl not found. Follow README.md."
command -v docker  >/dev/null 2>&1 || die "docker not found."

info "All prerequisites found."

# ── Create k3d cluster ────────────────────────────────────────────────────────
if k3d cluster list | grep -q "^${CLUSTER_NAME}"; then
  warn "Cluster '${CLUSTER_NAME}' exists — skipping creation."
else
  info "Creating k3d cluster '${CLUSTER_NAME}'..."
  k3d cluster create "${CLUSTER_NAME}" \
    --port "80:80@loadbalancer" \
    --port "443:443@loadbalancer" \
    --agents 3 \
    --registry-create "${REGISTRY_NAME}:${REGISTRY_PORT}"
  success "Cluster created."
fi

kubectl config use-context "k3d-${CLUSTER_NAME}"

# ── Build & push Docker image ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info "Building Python app image..."
docker build -t "assessment/app-python:latest" "${SCRIPT_DIR}/app-python/"
k3d image import "assessment/app-python:latest" --cluster "${CLUSTER_NAME}"
success "Image imported."

# ── Apply manifests ───────────────────────────────────────────────────────────
info "Applying Kubernetes manifests..."

kubectl apply -f "${SCRIPT_DIR}/k8s/base/namespace.yaml"
kubectl apply -f "${SCRIPT_DIR}/k8s/mongodb/"
kubectl apply -f "${SCRIPT_DIR}/k8s/redis/"
kubectl apply -f "${SCRIPT_DIR}/k8s/app/"

success "Manifests applied."

# ── Wait for pods ─────────────────────────────────────────────────────────────
info "Waiting for MongoDB..."
kubectl rollout status deployment/mongo -n "${NAMESPACE}" --timeout=180s

info "Waiting for Redis..."
kubectl rollout status deployment/redis -n "${NAMESPACE}" --timeout=120s

info "Waiting for Python app..."
kubectl rollout status deployment/app-python -n "${NAMESPACE}" --timeout=180s

success "All deployments ready!"

# ── Apply HPA ─────────────────────────────────────────────────────────────────
info "Applying HPA..."
kubectl apply -f "${SCRIPT_DIR}/k8s/app/hpa.yaml" || warn "HPA not applied"

# ── Print instructions ────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Assessment Environment Ready!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Add to /etc/hosts:"
echo -e "    ${YELLOW}127.0.0.1  assessment.local${NC}"
echo ""
echo "  Endpoints:"
echo "    Health       : http://assessment.local/healthz"
echo "    Readiness    : http://assessment.local/readyz"
echo "    API          : http://assessment.local/api/data"
echo "    Stats        : http://assessment.local/api/stats"
echo "    Cache Status : http://assessment.local/api/cache/status"
echo ""
echo "  Stress test:"
echo "    k6 run stress-test/stress-test.js"
echo ""
echo "  Monitor:"
echo "    kubectl get pods -n ${NAMESPACE} -w"
echo "    kubectl top pods -n ${NAMESPACE}"
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
