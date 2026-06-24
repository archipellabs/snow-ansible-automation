# ServiceNow ⇄ Ansible Automation Platform

**Self-healing IT, in both directions — driven by Event-Driven Ansible.**

> A monitored service crashes. Seconds later a ServiceNow incident appears, **Event-Driven Ansible**
> picks it up, restarts the service, and flips the ticket to **Resolved** — nobody touched a keyboard.
> Approve a *change* in ServiceNow instead, and the same machinery executes it on the target and writes
> back a work note.

A self-contained **proof of concept** that demonstrates **both directions** of a ServiceNow ↔ Ansible
Automation Platform integration, driven by **Event-Driven Ansible (EDA)** — running against a
*realistic simulated enterprise*, not throwaway hosts. Everything except ServiceNow (a real SaaS
developer instance) runs as rootless containers on a single VM.

![Architecture — how the components connect](docs/diagrams/architecture.svg)

## Why it's interesting

- 🔁 **Both directions, one demo.** **Pull** — EDA *polls* ServiceNow and auto-remediates incidents.
  **Push** — ServiceNow *pushes* approved changes to an EDA **Event Stream** for execution.
- 🤖 **Closed-loop, no human.** Outage → incident → remediation → *Resolved* — with a self-driving
  monitor that opens the incident on its own.
- 🏢 **A believable estate.** *Meridian Group*: 6 support teams, 5 business services, 4 real apps,
  9 servers, a populated **CMDB**, business users, and a **Keycloak** IdP — all from one manifest.
- ⚙️ **Config-as-code throughout.** Declarative EDA (GitOps), a **dynamic inventory sourced from the
  ServiceNow CMDB**, and dependency-free stdlib provisioning scripts.
- ✅ **Proven end-to-end.** Real FastAPI apps with `/health`, re-runnable scenario tests, and a live
  `htop`-style health dashboard.

## The two patterns

| | **Pull** — incident remediation | **Push** — change execution |
|---|---|---|
| Trigger | service down → incident in `Auto-Remediation` | change request **approved** (with a CI) |
| Direction | **EDA → ServiceNow** (outbound poll) | **ServiceNow → EDA** (inbound webhook) |
| EDA source | `servicenow.itsm.records` (poll, 10 s) | `ansible.eda.webhook` via an **Event Stream** (`:443`) |
| Result | service restarted, incident **Resolved** | change deployed, **work note** written back |

Pull needs no inbound exposure and self-heals (it re-polls); push is near-real-time but requires
ServiceNow to reach an authenticated endpoint. Both reuse the same AAP install, targets, and content.

## Start here

New to it? Read in order — each page builds the mental model:

| # | Page | What you'll get |
|---|---|---|
| 1 | **[Overview](docs/01-overview.md)** | What this is and how the pieces fit — the 5-minute mental model |
| 2 | **[The simulator](docs/02-simulator.md)** | *Meridian Group*: who runs IT, what they run, how it's all one manifest |
| 3 | **[Architecture](docs/03-architecture.md)** | The AAP control plane, the components, and what each script provisions |
| 4 | **[The patterns](docs/04-patterns.md)** | The runtime flows — pull, push, self-service, onboarding, self-healing — step by step |
| 5 | **[Identity (SSO)](docs/05-identity.md)** | The optional Keycloak layer: app SSO + AAP admin SSO |
| 6 | **[Playbooks](docs/06-playbooks.md)** | The automation content catalog (what each playbook does) |
| 7 | **[Build it](docs/07-build.md)** | The path — what you'll do, what you need, the 3 manual steps |
| 8 | **[Step by step](docs/08-steps.md)** | The full build procedure — every command, with a verify check |
| 9 | **[Tests](docs/09-tests.md)** | The health dashboard + the functional scenarios (and the AAP/AWX seam) |
| 10 | **[Notes](docs/10-notes.md)** | Hard-won lessons, limitations, and running cost |

## Status

**Working end-to-end** — both patterns, the self-healing monitor loop, the two catalog flows, and SSO,
each with a passing, re-runnable test. It is a PoC, deliberately scoped: single node, self-signed
certificates, throwaway targets. The honest limits are in **[Notes](docs/10-notes.md)**.

## License

[MIT](LICENSE).
