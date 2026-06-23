# Diagrams

Vector (SVG) diagrams of the PoC — resolution-independent, so they stay crisp at any zoom and
export to PNG at any DPI (`rsvg-convert -w 3200 architecture.svg -o architecture.png`, or open in
a browser and print to PDF).

| File | What it shows |
|---|---|
| [`architecture.svg`](architecture.svg) | Deployment topology — every component and how they connect: ServiceNow PDI, GitHub, `registry.redhat.io`, the Azure RHEL 9 VM and the AAP 2.7 containers (gateway, controller, EDA, hub), the transient decision/execution environments, and the target containers. The five runtime edges are numbered ①–⑤. |
| [`remediation-flow.svg`](remediation-flow.svg) | The runtime loop in six steps — service fails → incident opened → EDA detects → job launched → remediation runs → incident closed — with the closed-loop arrow back to ServiceNow. |

## Edge colors (architecture.svg)

- **green** — ServiceNow REST (poll incidents / update incident)
- **red** — `run_job_template` (EDA → Controller, through the gateway on `:443`, path `/api/controller/`)
- **orange** — spawn execution environment
- **blue** — SSH to the targets (`host.containers.internal:2201/2202`)
- **gray dashed** — supply paths: git pulls, registry mirror, decision-environment image pull
- **dashed purple box** — transient runtime (created per event, not a long-lived container)

## Editing

These are hand-authored SVG (plain XML) — edit directly in any text editor. Two load-bearing
details are called out on the diagrams because they were the hard part to get right (see the main
[README](../README.md#key-findings-lessons-learned)): the controller credential host ends in
`/api/controller/`, and the ServiceNow service account's timezone is `GMT`.
