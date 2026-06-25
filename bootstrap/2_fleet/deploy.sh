#!/usr/bin/env bash
# Build the Meridian Group fleet images and bring up the containers (compose). Runs ON the VM in
# ~/simulator/ — normally invoked for you by bootstrap/2_fleet/sync.sh from your laptop (rsync of
# simulator/ + .env + this script, then a remote run). To run by hand: sync simulator/ + the repo-root
# .env + this deploy.sh to ~/simulator/ on the VM, then ~/simulator/deploy.sh
#
# This script lives in bootstrap/2_fleet/ in the repo but is shipped to ~/simulator/ and run there, so it
# operates on the fleet build context (base/, apps/, compose.yml). The fleet reuses the controller's
# Target SSH key; sync.sh copies its public half to simulator/base/authorized_keys before deploying.
set -euo pipefail
cd "$(dirname "$0")"                 # ~/simulator on the VM
export PATH="$HOME/.local/bin:$PATH"   # podman-compose installs here (pip --user)

# Load the secrets compose interpolates from .env if present (parsed, never sourced — values can
# contain shell-hostile chars). sync.sh pushes the repo-root .env here as ~/simulator/.env.
RUNTIME="${RUNTIME:-aap}"
if [ -f .env ]; then
  while IFS='=' read -r k v; do
    case "$k" in
      AAP_FQDN|AWX_FQDN|KEYCLOAK_ADMIN_PASSWORD|KC_HRPORTAL_CLIENT_SECRET|HRPORTAL_SESSION_SECRET) export "$k=$v" ;;
    esac
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env)
fi

# The estate's public host = whichever control-plane VM it runs on (compose interpolates $FQDN).
if [ "$RUNTIME" = "awx" ]; then FQDN="${FQDN:-${AWX_FQDN:-}}"; else FQDN="${FQDN:-${AAP_FQDN:-}}"; fi
: "${FQDN:?set AAP_FQDN/AWX_FQDN (per RUNTIME) in ~/simulator/.env — the edge (TLS cert + redirect) and Keycloak need it}"
export FQDN
export KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
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

# Survive VM reboots: rootless containers with restart:unless-stopped only auto-start on boot if the
# user's podman-restart service is enabled (cloud-init already sets linger). Without this, a VM
# resize/reboot leaves the whole simulator Exited while AAP (systemd units) comes back on its own.
systemctl --user enable podman-restart.service 2>/dev/null || true

# Open the edge port in the host firewall. firewalld (running on RHEL) drops inbound traffic to
# ports it doesn't know — the AAP installer opened 80/443/84xx but not the simulator edge's 9443, so
# without this it times out even when the NSG allows it. --permanent survives reboots.
sudo firewall-cmd --add-port=9443/tcp --permanent >/dev/null 2>&1 && sudo firewall-cmd --reload >/dev/null 2>&1 || true

echo "== fleet =="
podman ps --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E 'web|db|intra|ged|mail|edge|keycloak' || true
echo ">> Edge on https://${FQDN}:9443/ (apps) + /auth (Keycloak). Open NSG :9443 if not already."
echo ">> Next: python3 bootstrap/3_keycloak/configure.py   (build the 'meridian' realm from fleet.yml)"
echo ">>       then regenerate the controller inventory + load the ServiceNow dataset from fleet.yml."
