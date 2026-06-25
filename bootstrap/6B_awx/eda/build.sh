#!/usr/bin/env bash
# Build the OSS decision environment (execution-environment.yml) with ansible-builder and push it to the
# in-cluster registry:2. The twin of bootstrap/6A_aap/eda/build.sh — but the AAP version pushes to the
# private Automation Hub, while here eda-server's activation pods pull the DE from registry:2 by image_url.
#
# Run ON the AWX VM (this folder is synced to ~/6B_awx/eda/). The registry is a NodePort on the node
# (localhost:30500, plain HTTP); k3s/containerd is told to pull localhost:30500 over HTTP via
# /etc/rancher/k3s/registries.yaml (set by install.sh), so activation pods can pull the same ref.
#   bash ~/6B_awx/eda/build.sh
set -euo pipefail
cd "$(dirname "$0")"

REG="localhost:30500"               # registry:2 NodePort on the node (plain HTTP)
IMG="snow-eda-de:latest"
LOCAL="localhost/${IMG}"
REMOTE="${REG}/${IMG}"

# ansible-builder into ~/.local. Ubuntu's system Python is PEP-668 "externally-managed", which blocks a
# plain --user install, so fall back to --break-system-packages (the --user install stays isolated in
# ~/.local either way). The plain form keeps working on the AAP/RHEL host.
python3 -m pip install --user --quiet ansible-builder 2>/dev/null \
  || python3 -m pip install --user --quiet --break-system-packages ansible-builder
export PATH="$HOME/.local/bin:$PATH"

echo ">> building ${LOCAL} with ansible-builder (ubi9-minimal base; java-17 + ansible-rulebook — slow first time)…"
ansible-builder build -t "$LOCAL" -f execution-environment.yml --container-runtime podman
podman image inspect "$LOCAL" >/dev/null

echo ">> pushing to the in-cluster registry (${REMOTE}, HTTP)…"
podman tag "$LOCAL" "$REMOTE"
podman push --tls-verify=false "$REMOTE"

echo ">> done. registry catalog:"
curl -s "http://${REG}/v2/_catalog" || true
echo
echo ">> Next (chantier E): reference ${REMOTE} as the eda-server Decision Environment image_url."
