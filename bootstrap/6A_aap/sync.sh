#!/usr/bin/env bash
# Push the bootstrap/6A_aap/ folder (install + config-as-code + the eda/ DE build) and the
# repo-root .env to the VM at ~/aap/. install.sh and ~/aap/eda/build.sh run there.
# Excludes binaries (tarball already on the VM) and the rendered inventory (secrets).
set -euo pipefail
cd "$(dirname "$0")"
AAP_FQDN="${AAP_FQDN:-$(grep -E '^AAP_FQDN=' ../../.env 2>/dev/null | head -1 | cut -d= -f2-)}"
[ -n "$AAP_FQDN" ] || { echo "AAP_FQDN not set (env var or repo-root .env)" >&2; exit 1; }
KEY="${HOME}/.ssh/snow-aap-poc"
SSH="ssh -i ${KEY} -o StrictHostKeyChecking=accept-new"

rsync -avz -e "$SSH" --exclude '*.tar.gz' --exclude 'inventory' \
  ./ "azureuser@${AAP_FQDN}:~/aap/"
# Secrets: push the single .env (install.sh parses it; never sourced).
[ -f ../../.env ] && rsync -avz -e "$SSH" ../../.env "azureuser@${AAP_FQDN}:~/aap/.env"

echo ">> Synced to ~/aap/ (incl. .env). Next: ssh … '~/aap/install.sh'"
