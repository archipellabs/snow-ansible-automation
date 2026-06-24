# ServiceNow + AAP + Event-Driven Ansible — Two Integration Patterns (PoC)

A self-contained proof of concept demonstrating **both directions** of a ServiceNow ↔ **Ansible
Automation Platform** integration, driven by **Event-Driven Ansible**:

- **Pull — incident auto-remediation.** EDA **polls** ServiceNow; a "service down" incident
  triggers a job that restarts the service and **resolves** the incident (or escalates).
- **Push — change execution.** ServiceNow **pushes** an *approved* Change Request to an EDA
  **Event Stream**; a job executes the change on the target and annotates the record.

Everything except ServiceNow runs as **rootless podman** containers on a single Azure RHEL 9 VM;
ServiceNow is a real SaaS Personal Developer Instance (PDI). The automation content is generic.

## The two patterns

| | **Pull** — incident remediation | **Push** — change execution |
|---|---|---|
| Trigger | service down → incident in `Auto-Remediation` group | change request **approved** (with a CI) |
| Direction | EDA → ServiceNow (**outbound** poll) | ServiceNow → EDA (**inbound** webhook) |
| EDA source | `servicenow.itsm.records` (poll, 10 s) | `ansible.eda.webhook` via an **Event Stream** |
| Job template | `Restart Service` | `Execute Change Request` |
| Playbook | `restart_service.yml` (restart + resolve) | `execute_change.yml` (deploy + work note) |
| Custom DE? | **yes** (`servicenow.itsm` is in no stock DE) | no — same DE; `ansible.eda` is built in |
| Exposure | outbound only | inbound on **:443** (gateway-managed, TLS) |
| End-to-end test | `tests/e2e_pull_incident_remediation.py` | `tests/e2e_push_change_execution.py` |

**Trade-off:** pull needs no inbound exposure and self-heals (it re-polls), at the cost of
latency; push is near-real-time but requires ServiceNow to reach an authenticated endpoint and
trust the gateway's certificate. Both reuse the same AAP install, targets, and DE.

## Architecture

![Architecture — components and how they connect](docs/architecture.svg)

Everything except ServiceNow runs as **rootless podman** containers on a single Azure RHEL 9 VM.
The numbered edges ①–⑤ on the architecture trace the **pull** runtime; the **push** pattern reuses
the same components and adds a gateway-managed **Event Stream**. The runtime flow of each pattern
(SVG, vector — sources + edge-colour legend in [`docs/`](docs/)):

| Pattern | Flow diagram |
|---|---|
| Pull — incident remediation | [`docs/remediation-flow.svg`](docs/remediation-flow.svg) |
| Push — change execution | [`docs/change-flow.svg`](docs/change-flow.svg) |
| Identity & SSO (optional Keycloak layer) | [`docs/identity-sso.svg`](docs/identity-sso.svg) |

The simulated Meridian estate (fleet, apps, edge gateway, and the optional **Keycloak** IdP) runs in
its own `simulator/` compose stack behind the edge on `:9443`; `architecture.svg` focuses on the AAP
control plane, and `identity-sso.svg` covers the SSO/identity layer.

## How it works — objects & flow

### Pull — incident remediation

1. A monitored service goes down → an **incident** is opened in ServiceNow, assigned to the
   **Auto-Remediation** group (`state = New`). This can be opened by anyone, or **self-driven**: the
   `monitor-health` activation probes each app's `/health` with `ansible.eda.url_check` and raises
   the incident itself (via the `Open Incident` job template → `open_incident.yml`) — see
   [playbooks/README](playbooks/README.md#self-driving-pull-loop-monitoring).
2. **EDA** polls ServiceNow (`servicenow.itsm.records`), detects the incident, and triggers the
   `Restart Service` **job template**, passing the incident number + the affected host.
3. The job runs `restart_service.yml` in an execution environment: SSH to the target, restart the
   service, re-check.
4. The playbook updates the incident via `servicenow.itsm`: **Resolved** if the service is back,
   otherwise **escalated**.

### Push — change execution

1. A **Change Request** is **approved** in ServiceNow (and references a CI target).
2. A **Business Rule** POSTs the change (number, sys_id, target) to an AAP **Event Stream** — a
   gateway-managed webhook endpoint on `:443` with token auth.
3. The event stream feeds the `ansible.eda.webhook` source of the `push-change-execution`
   activation, which triggers the `Execute Change Request` **job template**.
4. The job runs `execute_change.yml`: deploy the change to the target, restart the service, and
   write the result back to the change as a **work note**.

### Push — self-service catalog

1. A user orders the **Service Catalog** item "Redémarrer un service" and picks a server.
2. A **Business Rule** on the request item (`sc_req_item`) POSTs the chosen server to a second AAP
   **Event Stream** (`servicenow-catalog-stream`, same `:443` token endpoint).
3. The stream feeds the `ansible.eda.webhook` source of the `push-selfservice-restart` activation,
   which triggers the `Restart Service (Self-Service)` **job template**.
4. `restart_service_selfservice.yml` restarts that server's service and **closes the request item**.
   Set up declaratively (the **Configure EDA** job template) + `bootstrap/servicenow/setup_selfservice.py`.

### Objects provisioned by the scripts

**ServiceNow** — `bootstrap/servicenow/setup.py` (pull) + `setup_change.py` (push)
| Object | Pattern | Role |
|---|---|---|
| Assignment group `Auto-Remediation` | pull | trigger filter — EDA only reacts to incidents here |
| Service account `eda.integration` (+ `itil`, TZ `GMT`) | pull | API identity for EDA + the playbook |
| Meridian CMDB (servers, apps, business services, relations, people) | both | loaded from `simulator/fleet.yml` by `bootstrap/servicenow/dataset.py` |
| Business Rule *EDA - push approved change to AAP* | push | POSTs approved changes to the event stream |
| Trust-store cert (AAP gateway CA) | push | lets ServiceNow trust the gateway's TLS certificate |

**AAP controller** — `bootstrap/aap/controller/configure.py`
| Object | Role |
|---|---|
| Credential `Target SSH` (machine) | SSH key to reach the targets (user `ansible`) |
| Credential `ServiceNow PDI` (custom type) | injects `SN_HOST`/`SN_USERNAME`/`SN_PASSWORD` for `servicenow.itsm` |
| Inventory `Meridian Fleet` + source `ServiceNow CMDB` | **dynamic inventory** from the ServiceNow CMDB (`inventory.now.yml`, `servicenow.itsm.now`): the server CIs become hosts, the `u_*` columns + standard `support_group` become host vars (`ansible_port`/`service`/`role`/`support_group`), `keyed_groups` build `role_*` / `team_*` groups |
| Project `snow-ansible-automation` | pulls the playbooks from this Git repo |
| Job template `Restart Service` (pull) / `Execute Change Request` (push) | run the two core playbooks |
| Job templates `Collect Diagnostics` · `Free Disk` · `Open Incident` · `DB Create Role` · `DB Apply Migration` · `DB Status` · `DB Backup` | the helper / DB-admin playbooks (see [playbooks/README](playbooks/README.md)) |

**EDA** — base (DE, credentials, project) via `eda/configure.py`; the **event streams + all 5
activations are declarative** — `eda/vars/eda.yml` applied by the **Configure EDA** job template
(`eda/configure.yml`, `infra.aap_configuration` — GitOps: AAP configures itself from this repo).
| Object | Pattern | Role |
|---|---|---|
| Decision environment `snow-eda-de` | all | DE image (`de-minimal` + `servicenow.itsm`), pulled from the hub |
| Credential `AAP Controller` (host `…/api/controller/`) | all | lets the rulebooks launch job templates |
| EDA project `snow-ansible-automation` | all | rulebooks under `extensions/eda/rulebooks/` |
| Activation `pull-incident-remediation` | pull | polls ServiceNow → launches the job (injects `SN_*`) |
| Activation `monitor-health` | monitor | `ansible.eda.url_check` on each `/health` → `Open Incident` (self-driving pull) |
| `ServiceNow …Event Stream` credential + Event Streams (`…-chg-stream`, `…-catalog-stream`) | push | authenticated inbound endpoints on the gateway |
| Activation `push-change-execution` | push | webhook source mapped to the change stream → launches the job |
| Activation `push-selfservice-restart` | push | webhook source mapped to the catalog stream → launches the job |

> `configure.py` (controller) also removes the installer's `Demo *` objects; the `Ansible Galaxy`
> credential is a system default and is kept.

## Repository layout

**`bootstrap/`** holds everything you run once to *stand up and configure* the platform.
**`playbooks/`**, **`extensions/eda/rulebooks/`**, and **`collections/`** are the automation
content the AAP controller and EDA pull from this Git repo (the SCM project).

| Path | Purpose |
|---|---|
| `bootstrap/infra/` | Azure VM as **Bicep** (`main.bicep` + `resources.bicep` + `cloud-init.yaml`); copy `main.parameters.example.json` → `main.parameters.json` (gitignored) |
| `bootstrap/aap/` | AAP install (`install.sh` + inventory + `sync.sh`) **and** AAP config-as-code: `controller/configure.py` + `eda/configure.py` (base, Python), the **declarative** EDA config (`eda/configure.yml` + `eda/vars/eda.yml`, run by the Configure EDA job template), and the DE build (`eda/execution-environment.yml` + `build.sh`) |
| `bootstrap/awx/` | placeholder for a future AWX install (open-source alternative to AAP) |
| `bootstrap/servicenow/` | `setup.py` (group + service account) + `dataset.py` (Meridian CMDB from `fleet.yml`) + `setup_change.py` (push Business Rule + gateway-CA trust) |
| `bootstrap/targets/` | the SSH key the controller uses to reach the fleet (the servers live in `simulator/`) |
| `simulator/` | **the simulated estate** — Meridian Group: `fleet.yml` (source of truth), real apps (`apps/`, FastAPI + intranet), DB/mail images (`base/`), edge gateway (`apps/edge/`) + Keycloak, `compose.yml`; `sync.sh` (laptop installer) + `deploy.sh` (on the VM) |
| `playbooks/` | `restart_service.yml` (pull) + `execute_change.yml` (push) — pulled by the controller project |
| `collections/` | `requirements.yml` — collections AAP installs at project sync (`servicenow.itsm`) |
| `extensions/eda/rulebooks/` | `pull_incident_remediation.yml` (pull, poll) + `push_change_execution.yml` (push, webhook) |
| `lib/` | `poc.py` — shared stdlib transport for the Python scripts (`load_dotenv`, `http_json`, auth, SSL); per-API wrappers stay inline |
| `tests/` | `healthcheck.py` + the three end-to-end tests (see below) — re-runnable validation |
| `docs/` | architecture + remediation-flow (pull) + change-flow (push) diagrams (SVG) |
| `.env.example` | template for `.env` — the single secrets file (copy and fill) |

### Tests (`tests/`)

All are idempotent and read `.env`. They build on each other from narrow to broad:

| Test | Scope | What it proves |
|---|---|---|
| `healthcheck.py` | infrastructure | ServiceNow auth, AAP gateway/controller, subscription, targets up, and the EE→target ad-hoc ping |
| `e2e_controller_restart_direct.py` | controller only (**no EDA**) | breaks httpd, opens an incident, then **launches the job template directly** and checks the service is back + the incident resolved — isolates the playbook + ServiceNow write from the event layer |
| `e2e_pull_incident_remediation.py` | full **pull** chain | opens an incident and asserts **EDA itself** auto-launches the job (never launched by the test) → resolved |
| `e2e_push_change_execution.py` | full **push** chain | approves a change and asserts EDA auto-launches the execution job via the Event Stream → deployed + work note |

`e2e_controller_restart_direct.py` is the deliberate "lower layer": if a full-chain test fails, it tells you
whether the break is in the playbook/ServiceNow side or in the event-driven trigger.

## Secrets

All env-style secrets live in a single **`.env`** at the repo root (gitignored, see
`.env.example`). It is **parsed by code, never `source`d** — values can contain shell-hostile
characters (`% ! > { } # & ; $` …). SSH keys stay as files.

| Secret | Location |
|---|---|
| ServiceNow (admin + `eda.integration`), registry, AAP admin, FQDN, `SN_EVENTSTREAM_TOKEN` | `.env` (repo root, gitignored) |
| Project SSH key (VM access) | `~/.ssh/snow-aap-poc` |
| Target SSH key | `bootstrap/targets/keys/` (gitignored) |
| AAP admin password (deployed copy) | also in `~/aap/inventory` on the VM (chmod 600) |

## Prerequisites

- Azure subscription + `az` CLI (`az login`).
- **VM sizing**: the Bicep default is `Standard_D8s_v5` — **8 vCPU / 32 GB**. AAP containerized
  (~24 containers) **plus** the Meridian simulator (11 containers incl. Keycloak/JVM) need 32 GB;
  16 GB (`Standard_D4s_v5`) OOMs once the simulator is up. Running **AAP only**? Drop back to
  `Standard_D4s_v5` in `main.parameters.json`. Either way you need that many `Standard DSv5 Family`
  vCPUs of quota **in your region** (8 for D8s_v5) — see the quota note in step 1.
- Red Hat account with an active AAP subscription — the free **60-day AAP trial** works.
- A ServiceNow **PDI**.
- A project SSH key: `ssh-keygen -t ed25519 -f ~/.ssh/snow-aap-poc -N ""`.
- Local tools: `az`, `ssh`, `rsync`, `python3`.
- NSG inbound opened: **22** (SSH), **80** (edge HTTP→HTTPS redirect), **443** (AAP gateway),
  **9443** (Meridian edge: apps + Keycloak) — all declared in `bootstrap/infra/resources.bicep`.

## Naming & consoles

Environment-specific values are **not committed** — they live in `.env` and
`bootstrap/infra/main.parameters.json` (both gitignored). Conventions:

- **FQDN** = `<dnsLabel>.<region>.cloudapp.azure.com` (set `FQDN` in `.env`).
- **AAP UI** = `https://<FQDN>/` — user `admin`, password in `~/aap/inventory` (self-signed cert).
- **ServiceNow PDI** = `https://<SN_INSTANCE>` (set `SN_INSTANCE` in `.env`).
- **Fleet**: 9 Meridian servers (`hr-web-01`, `crm-web-01`, …) reached over SSH at `host:221x`; the
  apps are browsable through the edge gateway at `https://<FQDN>:9443/` (`/hr`, `/crm`, `/ged`), and
  the corporate IdP (Keycloak) at `https://<FQDN>:9443/auth`. (`:443`/`:8443` belong to AAP.)

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

## Build order (from zero)

If you were standing this up from scratch, the **logical** order is: build the IT estate (including
its identity) first, then ITSM, then the automation control plane, then wire them together. The
step-by-step below grew historically (AAP first), but the scripts are idempotent and independent, so
either order works — this is the order that tells the cleaner story:

1. **Infra** — provision the VM (Bicep): `bootstrap/infra/` (§1).
2. **IT estate (Meridian)** — bring up the fleet + edge **+ Keycloak**: `./simulator/sync.sh`; build
   the realm from `fleet.yml`: `bootstrap/keycloak/configure.py`. The apps get OIDC login; the HR DB
   (`leave_requests`) is filled by the DB playbooks (§4). *SSO is optional — see Limitations.*
3. **ITSM** — ServiceNow CMDB + service account from the same `fleet.yml`:
   `bootstrap/servicenow/setup.py` + `dataset.py` (§3).
4. **Control plane** — install AAP (§2), then config-as-code: controller (§5), DE + EDA (§6), push
   (§7).
5. **Identity integration** — AAP admin SSO: `bootstrap/aap/configure_sso.py` (Keycloak `aap` client).
6. **Flows** — the pull/push/monitor/self-service/onboarding patterns are created by the configure
   scripts above; validate each with `tests/`.

The same `simulator/fleet.yml` is the single source of truth for **all three** consumers — the
ServiceNow CMDB, the Keycloak realm, and (indirectly) the controller inventory. The inventory is no
longer generated from `fleet.yml` directly: `dataset.py` loads the fleet into the CMDB, then the
controller's **dynamic inventory** reads the CMDB back (`fleet.yml → CMDB → inventory`), so retiring a
server CI in ServiceNow drops it from automation on the next sync.

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
#        in your region -> Request increase (>= 8 for the default D8s_v5; 4 if you use D4s_v5).
#        Small/paired regions (e.g. australiacentral) may need the increase before D8s_v5 deploys.
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
cp .env.example .env                       # fill in your values
python3 bootstrap/servicenow/setup.py      # Auto-Remediation group + eda.integration account
python3 bootstrap/servicenow/dataset.py    # the Meridian CMDB from simulator/fleet.yml
```

`setup.py` creates the `Auto-Remediation` group and the `eda.integration` service account
(+ `itil` role, `active`, `password_needs_reset=false`, timezone `GMT`). `dataset.py` then loads the
Meridian estate (servers, applications, business services, relationships, people, support groups)
from `simulator/fleet.yml`. It also adds the custom server columns the **dynamic inventory** reads
(`u_ssh_port`/`u_service`/`u_role` on `cmdb_ci_linux_server`, via `sys_dictionary`) and populates them
— so the CMDB becomes the source of truth for the controller inventory (§5). The support team comes
from the standard `support_group` reference field (no custom copy needed).

> **Gotcha**: ServiceNow silently ignores `user_password` writes via the Table API. Set
> `eda.integration`'s password **once in the UI** (open the user → *Set Password*) and store it
> in `.env` as `SN_EDA_PASSWORD` — otherwise basic auth returns 401.

### 4. Target fleet (the Meridian simulator)

```bash
# one-time: generate the SSH key (private gitignored), trusted by the fleet build
ssh-keygen -t ed25519 -f bootstrap/targets/keys/target_key -N "" -C meridian-fleet

# push simulator/ + .env to the VM and build/start the whole stack — one command
./simulator/sync.sh

# build Keycloak's 'meridian' realm (groups/users/clients) from fleet.yml
python3 bootstrap/keycloak/configure.py

# (optional) federate AAP admin login to Keycloak — the local 'admin' login still works
python3 bootstrap/aap/configure_sso.py
```

> **SSO is optional.** Keycloak (employee app login + AAP admin SSO) is a realism layer; the two
> ServiceNow ↔ AAP patterns work without it. AAP always keeps its local `admin` account, so you can
> skip `configure_sso.py` (and the whole Keycloak phase) and lose nothing core.

`sync.sh` copies the trusted SSH pubkey, rsyncs `simulator/` + the repo-root `.env` to the VM, and
runs `deploy.sh` there (use `--sync` to skip the remote run). It builds the base/app/db/mail images
and brings up the **9 fleet servers + the edge gateway + Keycloak** (`podman compose`). Browse the
apps at `https://<FQDN>:9443/` (edge → intranet, `/hr`, `/crm`, `/ged`) and the IdP at
`https://<FQDN>:9443/auth` — open the NSG for `:9443`. Break a service to trigger remediation, e.g.
`podman exec hr-web-01 systemctl stop hr-portal`.

### 5. Controller config-as-code

```bash
python3 bootstrap/aap/controller/configure.py   # credentials, project, job templates + dynamic inventory
python3 tests/healthcheck.py                     # 6 checks incl. EE -> target ad-hoc ping
```

`configure.py` creates the `Meridian Fleet` inventory with a **`ServiceNow CMDB` source** (SCM-based,
`servicenow.itsm.now` reading `bootstrap/aap/controller/inventory.now.yml` from this project) and
triggers a sync — so the hosts come from the CMDB (§3), not a static list. The `ServiceNow PDI`
credential injects the `SN_*` env the plugin authenticates with.

### 6. Event-Driven Ansible

```bash
# build the custom DE and push it to the hub (run ON the VM; sync.sh already put it in ~/aap/eda/)
ssh -i ~/.ssh/snow-aap-poc azureuser@<FQDN> '~/aap/eda/build.sh'
# base objects (DE, hub + AAP Controller + Event Stream credentials, EDA project) — stdlib Python:
python3 bootstrap/aap/eda/configure.py
# event streams + all 5 activations — DECLARATIVE: launch the "Configure EDA" job template
# (created by step 5; GitOps — AAP applies eda/configure.yml + eda/vars/eda.yml from this repo).
```

`eda/configure.py` creates the **base** only. The event streams and all activations are declared in
`eda/vars/eda.yml` and applied by the **Configure EDA** job template (`eda/configure.yml`,
`infra.aap_configuration`). Load-bearing settings (see Key findings): the controller credential host
ends in **`/api/controller/`**; `eda.integration`'s timezone is **GMT**; event streams need
`forward_events: true` or events are captured but not forwarded.

```bash
python3 tests/e2e_pull_incident_remediation.py   # break the service -> incident -> EDA auto-launches the job -> resolved
```

### 7. Push pattern (Change Request → Event Stream)

The event stream + `push-change-execution` activation are part of the declarative EDA config (step 6).
Only the ServiceNow side is separate:

```bash
python3 bootstrap/servicenow/setup_change.py # Business Rule + trust the gateway CA in ServiceNow
python3 tests/e2e_push_change_execution.py   # approve a change -> EDA executes it -> work note
```

`setup_change.py` creates the Business Rule that POSTs approved changes to the Event Stream endpoint
(a gateway-managed webhook on `:443`), and uploads the gateway's self-signed CA to ServiceNow's trust
store so the outbound TLS validates (see Key findings).

---

## Key findings (lessons learned)

1. **EE → target networking** — the controller spawns execution environments with **pasta**
   networking, where `host.containers.internal` resolves to the host *and* can reach its
   rootless-published ports. So the fleet servers (SSH published on the host at `221x`) are reached
   from the EE via **`host.containers.internal:<ssh_port>`** — the dynamic inventory's `compose`
   sets `ansible_host=host.containers.internal` + `ansible_port` from the CMDB's `u_ssh_port` (not
   `127.0.0.1`, which is the EE's own loopback).
2. **Disk** — the RHEL LVM Azure image partitions only ~62 GB and ships tiny LVs (`/home` = 1 GB);
   the AAP images need ~25 GB → `install.sh` grows the partition + LVs.
3. **ServiceNow password** — not settable via the Table API; set it once in the UI and clear
   `password_needs_reset`.
4. **Azure quota/region** — a fresh subscription has 0 per-family vCPU quota and unregistered
   providers → register providers + request quota (the DSv5 grant may land in only one region).
   The default `D8s_v5` needs **8** DSv5 vCPUs; small/paired regions (e.g. `australiacentral`) may
   require raising the quota before it deploys.
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
9. **Custom DE only for what's missing** — Red Hat's recommended base is `de-minimal` (it already
   ships `ansible.eda`); we add only `servicenow.itsm`. The *push* pattern's webhook source is in
   `ansible.eda`, so it needs **no** custom content — the same DE serves both.
10. **Push uses Event Streams, not an open port** — the inbound webhook is a gateway-managed
   endpoint on `:443` with a credential (here a `ServiceNow Event Stream` token). The activation's
   rulebook keeps an `ansible.eda.webhook` source that is **mapped** to the stream via
   `source_mappings` (source name + `rulebook_hash`). Nothing extra is opened in the NSG.
11. **ServiceNow → gateway TLS** — ServiceNow validates outbound TLS, so the Business Rule's POST
   fails with `HTTP 0` against the gateway's self-signed cert. Fix: upload the AAP gateway CA
   (`~/aap/tls/ca.cert`, CN *Ansible Automation Platform*) into ServiceNow's trust store as a
   `trust_store` certificate (ServiceNow rewrites `short_description` to the cert CN on save).
   Production alternative: a CA-signed cert on the gateway.
12. **ServiceNow change state model** — the Table API rejects arbitrary `state` jumps (a guard
   business rule), so the trigger keys off the writable `approval` field (`approved`), and the
   playbook records a **work note** instead of transitioning the change.

## Status

**Done — both integration patterns working end-to-end**, each with a passing re-runnable test:

- **Pull** (`tests/e2e_pull_incident_remediation.py`): a ServiceNow incident in `Auto-Remediation` is auto-detected by
  the polling activation, which launches the job; the playbook restarts the service and resolves
  the incident — no manual launch.
- **Push** (`tests/e2e_push_change_execution.py`): an approved Change Request is pushed via the Event Stream to
  the webhook activation, which launches the job; the playbook deploys the change and writes a work
  note back — no manual launch.

Covered: infra, host base, AAP 2.7 install, targets, EE→target path, controller + EDA
config-as-code, custom DE, both activations, and the ServiceNow side of both patterns.

**Optional next steps**: AWX variant (`bootstrap/awx/`), more playbooks/use cases, a CA-signed
gateway cert, and a subscription manifest for offline entitlement.

## Limitations

This is a proof of concept — deliberately scoped. Be aware of:

- **Not production-hardened.** Single AAP node (no HA); the gateway keeps its **self-signed
  certificate** (ServiceNow trusts it via an uploaded trust-store cert — production should use a
  CA-signed cert); the targets are throwaway containers; the "change" the push playbook applies is a
  demo content deploy, not a real change.
- **Config-as-code is hybrid.** The **EDA layer is declarative** (`infra.aap_configuration` via the
  GitOps "Configure EDA" job template); the controller, ServiceNow and Keycloak config are still
  **bespoke stdlib Python** over the REST APIs (transparent, zero-dependency). A fully-standard setup
  would move those to collections too (`infra.aap_configuration` controller roles, `servicenow.itsm`,
  `community.general.keycloak_*`).
- **The declarative EDA layer has rough edges** (EDA is young): (a) the `eda_*` roles don't reliably
  accept token auth → we pass username/password; (b) **EDA can't update a running activation** ("not
  in disabled mode and in stopped status"), so the playbook reconciles activations by
  **delete-then-create** — every apply briefly recreates them; (c) the roles `no_log` their tasks, so
  debugging needs `aap_configuration_secure_logging: false`; (d) the collection adds install time to
  the EE on the first run.
- **The dynamic inventory carries simulator plumbing in the CMDB.** The controller reads its hosts
  from the CMDB (`servicenow.itsm.now`), which is the standard, source-of-truth approach. But because
  the "servers" are really rootless containers behind the host, two host vars are **simulator
  artifacts** stored as custom `u_*` columns: every host resolves to `host.containers.internal` (not a
  per-CI IP) and connects on a published `u_ssh_port` (221x) instead of `:22`. A real estate would
  drop `u_ssh_port` and compute `ansible_host` from the CI's real IP/FQDN — the plugin config
  (`inventory.now.yml`) would shrink accordingly. The `u_role`/`u_service` columns and the standard
  `support_group` reference field are legitimate CMDB attributes and would stay. Two ServiceNow quirks
  are handled in `inventory.now.yml`: the plugin reads **display values**, so Integer fields come back
  with a thousands separator (`u_ssh_port` → `"2,211"`) and are stripped before casting `ansible_port`;
  and the PDI ships ~6 sample Linux servers, so a `query: u_role ISNOTEMPTY` scopes the sync to the
  Meridian fleet.
- **The push pattern is more simplified than the pull one.** It needed more workarounds: trust the
  gateway CA in ServiceNow, trigger on the writable `approval` field (the change state model rejects
  arbitrary Table-API transitions), and write a **work note** rather than driving the change through
  its ServiceNow lifecycle.
- **SSO covers the apps and AAP, not ServiceNow.** Keycloak gives single sign-on to the simulated
  apps (employees, Étape 2) and AAP admins (Étape 3), but **ServiceNow keeps its native login** —
  federating a SaaS PDI to a Keycloak on a private VM would need the IdP publicly reachable with a
  CA-signed cert and SAML/OIDC config on the PDI, which would take this PoC too far for little gain.
  SSO as a whole is **optional**: the core ServiceNow ↔ AAP patterns work without Keycloak, and AAP
  always keeps its local `admin` login.
- **Minor.** The e2e tests leave test incidents/changes in the PDI (no cleanup); there is no CI; and
  the AAP entitlement comes from the trial/UI rather than a downloaded subscription manifest.

## Lifecycle & cost

```bash
az vm deallocate -g rg-snow-aap-poc -n aap-poc   # stop compute billing
az vm start      -g rg-snow-aap-poc -n aap-poc   # restart
az group delete  -n rg-snow-aap-poc --yes        # tear everything down
```

- The fleet containers (and Keycloak's dev-mode H2 data) are **not** persistent across reboots —
  re-run `~/simulator/deploy.sh` (or `./simulator/sync.sh`) and `bootstrap/keycloak/configure.py`
  after a VM restart.
- `D8s_v5` ≈ 10-12 €/day while allocated (≈ 2× `D4s_v5`); the 128 GB Premium disk keeps billing
  even when deallocated, so `az group delete` to fully stop costs.

## License

[MIT](LICENSE).
