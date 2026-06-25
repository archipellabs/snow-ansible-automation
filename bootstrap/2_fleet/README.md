# fleet/ — deploy the Meridian target fleet

The **deploy scripts** for the simulated target fleet, plus the **SSH key** the AAP controller's
`Target SSH` credential uses to reach every server. The fleet *definition* — `fleet.yml`, the real
apps, the DB/mail images, the edge gateway + Keycloak, and `compose.yml` — lives in
[`../../simulator/`](../../simulator/).

- **`sync.sh`** — laptop-side: copy the public key into the build context, push `simulator/` + `.env`
  + `deploy.sh` to the VM, then run the deploy there.
  ```bash
  ./bootstrap/2_fleet/sync.sh            # sync + build + start the stack on the VM
  ./bootstrap/2_fleet/sync.sh --sync     # sync only (skip the remote deploy)
  ```
- **`deploy.sh`** — runs on the VM (shipped to `~/simulator/`): build the fleet images and bring the
  stack up (`podman-compose`), enable reboot-survival, open the edge port.
- **`keys/target_key`** (+ `.pub`) — the credential's private key (gitignored). Generate it once:
  ```bash
  ssh-keygen -t ed25519 -f bootstrap/2_fleet/keys/target_key -N "" -C meridian-fleet
  ```
  The image build trusts its public half — `sync.sh` copies it to `simulator/base/authorized_keys`,
  and `bootstrap/6A_aap/controller/configure.py` loads the private half into the `Target SSH` credential.

📖 The estate itself → [docs/02-simulator.md](../../docs/02-simulator.md) · build steps →
[docs/08-steps.md](../../docs/08-steps.md).
