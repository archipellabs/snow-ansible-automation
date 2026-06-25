<sub>[↑ Docs map](../README.md#start-here) · [← 09 · Tests](09-tests.md) · **10 · Notes**</sub>

# Notes — lessons, limits, cost

## Key findings (lessons learned)

1. **EE → target networking** — the controller spawns execution environments with **pasta** networking,
   where `host.containers.internal` resolves to the host *and* can reach its rootless-published ports.
   So the fleet servers (SSH at `221x`) are reached via **`host.containers.internal:<ssh_port>`** — the
   dynamic inventory's `compose` sets `ansible_host`/`ansible_port` from the CMDB (not `127.0.0.1`, the
   EE's own loopback).
2. **Disk** — the RHEL LVM Azure image partitions only ~62 GB and ships tiny LVs (`/home` = 1 GB); the
   AAP images need ~25 GB → `install.sh` grows the partition + LVs.
3. **ServiceNow password** — not settable via the Table API; set it once in the UI and clear
   `password_needs_reset`.
4. **Azure quota/region** — a fresh subscription has 0 per-family vCPU quota and unregistered providers
   → register providers + request quota. The default `D8s_v5` needs **8** DSv5 vCPUs; small/paired
   regions (e.g. `australiacentral`) may require raising the quota before it deploys.
5. **PAYG vs BYOS** — the RHEL PAYG image avoids Cloud Access/subscription-manager; the AAP trial only
   entitles image pulls (via the registry service account).
6. **EDA → controller API path** — `ansible-rulebook` chooses the controller API slug from the
   credential host: a host *with a path* (`https://<FQDN>/api/controller/`) selects the AAP 2.5+ gateway
   slugs; a bare host selects the legacy `/api/v2/`, which **404s** behind the gateway. `run_job_template`
   silently never launches until the host carries the path.
7. **ServiceNow source timezone** — the `servicenow.itsm.records` source builds its poll-window filter
   with `gs.dateGenerate`, evaluated in the **querying user's** timezone. The rulebook pins
   `remote_servicenow_timezone: UTC`, so `eda.integration`'s timezone must be **GMT** (`1_account.py`
   sets it) — otherwise the window shifts and new incidents are never matched.
8. **EDA DE image must be in a registry** — activation workers pull the DE by `image_url`; a
   `localhost/...` image is invisible to them, so `build.sh` pushes the DE to the private hub.
9. **Custom DE only for what's missing** — the base is `de-minimal` (already ships `ansible.eda`); we add
   only `servicenow.itsm`. The *push* webhook source is in `ansible.eda`, so the same DE serves both.
10. **Push uses Event Streams, not an open port** — the inbound webhook is a gateway-managed endpoint on
    `:443` with a token credential; the rulebook's `ansible.eda.webhook` source is **mapped** to the
    stream via `source_mappings`. Nothing extra is opened in the NSG.
11. **ServiceNow → gateway TLS** — ServiceNow validates outbound TLS, so the Business Rule's POST fails
    with `HTTP 0` against the self-signed cert. Fix: upload the gateway CA into ServiceNow's trust store
    as a `trust_store` certificate. Production: a CA-signed cert on the gateway.
12. **ServiceNow change state model** — the Table API rejects arbitrary `state` jumps, so the trigger
    keys off the writable `approval` field (`approved`), and the playbook records a **work note** instead
    of transitioning the change.
13. **Resolved incidents stay `active=true`** — in ServiceNow `active` only flips to `false` at **Closed**
    (auto-close days later) or **Canceled**; a **Resolved** incident is still "active". So the monitor's
    anti-storm dedup (`open_incident.yml`) keys on the **open states** (`stateIN1,2,3`), *not* `active` —
    otherwise a recurrence after a fix would be deduped against the still-active Resolved ticket and never
    re-remediated. The remediation **resolves**; ServiceNow auto-closes; each outage gets its own incident.
14. **AWX runtime — reaching the fleet + rootless sshd** — on the AWX VM the controller runs jobs as
    **k3s pods** (not pasta EEs), so `host.containers.internal` doesn't resolve there. The fleet is reached
    at the **k3s node gateway `10.42.0.1`** + the published port, set as the `Meridian Fleet` inventory's
    `ansible_host` (per runtime — see [Limitations](#limitations)). And because the fleet runs **rootless
    podman**, the setuid `unix_chkpwd` can't read `/etc/shadow` in the user namespace, so PAM's *account*
    phase denies key-auth'd SSH **and** sudo (`Access denied by PAM account configuration`). The base image
    makes the account phase permissive (`account sufficient pam_permit.so` in the `sshd`/`sudo` PAM stacks)
    — auth stays key-only, and it works rootless on either host (RHEL or Ubuntu).

## Status

**Working end-to-end** — both patterns, the self-driving monitor loop, the two catalog flows, and SSO,
each with a passing, re-runnable scenario test:

- **Pull** (`1_pull_incident_remediation.py`): a ServiceNow incident is auto-detected by the polling
  activation, which launches the job; the playbook restarts the service and resolves the incident — no
  manual launch.
- **Push** (`2_push_change_execution.py`): an approved Change Request is pushed via the Event Stream to
  the webhook activation; the playbook deploys the change and writes a work note back — no manual launch.

**Optional next steps**: an AWX variant (`bootstrap/5B_awx/`), more playbooks, a CA-signed gateway cert,
and a subscription manifest for offline entitlement.

## Limitations

This is a proof of concept — deliberately scoped:

- **Not production-hardened.** Single AAP node (no HA); the gateway keeps its **self-signed certificate**;
  the targets are throwaway containers; the "change" the push playbook applies is a demo content deploy.
- **Config-as-code is hybrid.** The **EDA layer is declarative** (`infra.aap_configuration` via the GitOps
  Configure EDA job template); the controller, ServiceNow and Keycloak config are still **bespoke stdlib
  Python** over the REST APIs (transparent, zero-dependency). A fully-standard setup would move those to
  collections too.
- **The declarative EDA layer has rough edges** (EDA is young): the `eda_*` roles don't reliably accept
  token auth (we pass username/password); **EDA can't update a running activation**, so the playbook
  reconciles by **delete-then-create**; the roles `no_log` their tasks (debug with
  `aap_configuration_secure_logging: false`).
- **The fleet is reached the mono-machine way — IP/port are a bit rigged to stay single-host.** Because
  the "servers" are really containers on one VM, the connection is deliberately kept single-host instead
  of standing up a DNS zone, a dedicated subnet, or an overlay network. Each host's `ansible_port` is the
  **published SSH port** on the VM (`u_ssh_port`, a CMDB simulator artifact — ServiceNow renders it
  `"2,211"`), and `ansible_host` points at the VM itself — `host.containers.internal` on **AAP** (podman
  injects it), the **k3s node gateway `10.42.0.1`** on **AWX** (pods reach host-published ports there).
  To keep the shared CMDB inventory neutral, `ansible_host` isn't stored in the CMDB: each runtime sets it
  as an **inventory variable** (`bootstrap/5A_aap` / `5B_awx` `controller/configure.py`). A real estate
  would drop all of this and use each CI's real IP + SSH `:22`. The `u_role`/`u_service` columns and the
  standard `support_group` field are legitimate CMDB attributes.
- **SSO covers the apps and AAP, not ServiceNow.** Keycloak gives SSO to the simulated apps and AAP
  admins, but **ServiceNow keeps its native login** — federating a SaaS PDI to a Keycloak on a private VM
  would take this PoC too far. SSO as a whole is **optional**.
- **Minor.** The scenario tests leave test incidents/changes in the PDI (no cleanup); there is no CI; and
  the AAP entitlement comes from the trial/UI rather than a downloaded subscription manifest.

## Lifecycle & cost

```bash
az vm deallocate -g rg-snow-aap-poc -n aap-poc   # stop compute billing
az vm start      -g rg-snow-aap-poc -n aap-poc   # restart
az group delete  -n rg-snow-aap-poc --yes        # tear everything down
```

- The fleet containers (and Keycloak's dev-mode H2 data) are **not** persistent across reboots — re-run
  `./bootstrap/2_fleet/sync.sh` and `bootstrap/3_keycloak/configure.py` after a VM restart.
- `D8s_v5` ≈ 10-12 €/day while allocated (≈ 2× `D4s_v5`); the Premium disk keeps billing even when
  deallocated, so `az group delete` to fully stop costs.

---
<sub>[↑ Docs map](../README.md#start-here) · [← 09 · Tests](09-tests.md) · **10 · Notes**</sub>
