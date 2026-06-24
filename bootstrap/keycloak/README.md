# keycloak/ — Meridian's corporate IdP (SSO)

Keycloak is part of the **simulated IT estate** (it ships in `simulator/compose.yml`, not the AAP
control plane) — it's Meridian's single sign-on. Employees authenticate to the apps through it
(Étape 2), and the AAP admins federate to it (Étape 3). It's the same identity for everyone.

> **Optional layer.** SSO is a realism add-on — the core PoC (the two ServiceNow ↔ AAP patterns)
> works fully without it. AAP keeps its **local `admin`** login (the OIDC authenticator only *adds* a
> sign-in option and grants superuser to `IT-Admins`; `revoke=false` + local auth = no lock-out),
> and the apps run **open** when the `OIDC_*` env is absent. Skip the whole Keycloak phase and
> nothing else breaks. **Scope:** SSO covers the apps and AAP only — **ServiceNow keeps its native
> login** (federating a SaaS PDI to a Keycloak on a private VM is out of scope; see the main README
> Limitations).

## Topology

```
:443  → AAP gateway (Envoy)            [existing]
:9443 → edge Caddy (TLS, internal CA)  [Meridian]
         ├─ /        → apps (hr/crm/ged/intranet)
         └─ /auth    → Keycloak
:80   → edge (HTTP, redirects → :9443)
```

`:443` is taken by AAP, so the Meridian edge terminates TLS on **`:9443`** and reverse-proxies both
the apps and Keycloak under the FQDN. Keycloak runs with `KC_HTTP_RELATIVE_PATH=/auth` and trusts
the edge's `X-Forwarded-*` headers (`KC_PROXY_HEADERS=xforwarded`) to build correct `https` URLs.
Open the NSG for **`:9443`**. Dev mode (H2, ephemeral) for the PoC — re-run `configure.py` after a
Keycloak restart.

## `configure.py` — realm as code (from `fleet.yml`)

Idempotent, stdlib-only, admin REST API. The same `simulator/fleet.yml` that feeds the ServiceNow
CMDB builds the `meridian` realm:

- **groups** — one per support team (= the ServiceNow assignment groups), plus `IT-Admins`
  (umbrella for DSI staff) and `Employees`;
- **users** — every person in `fleet.yml` (username = email local part), shared demo password from
  `.env` (`KC_DEMO_PASSWORD`); staff land in their team + `IT-Admins`, business users in `Employees`;
- **clients** — `hr-portal` (employee SSO) and `aap` (admin SSO via the AAP gateway, with a `groups`
  token mapper so AAP can map team membership to roles). Client secrets come from `.env`
  (`KC_HRPORTAL_CLIENT_SECRET`, `KC_AAP_CLIENT_SECRET`) so Keycloak and the apps share them.

```bash
./simulator/sync.sh                        # bring the stack up (incl. Keycloak) on the VM
python3 bootstrap/keycloak/configure.py    # then build the realm
```

- Admin console: `https://<FQDN>:9443/auth/admin/` (master realm, user `admin`).
- Account portal: `https://<FQDN>:9443/auth/realms/meridian/account` (any user, demo password).

## App SSO — hr-portal (Étape 2)

`hr-portal` (FastAPI) uses the `hr-portal` OIDC client (Authorization Code flow): the UI (`/`) and
the directory API (`/api/employees`) require a Keycloak login; **`/health` stays public** so the EDA
`url_check` monitor keeps working. With no `OIDC_*` env, the app runs open (auth disabled).

Two non-obvious points the wiring handles:
- **Consistent issuer.** The browser *and* the app's back-channel (token/userinfo) both use the
  external issuer `https://<FQDN>:9443/auth/realms/meridian`. The edge has a compose **network alias
  for `${FQDN}`**, so the app reaches Keycloak through the edge (same issuer) without NAT hairpinning;
  it talks TLS with verification off (the edge's internal-CA cert).
- **systemd env.** The app runs as a systemd unit, which doesn't inherit container env by default —
  `hr-portal.service` lists the `OIDC_*` vars under `PassEnvironment`, and compose sets them on
  `hr-web-01` (secret from `.env`).

Smoke-test it (no browser): `python3 tests/e2e_hrportal_sso.py`.

## AAP admin SSO (Étape 3)

`bootstrap/aap/configure_sso.py` federates the **AAP Platform Gateway** to Keycloak (idempotent):

- an **OIDC authenticator** pointing at the `aap` client / `meridian` realm. We use the generic
  `oidc` plugin (not the dedicated `keycloak` one) because it exposes **`VERIFY_SSL=false`** — the
  edge serves a self-signed cert — and uses OIDC discovery instead of a hand-pasted realm key;
  `GROUPS_CLAIM=groups` matches the Keycloak group mapper;
- an **authenticator map** granting `is_superuser` to the Keycloak **`IT-Admins`** group
  (`revoke=false` → grant-only, so it never removes superuser from anyone).

The built-in **Local Database Authenticator stays enabled** — `admin` always has a local login, no
lock-out. The gateway reaches Keycloak at `https://<FQDN>:9443/auth/...` (back-channel), the same URL
the browser uses, so the token issuer is consistent (hairpin to the public IP works on this VM).

```bash
python3 bootstrap/aap/configure_sso.py      # after bootstrap/keycloak/configure.py
python3 tests/e2e_aap_sso.py                # headless OIDC login -> asserts an IT-Admins user is superuser
```

Browser: open `https://<FQDN>/` → **Sign in with Keycloak (Meridian)** → e.g. `nadia.haddad` /
demo password → lands in AAP as a superuser.

## Employee onboarding (identity provisioning)

A new-joiner flow that **creates the identity in Keycloak** (onboarding = an IdP account, not a row
in the HR app DB — that DB holds HR business data). ServiceNow → EDA → Ansible → Keycloak → ticket
closed:

```
ServiceNow catalog "Arrivée collaborateur" (name, email, service)
   → Business Rule → Event Stream (servicenow-onboarding-stream)
   → push-employee-onboarding activation → "Provision Employee" JT
   → provision_employee.yml: create the Keycloak user (+ Employees group) → close the RITM
```

The playbook authenticates to Keycloak with the **`aap-provisioner`** service-account client
(`manage-users`, `client_credentials`) — least privilege, no master admin in AAP. Wire it with
the **Configure EDA** job template (declarative) + `bootstrap/servicenow/setup_onboarding.py`; validate
with `python3 tests/e2e_employee_onboarding.py`.

## Build order

Keycloak comes up **before** the apps need it and **before** AAP federates to it:

```
VM → Keycloak (this) → fleet/apps (OIDC) → ServiceNow → AAP install → AAP↔Keycloak → automation
```
