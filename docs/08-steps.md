<sub>[↑ Docs map](../README.md#start-here) · [← 07 · Build](07-build.md) · **08 · Step by step** · [09 · Tests →](09-tests.md)</sub>

# Build — step by step

The full procedure, in order — every command, **what it actually does**, and **how to verify it**
before moving on. For the high-level map, the **prerequisites**, and the **`.env`** secrets table, see
**[07 · Build](07-build.md)**.

All commands run **from the repo root** unless noted. Steps are idempotent — re-run any of them safely.

### 0. Bootstrap — accounts, keys & artifacts (once)

- **Red Hat:** activate the **AAP 60-day trial**; create a **registry service account**
  (→ `REGISTRY_USERNAME` / `REGISTRY_PASSWORD`). Download the **AAP 2.7 Containerized Setup** tarball
  and drop it in `bootstrap/5A_aap/` (gitignored).
- **ServiceNow:** provision a **PDI** (→ `SN_INSTANCE`, admin `SN_USER` / `SN_PASS`).
- **Two SSH keys**, then fill `.env`:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/snow-aap-poc -N ""                                   # VM access
ssh-keygen -t ed25519 -f bootstrap/2_fleet/keys/target_key -N "" -C meridian-fleet     # controller → fleet
cp .env.example .env                                                                  # then fill it in
```

### 1. Provision the VM (Bicep)

```bash
cd bootstrap/1_infra
cp main.parameters.rhel.example.json main.parameters.rhel.json   # fill: dnsLabel, location, sshPublicKey (~/.ssh/snow-aap-poc.pub)
az deployment sub create --name aap-poc --location <region> \
  --template-file main.bicep --parameters main.parameters.rhel.json
```

Provisions the NSG (ports 22/80/443/9443), vNet, public IP with the DNS label, and a **RHEL 9 PAYG**
VM. `cloud-init` installs podman/git/ansible-core and enables rootless linger. **Brand-new
subscription:** first `az provider register -n Microsoft.Compute` / `Microsoft.Network`, and request
`Standard DSv5 Family vCPUs` quota (≥ 8 for `D8s_v5`) in your region.

✓ **Verify:** `ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> 'podman version && ansible --version'`
(succeeds once cloud-init finishes, ~3–5 min). Set `FQDN` in `.env`.

### 2. Stand up the IT estate (simulator + Keycloak)

```bash
./bootstrap/2_fleet/sync.sh                  # rsync simulator/ + .env + deploy.sh to the VM, build, compose up
python3 bootstrap/3_keycloak/configure.py    # build the 'meridian' realm from fleet.yml
```

`sync.sh` → `deploy.sh` (on the VM) copies `target_key.pub` into `simulator/base/authorized_keys` (so
the controller's *Target SSH* key logs into every server), builds the base + db + mail + 4 app images,
brings up **9 servers + the edge (`:9443`) + Keycloak**, enables `podman-restart` (survives reboots),
and opens the host firewall for `:9443`. `configure.py` then waits for Keycloak and builds the realm:
8 groups, ~20 users (shared `KC_DEMO_PASSWORD`), and the `hr-portal` / `aap` / `aap-provisioner`
clients.

✓ **Verify:** browse `https://<FQDN>:9443/` (apps) and `https://<FQDN>:9443/auth/admin/` (Keycloak,
user `admin`). *(SSO is an optional layer — see [05 · Identity](05-identity.md).)*

### 3. ServiceNow — CMDB + integration account

```bash
python3 bootstrap/4_servicenow/1_account.py  # Auto-Remediation group + eda.integration (itil role, timezone GMT)
```

> **🔶 Manual — set the account password.** ServiceNow ignores `user_password` writes via the Table
> API. In the SN UI: **Users → `eda.integration` → Set Password**, clear `password_needs_reset`, and
> store the value in `.env` as `SN_EDA_PASSWORD`. (Timezone **must** stay `GMT` — `1_account.py` sets
> it; see [10 · Notes](10-notes.md).)

```bash
python3 bootstrap/4_servicenow/2_cmdb.py     # load the Meridian CMDB from simulator/fleet.yml
```

`2_cmdb.py` loads the servers/apps/business-services/relations/people **and** adds the custom server
columns the dynamic inventory reads (`u_ssh_port` / `u_service` / `u_role` via `sys_dictionary`).

✓ **Verify:** in ServiceNow the `Auto-Remediation` group and the 9 server CIs exist; `eda.integration`
is `active` with `time_zone = GMT`.

### 4. Install AAP 2.7 (containerized)

```bash
./bootstrap/5A_aap/sync.sh                                   # push assets + .env to the VM
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/aap/install.sh'
```

`install.sh` (on the VM) **grows the LVM volumes** (the RHEL image ships tiny ones), logs in to
`registry.redhat.io`, adds the `FQDN → private-IP` `/etc/hosts` entry (avoids hairpinning), renders the
single-node inventory (`chmod 600`), then runs `ansible.containerized_installer.install` (~24
containers). It **prints the AAP admin password** (also in `~/aap/inventory`).

> **🔶 Manual — activate the subscription.** Open `https://<FQDN>/` (accept the self-signed cert), log
> in as `admin`, and activate your Red Hat subscription/trial manifest at first login (Settings →
> Subscriptions).

✓ **Verify:** `https://<FQDN>/` logs you in; `https://<FQDN>/api/controller/v2/ping/` returns `200`.

### 5. Controller config-as-code

```bash
python3 bootstrap/5A_aap/controller/configure.py   # credentials, dynamic inventory, project, job templates
python3 tests/health.py                          # holistic health dashboard
```

Creates the `Target SSH` + `ServiceNow PDI` credentials (plus `Keycloak Provisioner` + `AAP Config`),
the **`Meridian Fleet` inventory with a `ServiceNow CMDB` source** (`servicenow.itsm.now` reading
`inventory/meridian.now.yml` — the hosts come from the CMDB, not a static list), the Git project (the **first
SCM sync installs the collections from `requirements.yml`** — can take a few minutes), and the **14 job
templates** — including **`Configure EDA`**, the GitOps template you launch in Step 6. It also removes
the installer's `Demo *` objects.

✓ **Verify:** `tests/health.py` control-plane rows are green; the `Meridian Fleet` inventory shows
**9 hosts** synced from the CMDB.

### 6. Event-Driven Ansible

```bash
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/aap/eda/build.sh'   # build + push the custom DE to the hub
python3 bootstrap/5A_aap/eda/configure.py                            # DE object, AAP Controller cred, event-stream cred, EDA project
```

`build.sh` builds the decision environment (`de-minimal` + `servicenow.itsm`) and pushes it to the
private hub. `configure.py` registers the DE, the **`AAP Controller` credential whose host ends in
`/api/controller/`** (load-bearing — see [10 · Notes](10-notes.md)), the event-stream token credential,
and the EDA project.

> **🔶 Manual — launch *Configure EDA*.** In AAP → **Templates** → launch the **`Configure EDA`** job
> template. This applies `eda/vars/eda.yml` declaratively (`infra.aap_configuration`, GitOps): the **3
> event streams** + **all 5 activations**. EDA can't update a running activation, so it reconciles by
> **delete-then-create** — expect the activations to be recreated each run.

✓ **Verify:** AAP → **Event-Driven Automation → Activations**: all **5 running**. Then prove the loop:
`python3 tests/scenarios/1_pull_incident_remediation.py`.

### 7. Push wiring (ServiceNow side)

```bash
python3 bootstrap/4_servicenow/3_push_change.py        # Business Rule (approved change → event stream) + trust the gateway CA
python3 bootstrap/4_servicenow/4_catalog.py            # 'Restart a service' + 'Onboard a new employee' catalog items
```

These need the **event streams created in Step 6**. `3_push_change.py` also uploads the AAP gateway CA
into ServiceNow's trust store, so the Business Rule's outbound TLS POST succeeds.

✓ **Verify:** `tests/scenarios/2_push_change_execution.py` (change), `4_selfservice_restart.py` and
`5_employee_onboarding.py` (catalog).

### 8. AAP admin SSO (optional)

```bash
python3 bootstrap/5A_aap/configure_sso.py               # federate the AAP gateway to Keycloak (oidc, IT-Admins → superuser)
```

Federates the AAP gateway to the `aap` Keycloak client and grants `is_superuser` to the `IT-Admins`
group (grant-only). The local `admin` login stays enabled — no lock-out.

✓ **Verify:** `https://<FQDN>/` shows **Sign in with Keycloak (Meridian)**; `python3 tests/health.py
--only sso`. Full detail in [05 · Identity](05-identity.md).

## Validate

Once the stack is up, prove it: `python3 tests/health.py` (the live health dashboard), then the
numbered `tests/scenarios/` one at a time. Full detail — the probe groups, the deep/light + `--watch`
model, the AAP/AWX seam, and what each scenario proves — is in **[09 · Tests](09-tests.md)**.

---
<sub>[↑ Docs map](../README.md#start-here) · [← 07 · Build](07-build.md) · **08 · Step by step** · [09 · Tests →](09-tests.md)</sub>
