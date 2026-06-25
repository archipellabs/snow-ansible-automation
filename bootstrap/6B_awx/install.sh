#!/usr/bin/env bash
# Run ON the Ubuntu VM. Chantier A: k3s + registry:2 + AWX (awx-operator) + eda-server (eda-server-operator).
# Idempotent — re-runnable. All manifests live in ./k8s/ (synced with this folder by sync.sh).
#   FQDN=<fqdn> ~/6B_awx/install.sh        (sync.sh passes FQDN for you)
set -euo pipefail

FQDN="${FQDN:-${1:-}}"
[ -n "$FQDN" ] || { echo "set FQDN (env var or arg1), e.g. archipellabs-awx.<region>.cloudapp.azure.com" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
K8S="$HERE/k8s"

# 1) k3s — single node (containerd + Traefik ingress controller). World-readable kubeconfig.
if ! command -v k3s >/dev/null 2>&1; then
  echo ">> installing k3s…"
  curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
fi
# containerd must pull the in-cluster registry over http (NodePort). Only restart k3s if it changed.
if ! sudo cmp -s "$K8S/registries.yaml" /etc/rancher/k3s/registries.yaml 2>/dev/null; then
  echo ">> applying k3s registries.yaml + restarting k3s…"
  sudo install -m 0644 "$K8S/registries.yaml" /etc/rancher/k3s/registries.yaml
  sudo systemctl restart k3s
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
mkdir -p "$HOME/.kube" && cp -f /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
for i in $(seq 1 30); do kubectl get --raw=/readyz >/dev/null 2>&1 && break; sleep 4; done
kubectl get nodes

# 2) in-cluster registry (registry:2) for the DE image.
echo ">> deploying registry:2…"
kubectl apply -f "$K8S/registry.yaml"

# 3) awx-operator + AWX instance (Traefik ingress on the FQDN).
echo ">> deploying awx-operator + AWX…"
kubectl apply -k "$K8S/awx-operator"
kubectl -n awx rollout status deploy/awx-operator-controller-manager --timeout=600s
sed "s/__FQDN__/${FQDN}/g" "$K8S/awx.yaml" | kubectl apply -f -

# 4) eda-server-operator + EDA instance (eda-server drives AWX via automation_server_url).
echo ">> deploying eda-server-operator + eda-server…"
kubectl apply -k "$K8S/eda-operator"
kubectl -n eda rollout status deploy/eda-server-operator-controller-manager --timeout=600s || true
kubectl apply -f "$K8S/eda.yaml"

# 5) wait for AWX to come up (the operator reconciles it async), then smoke-test everything.
echo ">> waiting for AWX (first run pulls images + migrates — a few minutes)…"
for i in $(seq 1 40); do kubectl -n awx get deploy/awx-web >/dev/null 2>&1 && break; sleep 10; done
kubectl -n awx rollout status deploy/awx-task --timeout=600s || true
kubectl -n awx rollout status deploy/awx-web  --timeout=600s || true

FQDN="${FQDN}" bash "$HERE/verify.sh" || true
echo ">> eda-server reconciles async (api + workers + postgres/redis) — re-run verify.sh in a few min."
echo ">> Next (phase 1): lib/awx.py + controller config-as-code — see bootstrap/6B_awx/README.md."
