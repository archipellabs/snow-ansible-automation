<sub>[↑ Docs map](../README.md#start-here) · [← 08 · Build](08-build.md) · **09 · Step by step** · [10 · Tests →](10-tests.md)</sub>

# Build — step by step

The full procedure, **in `bootstrap/` folder order** — every command, what it does, and how to verify it
before moving on. For the high-level map, the **prerequisites**, and the **`.env`** secrets table, see
**[08 · Build](08-build.md)**.

All commands run **from the repo root** unless noted; every step is idempotent (re-run safely). This is
the **AAP** build; the open-source **AWX** path is the equivalent — see
[05 · AAP vs AWX](05-aap-vs-awx.md) and `bootstrap/6B_awx/`.

### 0. Bootstrap — accounts, keys & `.env` (once)

- **Red Hat:** activate the **AAP 60-day trial**; create a **registry service account**
  (→ `REGISTRY_USERNAME` / `REGISTRY_PASSWORD`); download the **AAP 2.7 Containerized Setup** tarball — the
  **non-bundle** one, ~11 MB (any `2.7-x` build) — into `bootstrap/6A_aap/` (gitignored; `sync.sh` pushes
  it to the VM, `install.sh` extracts it there).
- **ServiceNow:** provision a **PDI** (→ `SN_INSTANCE`, admin `SN_USER` / `SN_PASS`).
- **Two SSH keys:**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/snow-aap-poc -N ""                                 # VM access
ssh-keygen -t ed25519 -f bootstrap/2_fleet/keys/target_key -N "" -C meridian-fleet   # controller → fleet
cp .env.example .env
```

- **Fill `.env` completely now** — including **choosing `SN_EDA_PASSWORD`** (you set this same value on the
  ServiceNow account in Step 5). With every secret in `.env`, the rest of the build runs in folder order.

### 1. Provision the VM — `bootstrap/1_infra`

```bash
cd bootstrap/1_infra
cp main.parameters.rhel.example.json main.parameters.rhel.json   # fill: dnsLabel, location, sshPublicKey (~/.ssh/snow-aap-poc.pub)
az deployment sub create --name aap-poc --location <region> \
  --template-file main.bicep --parameters main.parameters.rhel.json
```

Provisions the NSG (22/80/443/9443), vNet, public IP + DNS label, and a **RHEL 9 PAYG** VM. `cloud-init`
installs podman + podman-compose, git and ansible-core, enables rootless linger, and **grows the LVM**
so rootless podman has room from the first boot (`/home` 1G→70G, `/var` 10G→25G — the fleet images, incl.
the Keycloak JVM, need it before Step 2). **Brand-new subscription:** first
`az provider register -n Microsoft.Compute` / `Microsoft.Network`, and request `Standard DSv5 Family vCPUs`
quota (≥ 8 for `D8s_v5`) in your region.

✓ **Verify:** `ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> 'podman version && podman-compose version && ansible --version'`
(~3–5 min after cloud-init finishes). Set `FQDN`/`AAP_FQDN` in `.env`, then `python3 tests/health.py
--only "vm up"` also gates the LVM grow + `podman-compose` (so a cloud-init regression fails here, not mid-fleet).

### 2. Deploy the fleet — `bootstrap/2_fleet`

```bash
./bootstrap/2_fleet/sync.sh   # rsync simulator/ + .env to the VM, build images, bring the stack up
```

`sync.sh` → `deploy.sh` (on the VM) builds the images and brings up **9 servers + the edge (`:9443`) +
Keycloak + Vault**, with reboot-survival and the `:9443` firewall opened.

✓ **Verify:** browse `https://<FQDN>:9443/` (apps); on the VM, `podman ps` shows the fleet + `keycloak` +
`vault`.

### 3. Build the Keycloak realm — `bootstrap/3_keycloak`

```bash
python3 bootstrap/3_keycloak/configure.py   # build the 'meridian' realm from fleet.yml
```

Builds the `meridian` realm: 9 groups, 15 users (shared `KC_DEMO_PASSWORD`), and the
`hr-portal` / `aap` / `aap-provisioner` clients. *(SSO is an optional layer — see
[06 · Identity](06-identity.md).)*

✓ **Verify:** `https://<FQDN>:9443/auth/admin/` (user `admin`); the `meridian` realm exists.

### 4. Seed the secrets into Vault — `bootstrap/4_vault`

The Vault container is up (Step 2) and `.env` is full (Step 0). Seed the secrets the controller will look
up at job runtime — Vault's `:8200` isn't exposed, so reach it through an SSH tunnel:

```bash
ssh -fNL 8200:localhost:8200 azureuser@<FQDN>   # tunnel to Vault's :8200
python3 bootstrap/4_vault/seed.py               # writes secret/meridian/{servicenow,keycloak,ssh}
```

From here, the controller's credentials resolve their secrets from Vault — **nothing literal is stored in
AAP/AWX**. *(Dev-mode Vault is in-memory: re-run after any Vault/VM restart, else jobs fail.)*

✓ **Verify:** `python3 tests/health.py --only vault` → 🟢 *Vault up (unsealed)* **and** 🟢 *Vault seeded
(secret/meridian)* (3/3 paths). Before this step the seeded row is ⚪ *not seeded yet*.

### 5. ServiceNow — CMDB + integration account — `bootstrap/5_servicenow`

```bash
python3 bootstrap/5_servicenow/1_account.py   # Auto-Remediation group + eda.integration (itil role, timezone GMT)
python3 bootstrap/5_servicenow/2_cmdb.py      # load the Meridian CMDB from simulator/fleet.yml
```

`1_account.py` creates the integration user; `2_cmdb.py` loads the servers / apps / business-services /
relations / people **and** the custom server columns the dynamic inventory reads (`u_ssh_port` /
`u_service` / `u_role`, via `sys_dictionary`).

> **🔶 Manual — set the account password.** The Table API can't write it. In the SN UI:
> **Users → `eda.integration` → Set Password**, set it to **the `SN_EDA_PASSWORD` you chose in Step 0**,
> and clear `password_needs_reset`. (Timezone **must** stay `GMT` — `1_account.py` sets it; see
> [11 · Notes](11-notes.md).)

✓ **Verify:** the `Auto-Remediation` group and the 9 server CIs exist; `eda.integration` is `active` with
`time_zone = GMT`.

### 6. Ansible controller — `bootstrap/6A_aap` *or* `bootstrap/6B_awx`

> **Two alternatives — the fork point.** Steps 1–5 are shared; the controller is where AAP and AWX
> diverge. This walks the **AAP** path (`6A_aap`); the open-source **AWX** variant (`6B_awx` — k3s +
> operators) builds the same thing, bar push — see [05 · AAP vs AWX](05-aap-vs-awx.md) and the
> `bootstrap/6B_awx/` README.

> The catalog + push-wiring scripts live in `5_servicenow/`, but they run **here** (6d) — the push
> Business Rule needs the **Event Stream** that EDA creates in 6c.

#### 6a · Install AAP 2.7 (containerized)

```bash
./bootstrap/6A_aap/sync.sh                                   # push assets + setup tarball + .env to the VM
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/aap/install.sh'
```

`install.sh` (on the VM) extracts the setup tarball (`sync.sh` pushed it; any 2.7-x build), re-applies the
LVM grow (idempotent — `cloud-init` already grew it at Step 1), logs in to
`registry.redhat.io`, adds the `FQDN → private-IP` `/etc/hosts` entry (avoids hairpinning), renders the
single-node inventory (`chmod 600`), then runs `ansible.containerized_installer.install` (~24 containers).
It **prints the AAP admin password** (also in `~/aap/inventory`).

> **🔶 Manual — activate the subscription.** Open `https://<FQDN>/` (accept the self-signed cert), log in
> as `admin` (the `AAP_ADMIN_PASSWORD`), then **Settings → Subscriptions** → activate with **your Red Hat
> account** (the console.redhat.com login that holds your AAP trial) or a manifest. *Three different
> credentials, don't mix them up:* the `admin` password is only the AAP login; the subscription wants your
> **Red Hat portal account**; the `REGISTRY_*` service account only pulls images. Jobs won't launch (so
> Steps 6b–6c stall) until this is green — check with `tests/health.py --only subscription`.

✓ **Verify:** `https://<FQDN>/` logs you in; `https://<FQDN>/api/controller/v2/ping/` returns `200`.

#### 6b · Controller config-as-code

```bash
python3 bootstrap/6A_aap/controller/configure.py   # credentials, dynamic inventory, project, job templates
python3 tests/health.py                            # holistic health dashboard
```

Creates the `Target SSH` + `ServiceNow PDI` credentials (plus `Keycloak Provisioner` + `AAP Config`) —
**without literal secrets**, sourced from Vault at job runtime via a `Meridian Vault` lookup credential
(so Step 4 must have run) — the **`Meridian Fleet` inventory with a `ServiceNow CMDB` source**
(`servicenow.itsm.now` reading `inventory/meridian.now.yml`: hosts from the CMDB, not a static list), the
Git project (it pulls playbooks/rulebooks/inventory from `GIT_REPO_URL`, so **push your repo there first**;
the **first SCM sync installs the collections from `requirements.yml`** — a few minutes), and the **14 job
templates** including **`Configure EDA`**. It also removes the installer's `Demo *` objects.

✓ **Verify:** `tests/health.py` **6 · Ansible controller** rows are green; `Meridian Fleet` shows **9 hosts** from the CMDB.

#### 6c · Event-Driven Ansible

```bash
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/aap/eda/build.sh'   # build + push the custom DE to the hub
python3 bootstrap/6A_aap/eda/configure.py                            # DE object, AAP Controller cred, event-stream cred, EDA project
```

`build.sh` builds the decision environment (`de-minimal` + `servicenow.itsm`) and pushes it to the private
hub. `configure.py` registers the DE, the **`AAP Controller` credential whose host ends in
`/api/controller/`** (load-bearing — see [11 · Notes](11-notes.md)), the event-stream token credential, and
the EDA project.

> **🔶 Manual — launch *Configure EDA*.** In AAP → **Templates** → launch **`Configure EDA`**. It applies
> `eda/vars/eda.yml` declaratively (`infra.aap_configuration`, GitOps): the **3 event streams** + **all 5
> activations**. EDA can't update a running activation, so it reconciles by **delete-then-create** —
> expect the activations to be recreated each run.

✓ **Verify:** AAP → **Event-Driven Automation → Activations**: all **5 running**. Then prove the loop:
`python3 tests/scenarios/1_pull_incident_remediation.py`.

#### 6d · Catalog + push wiring (ServiceNow side)

These need the **event streams created in 6c**.

```bash
python3 bootstrap/5_servicenow/3_catalog.py            # 'Restart a service' + 'Onboard a new employee' catalog items
python3 bootstrap/5_servicenow/4_push_change_aap.py    # Business Rule (approved change → event stream) + trust the gateway CA
```

`4_push_change_aap.py` also uploads the AAP gateway CA into ServiceNow's trust store, so the Business
Rule's outbound TLS POST succeeds.

✓ **Verify:** `tests/scenarios/2_push_change_execution.py` (change), `4_selfservice_restart.py` and
`5_employee_onboarding.py` (catalog).

#### 6e · AAP admin SSO (optional)

```bash
python3 bootstrap/6A_aap/configure_sso.py   # federate the AAP gateway to Keycloak (oidc, IT-Admins → superuser)
```

Federates the AAP gateway to the `aap` Keycloak client and grants `is_superuser` to the `IT-Admins` group
(grant-only). The local `admin` login stays enabled — no lock-out.

✓ **Verify:** `https://<FQDN>/` shows **Sign in with Keycloak (Meridian)**; `python3 tests/health.py
--only sso`. Full detail in [06 · Identity](06-identity.md).

## Validate

Once the stack is up, prove it: `python3 tests/health.py` (the live health dashboard), then the numbered
`tests/scenarios/` one at a time. Full detail — the probe groups, the `--watch` live model, the
AAP/AWX seam, and what each scenario proves — is in **[10 · Tests](10-tests.md)**.

---
<sub>[↑ Docs map](../README.md#start-here) · [← 08 · Build](08-build.md) · **09 · Step by step** · [10 · Tests →](10-tests.md)</sub>
