#!/usr/bin/env bash
# Build the custom EDA decision environment (de-supported + servicenow.itsm) locally.
# Run ON the VM (after rsync of ansible/eda/). Produces localhost/snow-eda-de:latest,
# which EDA uses directly (rootless, same host) — no registry push needed.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --user --quiet ansible-builder 2>/dev/null || pip3 install --user --quiet ansible-builder
export PATH="$HOME/.local/bin:$PATH"

ansible-builder build -t localhost/snow-eda-de:latest \
  -f execution-environment.yml --container-runtime podman

podman image inspect localhost/snow-eda-de:latest >/dev/null \
  && echo ">> built localhost/snow-eda-de:latest"
