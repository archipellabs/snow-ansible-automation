#!/usr/bin/env bash
# Build the custom EDA decision environment (de-minimal + servicenow.itsm) and push
# it to the private Automation Hub. Run ON the VM after sync.sh (this folder lands in
# ~/aap/eda/; reads ~/aap/.env).
#
# EDA activation workers pull the DE by image_url from a registry credential -- a local
# `localhost/...` image is NOT visible to them -- so the image must live in the hub.
# Produces <FQDN>/snow-eda-de:latest, referenced by bootstrap/5A_aap/eda/configure.py.
set -euo pipefail
cd "$(dirname "$0")"

ENVFILE="$HOME/aap/.env"
getenv() {
  [ -f "$ENVFILE" ] || return 0
  ENVFILE="$ENVFILE" KEY="$1" python3 -c '
import os
key = os.environ["KEY"]
for line in open(os.environ["ENVFILE"]):
    s = line.strip()
    if s and not s.startswith("#") and "=" in s:
        k, v = s.split("=", 1)
        if k.strip() == key:
            print(v); break
'
}

FQDN="$(getenv FQDN)"
[ -n "$FQDN" ] || { echo "FQDN not set in $ENVFILE" >&2; exit 1; }
ADMIN_USER="$(getenv AAP_ADMIN_USER)"; [ -n "$ADMIN_USER" ] || ADMIN_USER="admin"
ADMIN_PW="$(getenv AAP_ADMIN_PASSWORD)"
REG_USER="$(getenv REGISTRY_USERNAME)"
REG_PW="$(getenv REGISTRY_PASSWORD)"

LOCAL="localhost/snow-eda-de:latest"
REMOTE="${FQDN}/snow-eda-de:latest"

python3 -m pip install --user --quiet ansible-builder 2>/dev/null || pip3 install --user --quiet ansible-builder
export PATH="$HOME/.local/bin:$PATH"

# Base image (registry.redhat.io) login — needed to pull the de-minimal base layer.
if [ -n "$REG_USER" ] && [ -n "$REG_PW" ]; then
  printf '%s' "$REG_PW" | podman login registry.redhat.io --username "$REG_USER" --password-stdin >/dev/null
fi

ansible-builder build -t "$LOCAL" -f execution-environment.yml --container-runtime podman
podman image inspect "$LOCAL" >/dev/null

# Push to the private hub (self-signed cert -> --tls-verify=false).
printf '%s' "$ADMIN_PW" | podman login "$FQDN" --username "$ADMIN_USER" --password-stdin --tls-verify=false >/dev/null
podman tag "$LOCAL" "$REMOTE"
podman push --tls-verify=false "$REMOTE"
echo ">> pushed ${REMOTE}"
