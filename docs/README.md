# Documentation

The full guide to this PoC. Start with the **[root README](../README.md)** for the pitch and the
architecture diagram, then read the pages below in order — each one navigates to the next, and every
page links back to this map.

| # | Page | What you'll get |
|---|---|---|
| 1 | **[Overview](01-overview.md)** | What this is and how the pieces fit — the 5-minute mental model |
| 2 | **[The simulator](02-simulator.md)** | *Meridian Group*: who runs IT, what they run, how it's all one manifest |
| 3 | **[Architecture](03-architecture.md)** | The control plane (AAP or AWX), the components, and what each script provisions |
| 4 | **[The patterns](04-patterns.md)** | The runtime flows — pull, push, self-service, onboarding, self-healing |
| 5 | **[AAP vs AWX](05-aap-vs-awx.md)** | The runtime comparison — parity, differences, and how to choose |
| 6 | **[Identity (SSO)](06-identity.md)** | The Keycloak layer: app SSO + admin SSO (least-privilege) |
| 7 | **[Playbooks](07-playbooks.md)** | The automation content catalog (what each playbook does) |
| 8 | **[Build it](08-build.md)** | The path — what you'll do, what you need, the manual steps |
| 9 | **[Step by step](09-steps.md)** | The full build procedure — every command, with a verify check |
| 10 | **[Tests](10-tests.md)** | The health dashboard + the functional scenarios (and the AAP/AWX seam) |
| 11 | **[Notes](11-notes.md)** | Hard-won lessons, limitations, and running cost |

## Diagrams

The diagrams are embedded in the pages above. They're **hand-authored SVG** (plain XML) — resolution
independent and editable in any text editor; export to PNG with
`rsvg-convert -w 3200 diagrams/architecture.svg -o architecture.png` or print-to-PDF from a browser.

| File | Appears on | Shows |
|---|---|---|
| [`overview.svg`](diagrams/overview.svg) | [root README](../README.md) | generalist, governed architecture — ServiceNow drives a Controller (AAP or AWX) + EDA; Vault, Keycloak, GitOps |
| [`architecture.svg`](diagrams/architecture.svg) | [03](03-architecture.md) | deployment topology (AAP) — every component and the numbered runtime edges |
| [`patterns-overview.svg`](diagrams/patterns-overview.svg) | [01 · Overview](01-overview.md) | the two directions — **pull** and **push** side by side |
| [`remediation-flow.svg`](diagrams/remediation-flow.svg) | [04 · Patterns](04-patterns.md) | **pull** loop — service fails → incident → EDA polls → remediate → resolved |
| [`change-flow.svg`](diagrams/change-flow.svg) | [04 · Patterns](04-patterns.md) | **push** loop — change approved → Business Rule → Event Stream → EDA → work note |
| [`selfheal-loop.svg`](diagrams/selfheal-loop.svg) | [07 · Playbooks](07-playbooks.md) | the self-driving monitor loop — outage → incident → remediation → resolved |
| [`identity-sso.svg`](diagrams/identity-sso.svg) | [06 · Identity](06-identity.md) | the Keycloak layer — app SSO, admin SSO, onboarding |
| [`edge-ports.svg`](diagrams/edge-ports.svg) | [06 · Identity](06-identity.md) | edge port routing — :443 (control plane) vs :9443 (edge → apps + Keycloak) |
| [`onboarding-flow.svg`](diagrams/onboarding-flow.svg) | [06 · Identity](06-identity.md) | employee onboarding — ServiceNow → EDA → Ansible → Keycloak |
| [`build-order.svg`](diagrams/build-order.svg) | [06 · Identity](06-identity.md) | the build sequence — where Keycloak fits relative to the apps and AAP |
| [`simulator-org.svg`](diagrams/simulator-org.svg) | [02 · Simulator](02-simulator.md) | **who** — support teams + business users, grouped by domain |
| [`simulator-estate.svg`](diagrams/simulator-estate.svg) | [02 · Simulator](02-simulator.md) | **what** — the fleet behind a DMZ edge: web/app tier → data tier |
| [`simulator-manifest.svg`](diagrams/simulator-manifest.svg) | [02 · Simulator](02-simulator.md) | **how** — `fleet.yml` materialising into containers, CMDB, Keycloak, inventory |

<details>
<summary><code>architecture.svg</code> edge legend (for editing)</summary>

- **green** — ServiceNow REST (poll incidents / update incident)
- **red** — `run_job_template` (EDA → Controller, through the gateway on `:443`, path `/api/controller/`)
- **orange** — spawn execution environment
- **blue** — SSH to the fleet servers (`host.containers.internal:221x`)
- **gray dashed** — supply paths: git pulls, registry mirror, decision-environment image pull
- **dashed purple box** — transient runtime (created per event, not a long-lived container)

Two load-bearing details are called out on the diagram because they were the hard part to get right
(see [11 · Notes](11-notes.md)): the controller credential host ends in `/api/controller/`, and the
ServiceNow service account's timezone is `GMT`.
</details>
