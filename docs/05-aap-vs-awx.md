<sub>[↑ Docs map](../README.md#start-here) · [← 04 · The patterns](04-patterns.md) · **05 · AAP vs AWX** · [06 · Identity →](06-identity.md)</sub>

# AAP vs AWX — choosing the runtime

This integration runs on either **Ansible Automation Platform** (AAP — Red Hat, subscription) or **AWX**
(the open-source upstream). They are **not two codebases**: AWX *is* the upstream of the AAP controller,
so the controller API, the config-as-code, the playbooks, the rulebooks, the **governance**, and the
**test suite** are shared. You pick a runtime — the rest of the repo doesn't change. The `lib.runtime`
seam abstracts it, and every scenario runs on both with `RUNTIME=aap|awx`.

> **TL;DR.** Same **governed pull integration** on both, at parity. Choose **AAP** if you need the
> **push** pattern out of the box, vendor support, or a supported/regulated lifecycle. Choose **AWX** if
> cost, sovereignty, or "no subscription" lead — accepting a **stalled, community-supported EDA story**
> and the operational burden of running Kubernetes operators. The three feature gaps are all on the
> **EDA** side (push, EDA-secret sourcing, the EDA UI); the deeper risk is AWX's **maturity** (see below).

## Same DNA — what's identical

The bulk of the integration is **runtime-neutral** and proven on both (`tests/health.py --runtime awx`
all green; all pull scenarios pass on both):

- **Controller config-as-code** — the REST API is nearly identical (AWX `/api/v2` ≈ AAP
  `/api/controller/v2`): credentials, the **dynamic inventory sourced from the ServiceNow CMDB**
  (9 hosts), the project, and the **13 job templates** are created the same way (the push one — *Execute
  Change Request* — is created on both, but only wired to a flow on AAP).
- **The pull patterns** — incident auto-remediation, the self-healing monitor, self-service restart, and
  employee onboarding. All four work identically.
- **Secrets via HashiCorp Vault** — the **native runtime lookup** (lookup credential +
  `credential_input_sources`) is **identical**, because AWX is the controller upstream. On both, the
  controller stores no application secret.
- **SSO** — Keycloak OIDC into the platform admin login, on both.
- **The automation content** (`playbooks/`, `extensions/eda/rulebooks/`, `collections/`), the **simulated
  estate** (`simulator/`), and the **ServiceNow** setup (`5_servicenow/`) — shared, unchanged.
- **The tests** — the same `health.py` dashboard and `tests/scenarios/*` suite, selected by `RUNTIME`.

## Where they differ — the matrix

| Dimension | **AAP** | **AWX** |
|---|---|---|
| Install model | containerized installer (~24 containers: gateway · controller · hub · EDA) | **k3s + operators** (awx-operator + eda-server-operator) + `registry:2` + Traefik |
| Ingress / TLS | Platform Gateway `:443` | k3s **Traefik** `:443` |
| EDA engine | EDA Controller (in the gateway) | **eda-server** (`/api/eda/v1`) |
| EDA → controller auth | `AAP Controller` eda-credential (host `…/api/controller/`) | **AWX OAuth token** (`awx_token_id` + `automation_server_url`) |
| Image registry | Automation Hub | in-cluster `registry:2` |
| Decision Environment base | `de-minimal` (Red Hat registry) | `ubi9-minimal` (public) + a `tzdata` fix |
| Controller config-as-code | ✅ same REST API | ✅ **parity** |
| Pull patterns (remediation · self-service · onboarding · self-heal) | ✅ | ✅ **parity** |
| Secrets — controller (Vault) | ✅ native runtime lookup | ✅ **parity** |
| **Push (change execution)** | ✅ Event Stream (managed inbound webhook) | ❌ **AAP-only** (broker alternative) |
| **Secrets — EDA (Vault)** | ✅ native runtime lookup (via the `AAP Config` credential) | ⚠️ **config-time** read from Vault (no credential-lookup — value lands in eda-server) |
| **EDA UI** | ✅ unified gateway UI | ❌ standalone UI dead-end → eda-server API-only (demo via AWX UI) |
| SSO (Keycloak) | ✅ | ✅ |
| License / support | subscription **priced per managed node** (scales with fleet size) + enterprise support | open-source, **no node cap / no subscription**, community-only support |
| Maturity / cadence | mature, regular releases, supported lifecycle | **eda-server stalled at 1.0.2 (2024)** — no newer operator, no roadmap date |

✅ parity · ⚠️ works, but less cleanly · ❌ not available.

## The three real gaps (all on the EDA side)

**1 · Push (change execution) is AAP-only.** AAP's gateway provides a managed **Event Stream** — a single
authenticated inbound `:443` webhook a ServiceNow Business Rule POSTs an approved change to. eda-server
(1.0.2, the last release) has **no event-stream ingress**: activations run as Kubernetes Jobs with
instance-specific labels and no stable Service, so a webhook source's port can't be exposed reliably. So
on AWX **every flow is pull**. The clean, idiomatic fix (deliberately not built) is a **message broker**:
ServiceNow → an HTTP ingest (e.g. **Redpanda**, Kafka-compatible) → a topic → the activation's
`ansible.eda.kafka` source, which **connects outbound** — no inbound port to expose. See
[11 · Notes §16](11-notes.md).

**2 · EDA secrets: native lookup (AAP) vs config-time (AWX).** For **controller jobs**, both runtimes do a
live Vault lookup. For **EDA**, they diverge: on AAP the ServiceNow secret rides the `AAP Config`
controller credential into the Configure-EDA GitOps, so it too is a **native Vault lookup**. eda-server
has no credential-lookup, so on AWX the activation's `SN_*` are **read from Vault at config time** and
materialized into the activation — Vault stays the single source of truth, but the value does land in
eda-server. See [11 · Notes §17](11-notes.md).

**3 · The EDA UI.** AAP exposes EDA through its unified gateway UI. The standalone eda-server UI is a
**dead-end** with the OSS operator (it's the gateway-oriented unified UI; it won't initialize without the
Platform Gateway, and 1.0.2 is the last release). So the AWX variant exposes eda-server by its **API**
(NodePort 31080) only; the UI you demo is **AWX's**, and the EDA control plane is proven by
`tests/health.py --runtime awx` + the end-to-end pull, not a web page. See [11 · Notes §15](11-notes.md).

## Cost, support, sovereignty — the governance of the choice

Picking a runtime is itself a governance decision:

- **AAP** — a Red Hat **subscription** buys **enterprise support**, a supported lifecycle, the Platform
  Gateway (RBAC, analytics, the Event Stream), and a vendor to call. But it is **priced per managed node**,
  so **cost scales with the size of the estate you automate** — for a very large fleet (thousands of
  nodes) the subscription becomes a **material, even dominant, line item** (Red Hat doesn't publish list
  prices, but third-party price lists run from ~$13k for 100 nodes into seven figures for 10,000). The
  natural fit for **regulated** or **support-bound** environments — at a cost that grows with the fleet.
- **AWX** — **no license cost** and, crucially, **no managed-node cap** — cost is flat regardless of fleet
  size, which is exactly where a *gigantic* estate tips the maths toward AWX. Plus full **sovereignty**
  (you run the operators, no vendor dependency), at the price of **community-only** support, no SLA, and
  the operational weight of k3s + two operators.

Both encrypt credentials at rest, both integrate Vault, both do SSO — the *governance posture* is the
same. The difference is **who supports it** and **what it costs**, not how controlled it is.

## AWX — what "free" actually costs

Beyond the feature gaps, AWX carries **maturity and operational risk** an organization must price in.
"Free" is a *licence-cost* statement, not a *total-cost* one. From actually building this variant:

- **A stalling EDA story.** `eda-server-operator` **1.0.2 is the last release (2024)** — there is no
  newer operator, and the EDA / unified-UI refactor has **no published timeline**. You'd be building on a
  component whose future cadence is unknown.
- **EDA is bolted on, not integrated.** eda-server is a **separate** control plane — its own operator,
  its own `/api/eda/v1`, its own NodePort — **not part of the AWX UI** the way AAP's gateway unifies
  controller + EDA. That's two systems to deploy, secure, and reason about instead of one.
- **A bumpier install, with real problems.** Standing it up surfaced genuine yak-shaves the supported
  installer doesn't have: both operators ship a **dead `kube-rbac-proxy` sidecar image** (`gcr.io`, gone)
  that must be remapped to `quay.io/brancz/…`; the OSS DE base (`ubi9-minimal`) ships **no timezone data**,
  breaking `servicenow.itsm.records` until `tzdata` is pip-installed; rootless containers need a **PAM
  fix** to accept SSH. None are blockers — each is friction you own.
- **No support, no SLA.** Community channels only. When it breaks, **there is no vendor to call** — the
  org carries the operational burden end to end, including tracking a project whose release cadence stalled.

The point isn't that AWX is bad — for a **pull-only**, cost- or sovereignty-driven deployment with
in-house Kubernetes skills, it's a strong, genuinely capable choice. The point is that the subscription
buys **real things** (push, an integrated and maintained EDA, a supported install, someone to call), and
those things should be weighed against the licence saving — not assumed away because the binary is free.

## How to choose

- Need the **push** pattern (inbound, event-driven from ServiceNow) without standing up a broker → **AAP**.
- Need **vendor support**, a supported lifecycle, or you're in a **regulated** environment → **AAP**.
- **Very large estate** (thousands of nodes)? AAP's **per-managed-node** subscription scales linearly and
  can dominate cost → AWX's flat, uncapped model becomes compelling.
- **Pull-only** workload, **cost-** or **sovereignty**-driven, with in-house Kubernetes skills and willing
  to own a stalled, community-supported EDA → **AWX**.
- Want push on AWX anyway → the **broker** pattern (deferred) — see the gap-1 explanation above.
- Otherwise the two deliver the **same governed pull integration** — pick on **support + cost**.

## In this repo

- **`bootstrap/6A_aap/`** — the AAP build (containerized installer + config-as-code).
- **`bootstrap/6B_awx/`** — the AWX build (k3s + operators + config-as-code), end-to-end bar the push pattern.
- **`lib/runtime.py`** — the seam: `controller(runtime)` → `Aap()` / `Awx()`, `fqdn(runtime)`,
  `runtime_name()` (reads `RUNTIME`, default `aap`).
- **`tests/`** — one suite, both runtimes: `python3 tests/health.py --runtime awx`,
  `RUNTIME=awx python3 tests/scenarios/<name>.py`.

---
<sub>[↑ Docs map](../README.md#start-here) · [← 04 · The patterns](04-patterns.md) · **05 · AAP vs AWX** · [06 · Identity →](06-identity.md)</sub>
