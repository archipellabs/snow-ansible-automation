<sub>[↑ Docs map](../README.md#start-here) · [← 06 · Playbooks](06-playbooks.md) · **07 · Build** · [08 · Step by step →](08-steps.md)</sub>

# Build it — the path

What you'll do, what you need, and where the human steps are — before diving into the commands.
Everything is **scripted and idempotent**: you run it from your laptop against one Azure VM, and you
can safely re-run any step. Only **three actions need a human in a browser** (flagged 🔶) — everything
else is a script. Budget ~30–45 min, most of it the AAP install.

> **The full procedure — every command, what it does, and how to verify it — is in
> [08 · Step by step](08-steps.md).** This page is the map and the checklist of what to have ready.

## At a glance — the path

| Phase | You stand up | Run (from your laptop) | 🔶 Manual |
|---|---|---|---|
| **0 · Bootstrap** | accounts, keys, secrets | accounts + `ssh-keygen` ×2 + fill `.env` | — |
| **1 · Infra** | the Azure RHEL VM | `az deployment sub create …` | — |
| **2 · IT estate** | fleet + apps + Keycloak | `./bootstrap/2_fleet/sync.sh` · `keycloak/configure.py` | — |
| **3 · ITSM** | ServiceNow CMDB + account | `servicenow/1_account.py` · `2_cmdb.py` | 🔶 set the account password |
| **4 · Control plane** | AAP 2.7 (≈24 containers) | `aap/sync.sh` · `~/aap/install.sh` | 🔶 activate the subscription |
| **5 · Controller** | credentials, dynamic inventory, job templates | `aap/controller/configure.py` | — |
| **6 · EDA** | decision env + 5 activations | `~/aap/eda/build.sh` · `aap/eda/configure.py` | 🔶 launch *Configure EDA* |
| **7 · Push wiring** | catalog + change BR (push) | `servicenow/3_catalog.py` · `4_push_change_aap.py` | — |
| **8 · SSO** *(optional)* | AAP admin login via Keycloak | `aap/configure_sso.py` | — |
| **✓ Validate** | proof it works | `tests/health.py` · `tests/scenarios/*` | — |

> **🔶 The only three things you do by hand** (everything else is a script):
> 1. **Set the `eda.integration` password** in the ServiceNow UI — the Table API can't write it (Step 3).
> 2. **Activate the AAP subscription** at first UI login (Step 4).
> 3. **Launch the *Configure EDA* job template** once, to apply the declarative EDA config (Step 6).

The scripts are independent and idempotent, so the order is flexible — the one above tells the cleanest
story (estate → ITSM → control plane → wire them together). **→ [Run it, step by step](08-steps.md).**

## Prerequisites

- **Azure** subscription + `az` CLI (`az login`). On a brand-new subscription, register the providers
  and request quota first ([Step 1](08-steps.md#1-provision-the-vm-bicep)).
- **VM sizing:** the Bicep default is `Standard_D8s_v5` — **8 vCPU / 32 GB**. AAP containerized (~24
  containers) **plus** the Meridian simulator (11 containers incl. a Keycloak JVM) need 32 GB; 16 GB
  (`D4s_v5`) OOMs once the simulator is up. Running **AAP only**? Drop back to `D4s_v5`. Either way you
  need that many `Standard DSv5 Family` vCPUs of quota **in your region**.
- **Red Hat** account with an active AAP subscription — the free **60-day AAP trial** works.
- A ServiceNow **PDI** (Personal Developer Instance).
- Local tools: `az`, `ssh`, `rsync`, `python3` (+ `PyYAML`, auto-installed by the CMDB script).
- **NSG inbound** opened by `resources.bicep`: **22** (SSH), **80** (edge HTTP→HTTPS), **443** (AAP
  gateway), **9443** (Meridian edge: apps + Keycloak).

| Vendor console | URL |
|---|---|
| ServiceNow developer portal (create / wake a PDI) | https://developer.servicenow.com |
| Red Hat Hybrid Cloud Console (subscriptions, manifests) | https://console.redhat.com |
| Red Hat registry service accounts | https://access.redhat.com/terms-based-registry |
| AAP free trial | https://www.redhat.com/en/technologies/management/ansible/trial |
| Azure portal | https://portal.azure.com |

## Secrets — the `.env`

All env-style secrets live in a single **`.env`** at the repo root (gitignored — `cp .env.example .env`).
It is **parsed by code, never `source`d**, so values can contain shell-hostile characters
(`% ! > { } # & ; $` …). SSH keys stay as files.

| Group | Variables | Where it comes from |
|---|---|---|
| **Host** | `FQDN` | Bicep output: `<dnsLabel>.<region>.cloudapp.azure.com` |
| **ServiceNow** | `SN_INSTANCE`, `SN_USER`, `SN_PASS`, `SN_EDA_USERNAME`, `SN_EDA_PASSWORD` | your PDI; **`SN_EDA_PASSWORD` is 🔶 set in the SN UI** (Step 3) |
| **Red Hat registry** | `REGISTRY_USERNAME`, `REGISTRY_PASSWORD` | a registry service account — `username\|token` |
| **AAP admin** | `AAP_ADMIN_USER` (=`admin`), `AAP_ADMIN_PASSWORD` | you choose; `install.sh` also prints it |
| **Git project** | `GIT_REPO_URL` | this repo's clone URL (controller + EDA pull from it) |
| **Push token** | `SN_EVENTSTREAM_TOKEN` | generate: `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` |
| **Keycloak** | `KEYCLOAK_ADMIN_PASSWORD`, `KC_DEMO_PASSWORD`, `KC_HRPORTAL_CLIENT_SECRET`, `KC_AAP_CLIENT_SECRET`, `KC_PROVISIONER_SECRET`, `HRPORTAL_SESSION_SECRET` | you choose (random strings) |

| SSH key (a file, not in `.env`) | Path | Used by |
|---|---|---|
| Project key (VM access) | `~/.ssh/snow-aap-poc` | you → the VM |
| Target key (fleet access) | `bootstrap/2_fleet/keys/target_key` | the controller → the Meridian servers |

**Naming & consoles:** `FQDN` = `<dnsLabel>.<region>.cloudapp.azure.com`. AAP UI = `https://<FQDN>/`
(user `admin`, self-signed cert). Apps = `https://<FQDN>:9443/` (`/hr`, `/crm`, `/ged`); Keycloak =
`https://<FQDN>:9443/auth`. ServiceNow = `https://<SN_INSTANCE>`.

---

Got the accounts, the two keys, and a filled `.env`? **→ [08 · Step by step](08-steps.md)** runs the
whole thing, with a verify check after each phase.

---
<sub>[↑ Docs map](../README.md#start-here) · [← 06 · Playbooks](06-playbooks.md) · **07 · Build** · [08 · Step by step →](08-steps.md)</sub>
