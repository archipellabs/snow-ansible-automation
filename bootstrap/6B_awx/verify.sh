#!/usr/bin/env bash
# Quick smoke test — run ON the VM (called at the end of install.sh, re-runnable any time).
# AWX (pods/ingress/API/password) + the in-cluster registry + the eda-server namespace.
#   FQDN=<fqdn> ~/6B_awx/verify.sh
set -uo pipefail
FQDN="${FQDN:-${1:-}}"
[ -n "$FQDN" ] || { echo "set FQDN (env var or arg1)" >&2; exit 1; }
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

echo "== AWX pods =="
kubectl -n awx get pods 2>&1
echo "== AWX ingress =="
kubectl -n awx get ingress 2>&1

PW="$(kubectl -n awx get secret awx-admin-password -o jsonpath='{.data.password}' 2>/dev/null | base64 -d)"
PING="$(curl -sk --max-time 20 https://localhost/api/v2/ping/ -H "Host: ${FQDN}" 2>/dev/null)"
echo "== AWX api ping (via Traefik) =="
echo "  ${PING:-<no response>}"

echo "== registry (registry:2) =="
curl -s --max-time 10 http://localhost:30500/v2/ -o /dev/null -w "  registry http %{http_code} (200 = up)\n" 2>&1 || echo "  registry: no response"

echo "== eda-server pods =="
kubectl -n eda get pods 2>&1
echo "== eda-server api (NodePort 31080) =="
curl -s --max-time 10 http://localhost:31080/_healthz -o /dev/null -w "  eda-server /_healthz http %{http_code} (200 = up)\n" 2>&1 || echo "  eda-server: no response"

case "$PING" in
  *'"version"'*) STATUS="✅ AWX is UP" ;;
  *)             STATUS="⚠  AWX not answering yet — re-check: kubectl -n awx get pods" ;;
esac

cat <<EOF

================= ${STATUS} =================
URL:   https://${FQDN}/        (Traefik self-signed cert)
user:  admin
pass:  ${PW:-<not ready>}
==================================================
EOF
