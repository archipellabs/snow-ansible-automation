#!/usr/bin/env bash
# Run ON the VM (after sync.sh). Reads secrets from the synced .env (PARSED, never
# sourced), renders the inventory, then runs the AAP 2.7 containerized installer.
set -euo pipefail

SETUP_DIR="$HOME/aap/ansible-automation-platform-containerized-setup-2.7-1"
TMPL="$HOME/aap/inventory.tmpl"
INV="$HOME/aap/inventory"
ENVFILE="$HOME/aap/.env"

# Safe .env reader: parses one key without shell eval (robust to % ! > { } # & ; $ ...).
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

FQDN="$(getenv AAP_FQDN)"
[ -n "$FQDN" ] || { echo "AAP_FQDN not set in ~/aap/.env" >&2; exit 1; }
APP_PW="$(getenv AAP_ADMIN_PASSWORD)"
[ -n "$APP_PW" ] || APP_PW="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20)"
REG_USER="$(getenv REGISTRY_USERNAME)"
REG_PW="$(getenv REGISTRY_PASSWORD)"
if [ -z "$REG_USER" ] || [ -z "$REG_PW" ]; then
  # Fallback: reuse an existing `podman login` (registry.redhat.io auth file).
  AUTHFILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"
  [ -f "$AUTHFILE" ] || AUTHFILE="$HOME/.config/containers/auth.json"
  CREDS="$(python3 -c "import json,base64;d=json.load(open('$AUTHFILE'));a=d['auths']['registry.redhat.io']['auth'];print(base64.b64decode(a).decode())")"
  REG_USER="${CREDS%%:*}"; REG_PW="${CREDS#*:}"
fi

# 0) Storage: the RHEL LVM image ships tiny default LVs, too small for the AAP images
#    (rootless podman fills /home). Extend home/var with free disk space. Idempotent.
sudo dnf install -y cloud-utils-growpart >/dev/null 2>&1 || true
sudo growpart /dev/sda 4 2>/dev/null || true
sudo pvresize /dev/sda4 >/dev/null
sudo lvextend -r -L 70G /dev/rootvg/homelv 2>/dev/null || true
sudo lvextend -r -L 25G /dev/rootvg/varlv 2>/dev/null || true

# 1) Registry login (idempotent) — from .env, or from the existing auth file (fallback).
printf '%s' "$REG_PW" | podman login registry.redhat.io --username "$REG_USER" --password-stdin >/dev/null

# 2) Resolve the FQDN locally to the private IP (avoids hairpin on the public IP).
IP=$(hostname -I | awk '{print $1}')
grep -q " ${FQDN}\$" /etc/hosts || echo "${IP} ${FQDN}" | sudo tee -a /etc/hosts >/dev/null

# 3) Render the inventory in Python (str.replace: robust to '|', '/', etc.).
export FQDN APP_PW REG_USER REG_PW
python3 - "$TMPL" "$INV" <<'PY'
import os, sys
s = open(sys.argv[1]).read()
for k in ('FQDN', 'APP_PW', 'REG_USER', 'REG_PW'):
    s = s.replace('__%s__' % k, os.environ[k])
open(sys.argv[2], 'w').write(s)
PY
chmod 600 "$INV"

echo "=================================================================="
echo " AAP admin password (gateway/controller/hub/eda): ${APP_PW}"
echo " (also stored in ${INV}, chmod 600)"
echo "=================================================================="

# 4) Install (from the setup dir so ansible.cfg + bundled collections are loaded).
cd "$SETUP_DIR"
ansible-playbook -i "$INV" ansible.containerized_installer.install
