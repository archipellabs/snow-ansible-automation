#!/usr/bin/env bash
# Laptop-side installer for the Meridian simulator (mirrors bootstrap/aap/sync.sh): copy the SSH
# public key the fleet trusts, push simulator/ + the repo-root .env to the VM, then run deploy.sh
# there. One command instead of a manual rsync + ssh.
#
#   ./simulator/sync.sh           # sync + build + start the stack on the VM
#   ./simulator/sync.sh --sync    # sync only (skip the remote deploy)
#
# FQDN comes from the env or the repo-root .env. SSH key: ~/.ssh/snow-aap-poc.
set -euo pipefail
cd "$(dirname "$0")"
FQDN="${FQDN:-$(grep -E '^FQDN=' ../.env 2>/dev/null | head -1 | cut -d= -f2-)}"
[ -n "$FQDN" ] || { echo "FQDN not set (env var or repo-root .env)" >&2; exit 1; }
KEY="${HOME}/.ssh/snow-aap-poc"
SSH="ssh -i ${KEY} -o StrictHostKeyChecking=accept-new"

# The fleet trusts the controller's Target SSH key; deploy.sh needs its public half here.
if [ ! -f base/authorized_keys ] && [ -f ../bootstrap/targets/keys/target_key.pub ]; then
  cp ../bootstrap/targets/keys/target_key.pub base/authorized_keys
  echo "= copied target_key.pub -> base/authorized_keys"
fi
[ -f base/authorized_keys ] || { echo "missing base/authorized_keys (cp bootstrap/targets/keys/target_key.pub there)" >&2; exit 1; }

# Push the simulator tree (not its caches / any stray .env), then the single repo-root .env.
rsync -avz -e "$SSH" --exclude '__pycache__' --exclude '.env' ./ "azureuser@${FQDN}:~/simulator/"
[ -f ../.env ] && rsync -avz -e "$SSH" ../.env "azureuser@${FQDN}:~/simulator/.env"
echo ">> Synced simulator/ + .env to azureuser@${FQDN}:~/simulator/"

if [ "${1:-}" = "--sync" ]; then
  echo ">> Sync only. Deploy with: ssh -i ${KEY} azureuser@${FQDN} '~/simulator/deploy.sh'"
  exit 0
fi

echo ">> Building + starting the stack on the VM…"
$SSH "azureuser@${FQDN}" '~/simulator/deploy.sh'
echo ">> Next: python3 bootstrap/keycloak/configure.py   (build the 'meridian' realm from fleet.yml)"
