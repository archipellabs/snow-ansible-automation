# targets/ — SSH key for the estate

The simulated servers are now the **Meridian Group fleet** under
[`../../simulator/`](../../simulator/) (real apps + DB + mail, brought up by `simulator/deploy.sh`).
This folder keeps only the **SSH key** the controller's `Target SSH` machine credential uses to reach
every server in the fleet.

- `keys/target_key` (+ `.pub`) — the credential's private key (gitignored). Generate it once:
  ```bash
  ssh-keygen -t ed25519 -f bootstrap/targets/keys/target_key -N "" -C meridian-fleet
  ```
- The fleet build trusts its public half:
  ```bash
  cp bootstrap/targets/keys/target_key.pub simulator/base/authorized_keys
  ```

The old single-purpose `app-node-1/2` containers (a `Containerfile` + `deploy.sh` that lived here)
were retired in favour of the fleet.
