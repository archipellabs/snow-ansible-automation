#!/usr/bin/env bash
# Run ON the VM (after rsync of targets/). Builds the target image and starts
# app-node-1/2. SSH published on the host (2201/2202).
set -euo pipefail
cd "$(dirname "$0")"

podman network exists pocnet || podman network create pocnet
podman build -t poc-target:latest .

for i in 1 2; do
  name="app-node-$i"
  podman rm -f "$name" 2>/dev/null || true
  # SSH published on the host (220i). No HTTP published: the check runs ON the target
  # (the playbook targets the host), and 808x is already taken by AAP's gateway-proxy.
  podman run -d --name "$name" --network pocnet --systemd=always \
    -p "220${i}:22" poc-target:latest
done

echo "--- targets ---"
podman ps --format '{{.Names}}\t{{.Ports}}' --filter network=pocnet
echo ">> app-node-1: ssh host:2201 ; app-node-2: ssh host:2202"
echo ">> break the service: podman exec app-node-1 systemctl stop httpd"
