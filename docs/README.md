# Documentation

The full guide to this PoC. Start with the **[root README](../README.md)** for the pitch and the
architecture diagram, then read the pages below in order — each one navigates to the next, and every
page links back to this map.

| # | Page | What you'll get |
|---|---|---|
| 1 | **[Overview](01-overview.md)** | What this is and how the pieces fit — the 5-minute mental model |
| 2 | **[The simulator](02-simulator.md)** | *Meridian Group*: who runs IT, what they run, how it's all one manifest |
| 3 | **[Architecture](03-architecture.md)** | The AAP control plane, the components, and what each script provisions |
| 4 | **[The patterns](04-patterns.md)** | The runtime flows — pull, push, self-service, onboarding, self-healing |
| 5 | **[Identity (SSO)](05-identity.md)** | The optional Keycloak layer: app SSO + AAP admin SSO |
| 6 | **[Playbooks](06-playbooks.md)** | The automation content catalog (what each playbook does) |
| 7 | **[Build it](07-build.md)** | The path — what you'll do, what you need, the 3 manual steps |
| 8 | **[Step by step](08-steps.md)** | The full build procedure — every command, with a verify check |
| 9 | **[Tests](09-tests.md)** | The health dashboard + the functional scenarios (and the AAP/AWX seam) |
| 10 | **[Notes](10-notes.md)** | Hard-won lessons, limitations, and running cost |

## Diagrams

The diagrams are embedded in the pages above. They're **hand-authored SVG** (plain XML) — resolution
independent and editable in any text editor; export to PNG with
`rsvg-convert -w 3200 diagrams/architecture.svg -o architecture.png` or print-to-PDF from a browser.

| File | Appears on | Shows |
|---|---|---|
| [`architecture.svg`](diagrams/architecture.svg) | [root README](../README.md) · [03](03-architecture.md) | deployment topology — every component and the 5 numbered runtime edges |
| [`patterns-overview.svg`](diagrams/patterns-overview.svg) | [01 · Overview](01-overview.md) | the two directions — **pull** and **push** side by side |
| [`remediation-flow.svg`](diagrams/remediation-flow.svg) | [04 · Patterns](04-patterns.md) | **pull** loop — service fails → incident → EDA polls → remediate → resolved |
| [`change-flow.svg`](diagrams/change-flow.svg) | [04 · Patterns](04-patterns.md) | **push** loop — change approved → Business Rule → Event Stream → EDA → work note |
| [`selfheal-loop.svg`](diagrams/selfheal-loop.svg) | [06 · Playbooks](06-playbooks.md) | the self-driving monitor loop — outage → incident → remediation → resolved |
| [`identity-sso.svg`](diagrams/identity-sso.svg) | [05 · Identity](05-identity.md) | the optional Keycloak layer — app SSO, AAP admin SSO, onboarding |
| [`edge-ports.svg`](diagrams/edge-ports.svg) | [05 · Identity](05-identity.md) | edge port routing — :443 (AAP) vs :9443 (edge → apps + Keycloak) |
| [`onboarding-flow.svg`](diagrams/onboarding-flow.svg) | [05 · Identity](05-identity.md) | employee onboarding — ServiceNow → EDA → Ansible → Keycloak |
| [`build-order.svg`](diagrams/build-order.svg) | [05 · Identity](05-identity.md) | the build sequence — where Keycloak fits relative to the apps and AAP |
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
(see [10 · Notes](10-notes.md)): the controller credential host ends in `/api/controller/`, and the
ServiceNow service account's timezone is `GMT`.
</details>
