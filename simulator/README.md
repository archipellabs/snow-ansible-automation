# Simulator — Meridian Group

A fictional company whose IT estate this PoC simulates, so the ServiceNow ↔ AAP automation
runs against something coherent and believable instead of throwaway hosts.

## The company

**Meridian Group** is a mid-size enterprise (~2,000 employees, several sites). Its IT department
(the *DSI*) runs a handful of internal business applications, each owned by a support team. This is
exactly what ServiceNow models: **business service → application → servers → assignment group**.

| Business service | Application | Servers | Support team |
|---|---|---|---|
| RH Self-Service | HR Portal (FastAPI) | `hr-web-01`, `hr-db-01` | HR-IT, DBA |
| Relation Client | CRM (FastAPI) | `crm-web-01`, `crm-web-02`, `crm-db-01` | CRM Team, DBA |
| Intranet | Intranet (static) | `intra-01` | Collab |
| Gestion Documentaire | GED (FastAPI) | `ged-01`, `ged-db-01` | ECM, DBA |
| Messagerie | Mail Relay (postfix) | `mail-01` | Infra |

## Source of truth

[`fleet.yml`](fleet.yml) describes the whole estate once and feeds **three** consumers, so
everything stays coherent by construction:

```
simulator/fleet.yml ──┬──► ServiceNow dataset   (CIs, applications, services, relations, groups)
                      ├──► target containers     (one container per server, running its services)
                      └──► controller inventory  (host vars: role, service, app, team)
```

The end-to-end chain is then coherent:
`incident.cmdb_ci → server → role/service (host var) → remediation playbook → right team`.

## Real apps

The applications under [`apps/`](apps/) are **real, runnable services** (FastAPI + HTML), each with a
`/health` endpoint. That lets Event-Driven Ansible detect genuine outages with the
`ansible.eda.url_check` source (not hand-created incidents), and lets the playbooks restart real
services and verify real health.

Faults can be injected for the demo (stop the systemd service, or drop the health flag file) — the
automation then detects, remediates, and verifies.

## Access (edge gateway / DMZ)

A Caddy reverse proxy ([`apps/edge/Caddyfile`](apps/edge/Caddyfile)) is the single internet-facing entry — a
DMZ "firewall" in front of the apps. Only its `:80` is reachable from outside (the NSG opens
22/80/443; the apps' 908x ports are host-only, used internally by EDA `url_check`). It path-routes
on the one FQDN, so you can see what the estate looks like "deployed":

| URL | Goes to |
|---|---|
| `http://<FQDN>/` | Intranet portal (services directory) |
| `http://<FQDN>/hr/` | HR Portal |
| `http://<FQDN>/crm/` | CRM (load-balanced over `crm-web-01` / `crm-web-02`) |
| `http://<FQDN>/ged/` | GED |

AAP keeps the admin console on `https://<FQDN>/` (:443). If `:80` is later needed for an ACME cert
on the AAP gateway, the edge can move to a dedicated port (open it in the NSG).

## Planned

- **Keycloak** (rootless podman, added to the stack) to authenticate the application *users* —
  the FastAPI apps and the intranet would sit behind it (OIDC). Later, an **AAP ↔ Keycloak**
  integration for *admin* SSO on the platform.

> Everything here is fictional. `*.meridian.example` is a documentation domain; no real company,
> data, or credentials are involved.
