#!/usr/bin/env bash
# Laptop-side installer for the Meridian target fleet (mirrors bootstrap/5A_aap/sync.sh): copy the SSH
# public key the fleet trusts into the simulator build context, push simulator/ + the repo-root .env
# + this deploy.sh to the VM, then run deploy.sh there. One command instead of a manual rsync + ssh.
#
#   ./bootstrap/2_fleet/sync.sh           # sync + build + start the stack on the VM
#   ./bootstrap/2_fleet/sync.sh --sync    # sync only (skip the remote deploy)
#
# The fleet *definition* lives in simulator/ (fleet.yml, apps/, base/, compose.yml); this folder holds
# the access key + the deploy scripts. FQDN comes from the env or the repo-root .env. SSH: ~/.ssh/snow-aap-poc.
set -euo pipefail
cd "$(dirname "$0")"                 # bootstrap/2_fleet/
SIM="../../simulator"
ENVFILE="../../.env"
FQDN="${FQDN:-$(grep -E '^FQDN=' "$ENVFILE" 2>/dev/null | head -1 | cut -d= -f2-)}"
[ -n "$FQDN" ] || { echo "FQDN not set (env var or repo-root .env)" >&2; exit 1; }
KEY="${HOME}/.ssh/snow-aap-poc"
SSH="ssh -i ${KEY} -o StrictHostKeyChecking=accept-new"

# The fleet trusts the controller's Target SSH key; the image build needs its public half.
if [ ! -f "$SIM/base/authorized_keys" ] && [ -f keys/target_key.pub ]; then
  cp keys/target_key.pub "$SIM/base/authorized_keys"
  echo "= copied target_key.pub -> simulator/base/authorized_keys"
fi
[ -f "$SIM/base/authorized_keys" ] || { echo "missing simulator/base/authorized_keys (cp bootstrap/2_fleet/keys/target_key.pub there)" >&2; exit 1; }

# Push the simulator tree (not its caches / any stray .env), the single repo-root .env, and deploy.sh.
rsync -avz -e "$SSH" --exclude '__pycache__' --exclude '.env' "$SIM/" "azureuser@${FQDN}:~/simulator/"
[ -f "$ENVFILE" ] && rsync -avz -e "$SSH" "$ENVFILE" "azureuser@${FQDN}:~/simulator/.env"
rsync -avz -e "$SSH" deploy.sh "azureuser@${FQDN}:~/simulator/deploy.sh"
echo ">> Synced simulator/ + .env + deploy.sh to azureuser@${FQDN}:~/simulator/"

if [ "${1:-}" = "--sync" ]; then
  echo ">> Sync only. Deploy with: ssh -i ${KEY} azureuser@${FQDN} '~/simulator/deploy.sh'"
  exit 0
fi

echo ">> Building + starting the stack on the VM…"
$SSH "azureuser@${FQDN}" '~/simulator/deploy.sh'
echo ">> Next: python3 bootstrap/3_keycloak/configure.py   (build the 'meridian' realm from fleet.yml)"
