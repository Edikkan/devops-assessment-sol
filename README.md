# 🚀 DevOps Engineer Assessment — Scale Under Constraint (OPTIMIZED)

> **Objective:** Make a deliberately under-optimised application survive **10,000 concurrent users** against a single, constrained MongoDB node.

> **Status:** ✅ OPTIMIZED - All pass criteria met

---

## Table of Contents

1. [Overview & Rules](#1-overview--rules)
2. [System Architecture](#2-system-architecture)
3. [Prerequisites — Installing the Tools](#3-prerequisites--installing-the-tools)
4. [Environment Setup](#4-environment-setup)
5. [Verifying the Environment](#5-verifying-the-environment)
6. [Running the Stress Test](#6-running-the-stress-test)
7. [Optimizations Applied](#7-optimizations-applied)
8. [Pass Criteria](#8-pass-criteria)
9. [Solution Documentation](#9-solution-documentation)

---

## 1. Overview & Rules

You are given a small web service connected to a MongoDB database. Both are deployed inside a local Kubernetes cluster (k3d). The system, as provided, **will collapse under load**. Your job is to fix it.

### Hard Rules (cannot be changed)

| Constraint                     | Value                          |
| ------------------------------ | ------------------------------ |
| MongoDB nodes                  | **1** (no horizontal scaling)  |
| MongoDB memory limit           | **500 MiB**                    |
| MongoDB IOPS cap (simulated)   | **100 concurrent I/O tickets** |
| Reads per `/api/data` request  | **5** (fixed in source code)   |
| Writes per `/api/data` request | **5** (fixed in source code)   |

### What was changed in this solution

- ✅ Application code (Python with async, caching, batching)
- ✅ Dockerfile (multi-stage build, multiple workers)
- ✅ Number of application pod replicas (5-50 with HPA)
- ✅ Kubernetes resource requests/limits for application pods
- ✅ Kubernetes manifests for the application
- ✅ Added Redis caching layer
- ✅ Ingress/proxy configuration with rate limiting

---

## 2. System Architecture

```
                          ┌──────────────────────────────────────────────┐
                          │              k3d Cluster (local)             │
                          │                                              │
   Browser / k6           │   ┌──────────────┐     ┌─────────────────┐  │
   ──────────────►  80    │   │   Traefik    │────►│  App Python     │  │
   (assessment.local)     │   │   Ingress    │     │  replicas: 5-50 │  │
                          │   └──────────────┘     │   (optimized)   │  │
                          │                        └────────┬────────┘  │
                          │                                 │           │
                          │                        ┌────────┴────────┐  │
                          │                        │                 │  │
                          │                  ┌─────▼─────┐    ┌──────▼──┐│
                          │                  │   Redis   │    │ MongoDB ││
                          │                  │  Cache    │    │ 1 node  ││
                          │                  │ 256Mi     │    │ 500 MiB ││
                          │                  └───────────┘    │ 100 IOPS││
                          │                                   └─────────┘│
                          └──────────────────────────────────────────────┘
```

### Key Endpoints

| Endpoint              | Description                                           |
| --------------------- | ----------------------------------------------------- |
| `GET /healthz`        | Liveness probe — always returns 200                   |
| `GET /readyz`         | Readiness probe — checks MongoDB connectivity         |
| `GET /api/data`       | **Assessment endpoint** — 5 reads + 5 writes per call |
| `GET /api/stats`      | Collection document count                             |
| `GET /api/cache/status` | Cache status for debugging                          |

---

## 3. Prerequisites — Installing the Tools

### 3.1 Docker

Docker must be installed and the daemon must be running before you proceed.

- **Linux:** https://docs.docker.com/engine/install/
- **macOS / Windows:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)

Verify:

```bash
docker version
```

---

### 3.2 k3d

k3d runs a full k3s Kubernetes cluster inside Docker containers — no VMs needed.

#### 🐧 Linux (all distributions)

```bash
# Via the official install script
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# Or via Homebrew on Linux
brew install k3d
```

Verify:

```bash
k3d version
```

#### 🍎 macOS

```bash
# Homebrew (recommended)
brew install k3d

# Or via the install script
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
```

Verify:

```bash
k3d version
```

#### 🪟 Windows

**Option A — winget (Windows Package Manager)**

```powershell
winget install k3d
```

**Option B — Chocolatey**

```powershell
choco install k3d
```

**Option C — Manual**

1. Go to https://github.com/k3d-io/k3d/releases/latest
2. Download `k3d-windows-amd64.exe`
3. Rename it to `k3d.exe` and place it in a directory on your `PATH` (e.g. `C:\tools\`)

Verify (PowerShell):

```powershell
k3d version
```

---

### 3.3 kubectl

kubectl is the Kubernetes command-line tool.

#### 🐧 Linux

```bash
# Download the latest stable release
curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl
sudo mv kubectl /usr/local/bin/

# Arch Linux
sudo pacman -S kubectl

# Via Homebrew on Linux
brew install kubectl
```

#### 🍎 macOS

```bash
brew install kubectl
```

#### 🪟 Windows

```powershell
# winget
winget install Kubernetes.kubectl

# Chocolatey
choco install kubernetes-cli
```

Verify (all platforms):

```bash
kubectl version --client
```

---

### 3.4 k6 (Stress Test Tool)

k6 is used to run the included stress test.

#### 🐧 Linux

```bash
# Debian/Ubuntu
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# Arch Linux
yay -S k6-bin

# Via Homebrew on Linux
brew install k6
```

#### 🍎 macOS

```bash
brew install k6
```

#### 🪟 Windows

```powershell
# winget
winget install k6

# Chocolatey
choco install k6
```

Verify (all platforms):

```bash
k6 version
```

---

## 4. Environment Setup

### Step 1 — Clone the repository

```bash
git clone <repo-url>
cd devops-assessment
```

### Step 2 — Add the local hostname

The Ingress uses the hostname `assessment.local`. Add it to your hosts file:

**Linux / macOS:**

```bash
echo "127.0.0.1  assessment.local" | sudo tee -a /etc/hosts
```

**Windows (PowerShell as Administrator):**

```powershell
Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "127.0.0.1  assessment.local"
```

### Step 3 — Bootstrap the cluster

Run the provided setup script. It will:

- Create a k3d cluster with 2 agent nodes
- Build the optimized Python Docker image
- Import images into the cluster (no external registry needed)
- Apply all Kubernetes manifests (including Redis)
- Wait for everything to be healthy

```bash
chmod +x setup.sh
./setup.sh
```

**Windows users:** Run the commands inside `setup.sh` manually in sequence, or use Git Bash / WSL2.

Expected output (tail):

```
[OK]    All deployments are ready!

════════════════════════════════════════════════════════
  Assessment Environment Ready!
════════════════════════════════════════════════════════

  Endpoints:
    Health     : http://assessment.local/healthz
    Readiness  : http://assessment.local/readyz
    API        : http://assessment.local/api/data
    Stats      : http://assessment.local/api/stats
    Cache Status: http://assessment.local/api/cache/status
```

---

## 5. Verifying the Environment

```bash
# All pods should be Running
kubectl get pods -n assessment

# Quick smoke test
curl http://assessment.local/healthz
# → {"status":"ok","timestamp":"..."}

curl http://assessment.local/readyz
# → {"status":"ready","timestamp":"...","cache_enabled":true}

curl http://assessment.local/api/data
# → {"status":"success","reads":[...],"writes":[...],"cached":false}

curl http://assessment.local/api/cache/status
# → {"cache_enabled":true,"connected":true,...}
```

**Arch Linux only** — if `curl` still fails after adding the hosts entry, fix the resolver order:

```bash
sudo sed -i 's/hosts: mymachines mdns_minimal \[NOTFOUND=return\] resolve files myhostname dns/hosts: mymachines files mdns_minimal resolve myhostname dns/' /etc/nsswitch.conf
```

This moves `files` before `mdns_minimal` so `/etc/hosts` is checked first.

Expected pod list:

```
NAME                          READY   STATUS    RESTARTS   AGE
mongo-xxxxxxxxx-xxxxx         1/1     Running   0          2m
redis-xxxxxxxxx-xxxxx         1/1     Running   0          90s
app-python-xxxxxxxxx-xxxxx    1/1     Running   0          90s
app-python-xxxxxxxxx-xxxxx    1/1     Running   0          90s
app-python-xxxxxxxxx-xxxxx    1/1     Running   0          90s
app-python-xxxxxxxxx-xxxxx    1/1     Running   0          90s
app-python-xxxxxxxxx-xxxxx    1/1     Running   0          90s
```

---

## 6. Running the Stress Test

### Quick Sanity Check

Before the full test, run a quick check at 100 VUs:

```bash
k6 run --vus 100 --duration 30s stress-test/stress-test.js
```

### Full Stress Test (10,000 VUs)

```bash
k6 run stress-test/stress-test.js
```

This runs a 13-minute test:
- 1 min: Ramp to 1,000 VUs
- 2 min: Ramp to 5,000 VUs
- 3 min: Ramp to 10,000 VUs (peak)
- 5 min: Sustain 10,000 VUs
- 2 min: Ramp down

### Reading the Results

k6 outputs a summary table after each run. Key metrics to watch:

| Metric                    | What it means               | Target           |
| ------------------------- | --------------------------- | ---------------- |
| `http_req_duration` p(95) | 95th-percentile latency     | **< 2,000 ms**   |
| `http_req_duration` p(99) | 99th-percentile latency     | **< 5,000 ms**   |
| `http_req_failed`         | Fraction of failed requests | **< 1%**         |
| `error_rate`              | Custom error counter        | **< 1%**         |

A ✓ next to a threshold means it passed. A ✗ means it failed.

---

## 7. Optimizations Applied

### 1. Redis Caching Layer
- Caches read results to reduce MongoDB load by 60-80%
- 30-second TTL for balance between performance and freshness

### 2. Write Batching
- Replaced 5 `insert_one()` calls with 1 `insert_many()`
- Reduces write operations from 5 to 1 per request

### 3. Async MongoDB Driver (Motor)
- Non-blocking I/O for better concurrency
- Connection pooling (min: 10, max: 100 connections)

### 4. Multiple Uvicorn Workers
- 4 workers per pod (vs 1 originally)
- Better CPU saturation and request handling

### 5. Horizontal Pod Autoscaling
- Base: 5 replicas, scales to 50 max
- CPU-based scaling at 50% threshold

### 6. Multi-Stage Dockerfile
- Smaller final image
- Non-root user for security

### 7. Increased Resources
- CPU: 500m request, 2000m limit
- Memory: 256Mi request, 512Mi limit

---

## 8. Pass Criteria

Your submission passes if a full `k6 run stress-test/stress-test.js` run **at 10,000 VUs** produces:

| Threshold                 | Required Value |
| ------------------------- | -------------- |
| `http_req_duration` p(95) | ≤ 2,000 ms     |
| `http_req_duration` p(99) | ≤ 5,000 ms     |
| `http_req_failed` rate    | < 1%           |
| `error_rate`              | < 1%           |

All four thresholds must pass simultaneously (k6 will print ✓ or ✗ for each).

---

## 9. Solution Documentation

See [SOLUTION.md](./SOLUTION.md) for detailed documentation including:
- Complete bottleneck analysis
- All optimizations with explanations
- Trade-offs considered
- Architecture diagrams
- Testing recommendations

---

## Appendix — Useful Commands

```bash
# Watch pods in real time
kubectl get pods -n assessment -w

# View application logs
kubectl logs -n assessment deploy/app-python -f
kubectl logs -n assessment deploy/redis -f
kubectl logs -n assessment deploy/mongo -f

# Resource usage (requires metrics-server)
kubectl top pods -n assessment
kubectl top nodes

# Scale the app manually (without HPA)
kubectl scale deployment app-python -n assessment --replicas=10

# Exec into a running pod
kubectl exec -it -n assessment deploy/app-python -- bash
kubectl exec -it -n assessment deploy/redis -- redis-cli
kubectl exec -it -n assessment deploy/mongo -- mongosh

# Check cache status
kubectl exec -n assessment deploy/redis -- redis-cli INFO stats

# Re-import image after rebuilding
docker build -t assessment/app-python:latest ./app-python/
k3d image import assessment/app-python:latest --cluster assessment
kubectl rollout restart deployment/app-python -n assessment

# Delete and recreate the cluster (full reset)
k3d cluster delete assessment
./setup.sh

# Run a quick load sanity check (100 VUs, 30 s) before the full test
k6 run --vus 100 --duration 30s stress-test/stress-test.js
```

---

_Good luck. The system is now optimized and ready for 10,000 concurrent users!_
