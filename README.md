# ServiceNow + AAP + EDA — Event-Driven Incident Auto-Remediation (PoC)

A self-contained proof of concept: a **ServiceNow** incident triggers **Event-Driven Ansible**,
which runs an **Ansible Automation Platform** job that remediates the affected host and
updates/closes the incident. The implementation is generic and reusable.

## Use case

A web service goes down → an incident is opened in ServiceNow (assignment group
`Auto-Remediation`) → EDA polls ServiceNow and detects it → triggers the AAP job
**"Remediate Ping Server"** → the playbook restarts the service on the target, re-checks, and
**resolves** the incident (or **escalates** if it stays down).

## Architecture

```
ServiceNow PDI (cloud)  <->  AAP 2.7  (gateway + controller + EDA + hub, one RHEL 9 VM on Azure)  ->  target containers (sshd + httpd)
```

Everything except ServiceNow runs as **rootless podman** containers on a single Azure RHEL 9 VM.
ServiceNow is the real SaaS Personal Developer Instance (PDI).

## How it works — objects & flow

End-to-end flow:

1. A monitored service goes down → an **incident** is opened in ServiceNow, assigned to the
   **Auto-Remediation** group (`state = New`).
2. **EDA** polls ServiceNow (`servicenow.itsm.records`), detects the incident, and triggers the
   controller **job template**, passing the incident number + the affected host.
3. The **job template** runs `remediate_ping.yml` in an execution environment: SSH to the target,
   restart the service, re-check.
4. The playbook updates the incident via `servicenow.itsm`: **Resolved** if the service is back,
   otherwise **escalated**.

Objects provisioned by the scripts:

**ServiceNow** — `bootstrap/servicenow/setup.py`
| Object | Role |
|---|---|
| Assignment group `Auto-Remediation` | trigger filter — EDA only reacts to incidents in this group |
| Service account `eda.integration` (+ `itil`) | API identity used by EDA (read) and the playbook (update) |
| CIs `app-node-1` / `app-node-2` | represent the target hosts |

**AAP controller** — `ansible/controller/configure.py`
| Object | Role |
|---|---|
| Credential `Target SSH` (machine) | SSH key to reach the targets (user `ansible`) |
| Credential `ServiceNow PDI` (custom type) | injects `SN_HOST`/`SN_USERNAME`/`SN_PASSWORD` for `servicenow.itsm` |
| Inventory `POC Targets` | `app-node-1/2` at `host.containers.internal:2201/2202` |
| Project `snow-ansible-automation` | pulls the playbooks from this Git repo |
| Job template `Remediate Ping Server` | runs `ansible/playbooks/remediate_ping.yml` on `POC Targets` |

**EDA** — `ansible/eda/configure.py`
| Object | Role |
|---|---|
| Decision environment `snow-eda-de` | DE image with `servicenow.itsm` (records source), pulled from the hub |
| Credential `Hub …Container Registry` | pulls the DE image from the private Automation Hub |
| Credential `AAP Controller` | lets the rulebook launch the job template (host `…/api/controller/`) |
| EDA project `snow-ansible-automation` | same Git repo; rulebooks under `extensions/eda/rulebooks/` |
| Rulebook activation `snow-ping-remediation` | polls ServiceNow → launches the job template (injects `SN_*`) |

> `configure.py` (controller) also removes the installer's `Demo *` objects; the `Ansible Galaxy`
> credential is a system default and is kept.

## Repository layout

**`bootstrap/`** is one-time lab setup; **`ansible/`** + **`extensions/eda/rulebooks/`** are the
automation content the AAP controller and EDA pull from this Git repo.

| Path | Purpose |
|---|---|
| `bootstrap/infra/` | Azure VM as **Bicep** (`main.bicep` + `resources.bicep` + `cloud-init.yaml`); copy `main.parameters.example.json` → `main.parameters.json` (gitignored) |
| `bootstrap/aap/` | AAP containerized install: inventory template + render/install script + rsync helper |
| `bootstrap/awx/` | placeholder for a future AWX install (open-source alternative to AAP) |
| `bootstrap/servicenow/` | `setup.py` — provisions the ServiceNow objects (group, service account, CIs) |
| `bootstrap/targets/` | `Containerfile` + `deploy.sh` for the `app-node-1/2` target containers |
| `ansible/playbooks/` | `remediate_ping.yml` — the remediation playbook |
| `ansible/collections/` | `requirements.yml` — collections the project/DE needs (`servicenow.itsm`) |
| `ansible/controller/` | `configure.py` — controller config-as-code (credentials, inventory, project, job template) |
| `ansible/eda/` | `execution-environment.yml` + `build.sh` (build & push DE) + `configure.py` (DE, credentials, project, activation) |
| `extensions/eda/rulebooks/` | `snow_ping_remediation.yml` — the EDA rulebook (EDA's required discovery path) |
| `tests/` | `healthcheck.py` + `e2e_remediation.py` (controller path) + `e2e_eda.py` (full EDA auto-trigger) — re-runnable validation |
| `.env.example` | template for `.env` — the single secrets file (copy and fill) |

## Secrets

All env-style secrets live in a single **`.env`** at the repo root (gitignored, see
`.env.example`). It is **parsed by code, never `source`d** — values can contain shell-hostile
characters (`% ! > { } # & ; $` …). SSH keys stay as files.

| Secret | Location |
|---|---|
| ServiceNow (admin + `eda.integration`), registry, AAP admin, FQDN | `.env` (repo root, gitignored) |
| Project SSH key (VM access) | `~/.ssh/snow-aap-poc` |
| Target SSH key | `bootstrap/targets/keys/` (gitignored) |
| AAP admin password (deployed copy) | also in `~/aap/inventory` on the VM (chmod 600) |

## Prerequisites

- Azure subscription + `az` CLI (`az login`).
- Red Hat account with an active AAP subscription — the free **60-day AAP trial** works.
- A ServiceNow **PDI**.
- A project SSH key: `ssh-keygen -t ed25519 -f ~/.ssh/snow-aap-poc -N ""`.
- Local tools: `az`, `ssh`, `rsync`, `python3`.

## Naming & consoles

Environment-specific values are **not committed** — they live in `.env` and
`bootstrap/infra/main.parameters.json` (both gitignored). Conventions:

- **FQDN** = `<dnsLabel>.<region>.cloudapp.azure.com` (set `FQDN` in `.env`).
- **AAP UI** = `https://<FQDN>/` — user `admin`, password in `~/aap/inventory` (self-signed cert).
- **ServiceNow PDI** = `https://<SN_INSTANCE>` (set `SN_INSTANCE` in `.env`).
- **Targets**: `app-node-1` (ssh host:2201), `app-node-2` (ssh host:2202).

Vendor consoles:

| What | URL |
|---|---|
| ServiceNow developer portal (create / wake a PDI) | https://developer.servicenow.com |
| Red Hat Hybrid Cloud Console (subscriptions, manifests) | https://console.redhat.com |
| Red Hat registry service accounts | https://access.redhat.com/terms-based-registry |
| Red Hat downloads (AAP setup tarball) | https://access.redhat.com/downloads |
| AAP free trial | https://www.redhat.com/en/technologies/management/ansible/trial |
| Azure portal | https://portal.azure.com |

---

## Step by step (what we did)

### 0. Accounts & artifacts

- Activate the Red Hat **AAP 60-day trial**.
- Create a Red Hat **registry service account** (access.redhat.com/terms-based-registry) →
  `username|token` to pull from `registry.redhat.io`.
- Download the **AAP 2.7 Containerized Setup** tarball (the small, non-bundle one) and drop it
  in `bootstrap/aap/` (gitignored).
- Provision a ServiceNow **PDI**.

### 1. Provision the VM (Bicep)

```bash
cd bootstrap/infra
cp main.parameters.example.json main.parameters.json   # fill: dnsLabel, location, sshPublicKey
az deployment sub create --name aap-poc --location <region> \
  --template-file main.bicep --parameters main.parameters.json
```

Brand-new subscription gotchas (do these first if needed):

```bash
az provider register -n Microsoft.Compute
az provider register -n Microsoft.Network
# Quota: Portal -> Subscription -> Usage + quotas -> "Standard DSv5 Family vCPUs"
#        in your region -> Request increase (e.g. 5).
```

- `cloud-init` installs podman/git/ansible-core at first boot and enables rootless linger.
- Connect:
  `az ssh vm -g rg-snow-aap-poc --vm-name aap-poc --local-user azureuser --private-key-file ~/.ssh/snow-aap-poc`

### 2. Install AAP 2.7 (containerized)

Fill `.env` (`REGISTRY_USERNAME`/`REGISTRY_PASSWORD`, `AAP_ADMIN_PASSWORD`, `FQDN`), drop the
setup tarball into `bootstrap/aap/`, then from your machine:

```bash
./bootstrap/aap/sync.sh                                   # pushes assets + .env to the VM
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/aap/install.sh'
```

`install.sh` reads `.env` (registry login, admin password, FQDN), grows the LVM volumes (the
RHEL image LVs are tiny), adds the `FQDN -> private IP` `/etc/hosts` entry, renders the
single-node "growth" inventory, then runs `ansible.containerized_installer.install`. Result:
24 containers, all components on one node. **At first UI login, activate the subscription**
with your Red Hat username/password.

### 3. ServiceNow objects

```bash
cp .env.example .env        # fill in your values
python3 bootstrap/servicenow/setup.py
```

Creates the `Auto-Remediation` group, the `eda.integration` service account (+ `itil` role,
`active`, `password_needs_reset=false`), and the `app-node-1/2` CIs.

> **Gotcha**: ServiceNow silently ignores `user_password` writes via the Table API. Set
> `eda.integration`'s password **once in the UI** (open the user → *Set Password*) and store it
> in `.env` as `SN_EDA_PASSWORD` — otherwise basic auth returns 401.

### 4. Target containers

```bash
# one-time: generate the target SSH key (private gitignored; public = authorized_keys)
ssh-keygen -t ed25519 -f bootstrap/targets/keys/target_key -N "" -C poc-target
cp bootstrap/targets/keys/target_key.pub bootstrap/targets/authorized_keys

rsync -avz --exclude 'keys/' bootstrap/targets/ azureuser@<FQDN>:~/targets/
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/targets/deploy.sh'
```

Builds an ubi9-init systemd container (sshd + httpd) and starts `app-node-1/2` with SSH on host
ports 2201/2202. Break the service with `podman exec app-node-1 systemctl stop httpd`.

### 5. Controller config-as-code

```bash
python3 ansible/controller/configure.py     # credentials, inventory, project, job template
python3 tests/healthcheck.py                # 6 checks incl. EE -> target ad-hoc ping
```

### 6. Event-Driven Ansible

```bash
# build the custom DE and push it to the hub (run ON the VM, after sync.sh)
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/eda/build.sh'   # rsync ansible/eda/ -> ~/eda/ first
# then, from your machine: DE, credentials, EDA project, rulebook activation
python3 ansible/eda/configure.py
```

`configure.py` creates the decision environment, the hub registry + `AAP Controller` credentials,
the EDA project, and the rulebook activation (`log_level: info`). Two settings are load-bearing
(see Key findings): the controller credential host ends in **`/api/controller/`**, and
`eda.integration`'s ServiceNow timezone is **GMT**.

```bash
python3 tests/e2e_eda.py   # break httpd -> open incident -> EDA auto-launches the job -> resolved
```

---

## Key findings (lessons learned)

1. **EE → target networking** — the controller spawns execution environments with **pasta**
   networking, where `host.containers.internal` resolves to the host *and* can reach its
   rootless-published ports. So the targets (SSH published on the host at 2201/2202) are reached
   from the EE via **`host.containers.internal:2201/2202`** — the inventory sets
   `ansible_host=host.containers.internal` + `ansible_port` (not `127.0.0.1`, which is the EE's
   own loopback).
2. **Disk** — the RHEL LVM Azure image partitions only ~62 GB and ships tiny LVs (`/home` = 1 GB);
   the AAP images need ~25 GB → `install.sh` grows the partition + LVs.
3. **ServiceNow password** — not settable via the Table API; set it once in the UI and clear
   `password_needs_reset`.
4. **Azure quota/region** — a fresh subscription has 0 per-family vCPU quota and unregistered
   providers → register providers + request quota (the DSv5 grant may land in only one region).
5. **PAYG vs BYOS** — the RHEL PAYG image avoids Cloud Access/subscription-manager; the AAP trial
   only entitles image pulls (via the registry service account).
6. **EDA → controller API path** — `ansible-rulebook` chooses the controller API slug from the
   credential host: a host *with a path* (`https://<FQDN>/api/controller/`) selects the AAP 2.5+
   gateway slugs (`v2/config/`); a bare host selects the legacy `/api/v2/`, which **404s** behind
   the gateway. `run_job_template` silently never launches until the host carries the path.
7. **ServiceNow source timezone** — the `servicenow.itsm.records` source builds its poll-window
   filter with `gs.dateGenerate`, which ServiceNow evaluates in the **querying user's** timezone.
   The rulebook pins `remote_servicenow_timezone: UTC`, so `eda.integration`'s timezone must be
   **GMT** (`setup.py` sets it) — otherwise the window shifts hours ahead and new incidents are
   never matched. A far-past `updated_since` masks this in quick tests; it only bites at "now".
8. **EDA DE image must be in a registry** — activation workers pull the DE by `image_url` from a
   registry credential; a `localhost/...` image is invisible to them, so `build.sh` pushes the DE
   to the private hub and the DE points at `<FQDN>/snow-eda-de:latest` (pull policy `always`).

## Status

**Done — full event-driven loop working end-to-end** (`tests/e2e_eda.py` passes): a ServiceNow
incident in the `Auto-Remediation` group is auto-detected by the EDA activation, which launches the
controller job template; the playbook restarts the service and resolves the incident — no manual
launch. Covered: infra, host base, AAP 2.7 install, ServiceNow objects + service account, target
containers, EE→target path, controller config-as-code, custom DE, EDA config-as-code + activation.

**Optional next steps**: AWX variant (`bootstrap/awx/`), more remediation playbooks/use cases,
hardening (reverse proxy, real certs), and obtaining a subscription manifest for offline entitlement.

## Lifecycle & cost

```bash
az vm deallocate -g rg-snow-aap-poc -n aap-poc   # stop compute billing
az vm start      -g rg-snow-aap-poc -n aap-poc   # restart
az group delete  -n rg-snow-aap-poc --yes        # tear everything down
```

- The target containers are **not** persistent across reboots — re-run `~/targets/deploy.sh`
  after a VM restart.
- D4s_v5 ≈ 5-6 €/day while allocated; the 128 GB Premium disk keeps billing when deallocated.
