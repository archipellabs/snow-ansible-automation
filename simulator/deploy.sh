#!/usr/bin/env bash
# Build the Meridian Group fleet images and bring up the 9 containers (compose). Run ON the VM
# after syncing simulator/ there:  rsync -avz simulator/ azureuser@<FQDN>:~/simulator/  &&  ~/simulator/deploy.sh
#
# The fleet reuses the controller's Target SSH key, so the existing "Target SSH" credential logs
# into every server. Put its public half at base/authorized_keys before deploying:
#   cp bootstrap/targets/keys/target_key.pub simulator/base/authorized_keys
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"   # podman-compose installs here (pip --user)

[ -f base/authorized_keys ] || { echo "missing base/authorized_keys — cp the target_key.pub there" >&2; exit 1; }
command -v podman-compose >/dev/null || { echo "podman-compose missing — pip3 install --user podman-compose" >&2; exit 1; }

# Shared base first, then the infra (db/mail) and app images that are FROM it.
podman build -t localhost/meridian-base:latest -f base/Containerfile      base
podman build -t localhost/meridian-db:latest   -f base/db.Containerfile   base
podman build -t localhost/meridian-mail:latest -f base/mail.Containerfile base
podman build -t localhost/meridian-hr-portal:latest   apps/hr-portal
podman build -t localhost/meridian-crm:latest         apps/crm
podman build -t localhost/meridian-ged:latest         apps/ged
podman build -t localhost/meridian-intranet:latest    apps/intranet

podman-compose -f compose.yml up -d

echo "== fleet =="
podman ps --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E 'web|db|intra|ged|mail|edge' || true
echo ">> Next: regenerate the controller inventory + load the ServiceNow dataset from fleet.yml."
