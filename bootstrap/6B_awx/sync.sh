#!/usr/bin/env bash
# Laptop-side: push bootstrap/6B_awx/ + the repo-root .env to the AWX VM and run install.sh there
# (mirrors bootstrap/2_fleet/sync.sh). Host from $AWX_FQDN or the repo-root .env; SSH key ~/.ssh/snow-aap-poc.
#
# install.sh runs DETACHED (setsid + log): installing k3s resets the host network and can drop the SSH
# session mid-run, so we detach it and reconnect to follow ~/6B_awx/install.log until it finishes.
#   AWX_FQDN=archipellabs-awx.<region>.cloudapp.azure.com ./bootstrap/6B_awx/sync.sh
set -euo pipefail
cd "$(dirname "$0")"
ENVFILE="../../.env"
FQDN="${AWX_FQDN:-$(grep -E '^AWX_FQDN=' "$ENVFILE" 2>/dev/null | head -1 | cut -d= -f2-)}"
[ -n "$FQDN" ] || { echo "AWX_FQDN not set (env var or repo-root .env)" >&2; exit 1; }
KEY="${HOME}/.ssh/snow-aap-poc"
SSH="ssh -i ${KEY} -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=10"

rsync -avz -e "$SSH" --exclude '__pycache__' --exclude '.env' ./ "azureuser@${FQDN}:~/6B_awx/"
[ -f "$ENVFILE" ] && rsync -avz -e "$SSH" "$ENVFILE" "azureuser@${FQDN}:~/6B_awx/.env"

echo ">> starting install.sh (detached) on ${FQDN}…"
$SSH "azureuser@${FQDN}" "cd ~/6B_awx && FQDN='${FQDN}' setsid bash install.sh >install.log 2>&1 </dev/null & echo \$! >install.pid; echo 'install pid '\$(cat install.pid)"

echo ">> following install.log (reconnects through the k3s network reset; Ctrl-C is safe)…"
for a in 1 2 3 4 5 6; do
  $SSH "azureuser@${FQDN}" '
    cd ~/6B_awx 2>/dev/null || exit 1
    tail -f install.log & TP=$!
    while kill -0 "$(cat install.pid 2>/dev/null)" 2>/dev/null; do sleep 3; done
    sleep 2; kill "$TP" 2>/dev/null; echo "== install.sh exited =="
  ' && break
  echo "  …SSH dropped (k3s network reset?), reconnecting ($a)"
done
