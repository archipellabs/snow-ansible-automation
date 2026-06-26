<sub>[↑ Docs map](../README.md#start-here) · **01 · Overview** · [02 · The simulator →](02-simulator.md)</sub>

# Overview — the mental model

Five minutes to grasp what this is, before any code. The whole PoC is **three actors and two
patterns**.

## Three actors

| Actor | Role | Think of it as |
|---|---|---|
| **ServiceNow** | the system of record (ITSM) — incidents, changes, the CMDB, the service catalog | *where work is requested and tracked* |
| **Automation Controller (AAP *or* AWX)** | the automation engine — job templates run playbooks against the fleet | *the hands that do the work* |
| **Event-Driven Ansible (EDA)** | the connective tissue — it watches for events and launches the right job | *the reflex between the two* |

EDA is what makes this **event-driven** rather than scheduled or manual: nothing runs on a timer and
no one clicks "launch". An event happens, the right automation fires.

The controller is **AAP or AWX** — the same integration, governance, and content run on either; the
runtime is a support-and-cost choice, not a lock-in (see **[05 · AAP vs AWX](05-aap-vs-awx.md)**).

## Two patterns — the two ways EDA bridges the gap

![Two patterns — pull and push, the two directions EDA bridges](diagrams/patterns-overview.svg)

- **Pull** — EDA *polls* ServiceNow's incident table (every 10 s). A matching incident launches the
  remediation job. ServiceNow doesn't even need to know the controller exists; nothing inbound is exposed; it
  self-heals because it keeps re-polling.
- **Push** — a ServiceNow **Business Rule** POSTs an approved change to an **Event Stream** (a
  gateway-managed webhook). The event launches the execution job. Near-real-time, at the cost of one
  authenticated inbound endpoint. *(Push is **AAP-only** — on AWX every flow is pull; see
  [05 · AAP vs AWX](05-aap-vs-awx.md).)*

Same control plane, same fleet, same content — two opposite trigger directions. That symmetry *is* the
demo. The step-by-step runtime of each lives in **[04 · The patterns](04-patterns.md)**.

## Governed throughout

The point isn't only that it automates — it's that it automates **without bypassing control**. The
governance an enterprise expects is built in, not bolted on:

- **ServiceNow stays the system of record + approval gate** — the inventory is the **CMDB** (one source
  of truth), a change must be **approved** to run, and every action writes a **work note** back (audit trail).
- **Secrets live in HashiCorp Vault** — resolved at job runtime; no secret is stored in the platform.
- **Identity is Keycloak SSO/RBAC**, with **least-privilege** service accounts — no master admin in the automation.
- **Everything is code** — the EDA configuration is declarative and applied from Git (GitOps).
- **Blast radius is contained** — EDA reacts only to the `Auto-Remediation` assignment group, not every incident.

Where each of these lives is named in **[03 · Architecture](03-architecture.md)**.

## Running against something real

The automation doesn't act on throwaway hosts — it acts on **Meridian Group**, a fictional but
coherent enterprise: support teams, business services, applications, servers, a populated CMDB,
business users, and an identity provider. One manifest (`simulator/fleet.yml`) defines it all and
materialises it everywhere (containers, the ServiceNow CMDB, Keycloak, the controller's inventory) — so a
ServiceNow incident maps cleanly to a real server, its owning team, and the right playbook.

Meet the cast in **[02 · The simulator](02-simulator.md)**.

## What this is *not*

A production deployment. It's a deliberately-scoped PoC: a single node, self-signed certificates,
throwaway target containers, and a free ServiceNow developer instance. The governance *pattern* is
demonstrated end-to-end, but it is **not production-hardened**. The honest limits — and the
hard-won lessons behind the parts that were tricky — are in **[11 · Notes](11-notes.md)**.

---
<sub>[↑ Docs map](../README.md#start-here) · **01 · Overview** · [02 · The simulator →](02-simulator.md)</sub>
