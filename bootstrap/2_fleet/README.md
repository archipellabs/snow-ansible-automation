# fleet/ — deploy the Meridian target fleet

The **deploy scripts** for the simulated fleet, plus the **SSH key** the controller's `Target SSH`
credential uses to reach every server. The fleet *definition* lives in [`../../simulator/`](../../simulator/).

```bash
ssh-keygen -t ed25519 -f bootstrap/2_fleet/keys/target_key -N "" -C meridian-fleet  # once (gitignored)
./bootstrap/2_fleet/sync.sh          # sync simulator/ + .env to the VM, build, bring the stack up
./bootstrap/2_fleet/sync.sh --sync   # sync only (skip the remote deploy)
```

`sync.sh` (laptop) ships `simulator/`, `.env`, and `deploy.sh` to the VM; `deploy.sh` (on the VM) builds
the images, brings the stack up with `podman-compose`, enables reboot-survival, and opens the edge port.
The build trusts the key's public half (`sync.sh` → `simulator/base/authorized_keys`); the private half is
loaded into the `Target SSH` credential by `controller/configure.py`.

📖 The estate itself → [docs/02-simulator.md](../../docs/02-simulator.md) · build steps → [docs/09-steps.md](../../docs/09-steps.md).
