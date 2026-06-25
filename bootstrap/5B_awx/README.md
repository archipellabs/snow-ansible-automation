# 5B_awx/ — AWX variant (roadmap)

The fully open-source alternative to [`../5A_aap/`](../5A_aap/) — same functionality, no AAP, no
subscription. **Status: architecture decided, build in progress.** The runtime seam already exists
(`lib/runtime.py`, `tests/health.py --runtime aap|awx`), so a large part of the project is reused as-is.

## The core gap — AWX ≈ Automation Controller only

AAP bundles things AWX does **not** ship. We fill each with its OSS upstream:

| AAP brick | OSS replacement (decided) |
|---|---|
| Automation Controller | **AWX** (awx-operator on k3s) |
| EDA Controller | **eda-server** (eda-server-operator) — restores activations + event streams + the `/api/eda/v1` API |
| Platform Gateway (`:443`, routing, SSO, event streams) | **k3s Traefik ingress** + TLS (host/path routing) |
| Automation Hub (private registry) | **`registry:2`** in-cluster for the DE image; collections pulled from **public Galaxy** at project sync |
| Subscription / manifest | — (free) |

> Load-bearing detail: the rulebook's controller credential host is **`/api/v2/`** for AWX (AAP needed
> `/api/controller/`). See [docs/10 · Notes](../../docs/10-notes.md).

## Decided architecture (setup ①)

**k3s + AWX + eda-server + registry:2 + Traefik ingress/TLS + Keycloak SSO**, on the Ubuntu/D4 VM.

- Choosing **eda-server** (not raw `ansible-rulebook`) gives the **same EDA API as AAP** → the declarative
  EDA config (`5A_aap/eda/`) and the activations health-probe are largely reusable.
- **Traefik** fronts AWX + eda-server (+ the event-stream endpoint) on **:443** by host/path → no extra
  NSG port; `:9443` stays the simulator edge. SSO is **Keycloak** OIDC into AWX **and** eda-server.
- Fits the **D4 (16 GB)**: AWX (~3-4 GB) + eda-server (~2-3 GB) + simulator (~3 GB) ≈ 10-11 GB.

### Decisions (locked)
1. Deploy via **k3s + operators** (awx-operator + eda-server-operator). ✅
2. EDA = **eda-server** (full controller), not ansible-rulebook-only. ✅
3. Registry = **registry:2**; **no galaxy_ng** (collections from public Galaxy). ✅
4. Entry/TLS = **Traefik ingress** (k3s built-in). ✅
5. SSO = **Keycloak** OIDC into AWX + eda-server. ✅
6. Push = eda-server **event stream** exposed via Traefik on :443 (no extra inbound port). ✅

## Reused as-is (no change)

- `playbooks/`, `extensions/eda/rulebooks/`, `collections/requirements.yml` (automation content)
- `simulator/` (the estate), `bootstrap/1_infra` (VM — Ubuntu via `osFamily`), `2_fleet`, `3_keycloak`
- `bootstrap/4_servicenow` — CMDB + account + the `servicenow.itsm.now` dynamic inventory
- `lib/poc.py`, `lib/servicenow.py`; `lib/aap.py` is the **template** for `lib/awx.py`

## Workstreams

| # | Work | Effort | Notes |
|---|---|---|---|
| **A** | **k3s + operators** | medium | ✅ **Done** — k3s + `registry:2` (NodePort 30500) + **AWX** (awx-operator, Traefik ingress on the FQDN, 24.6.1) + **eda-server** (eda-server-operator 1.0.2, NodePort 31080, `/_healthz`=200, drives AWX via `automation_server_url`). Manifests in `k8s/`; `install.sh` self-verifies via `verify.sh`. *Gotcha:* both operators' `kube-rbac-proxy` sidecar (dead `gcr.io` image) remapped to `quay.io/brancz/…` |
| **B** | **Decision Environment** | easy | ✅ **Done** — `eda/execution-environment.yml` builds `snow-eda-de` from **public `ubi9-minimal`** (no RH subscription — the `de-minimal` twin) + `ansible-rulebook`/OpenJDK-17 + `ansible.eda`/`servicenow.itsm`; `eda/build.sh` pushes to **`registry:2`** (`localhost:30500/snow-eda-de:latest`, 856 MB, pullable by activation pods). |
| **C** | **`lib/awx.py`** + wiring | easy | ✅ **Done** — `lib/awx.py` (subclass of `lib.aap.Aap`, `base=/api/v2`, no `eda`); `awx` branch enabled in `runtime.py`; `controller('awx')` validated live against AWX 24.6.1 |
| **D** | **Controller config-as-code** | easy | ✅ **Done** — `controller/configure.py` (the `awx` twin; AAP-only "AAP Config" cred + "Configure EDA" JT dropped). Live: project synced, **CMDB inventory → 9 hosts**, 13 job templates with Target SSH + ServiceNow creds. **Job→fleet connectivity solved**: `ansible_host` = k3s node gateway `10.42.0.1` (inventory var) + the base image's rootless-PAM fix → **ad-hoc `ping` green on all 9**. |
| **E** | **EDA config (activations)** | easy-med | ✅ **Done (pull)** — `eda/configure.py` against eda-server (`/api/eda/v1`): AWX **OAuth token** (not a controller-cred — eda-server uses `awx_token_id` + its `automation_server_url`), DE from `registry:2`, project, **4 pull activations** (incident/self-service/onboarding/monitor). **E2E pull loop validated**: incident → activation → AWX JT → remediation → resolved. *Gotcha:* ubi9-minimal ships no zoneinfo → `servicenow.itsm.records` `ZoneInfoNotFoundError` until the DE pip-installs `tzdata` into python3.11. *(Push/change = phase 2 / F.)* |
| **F** | **Push** | medium | ServiceNow Business Rule → eda-server **event-stream** URL via Traefik (:443, TLS trust). *(pull/self-heal unchanged.)* |
| **G** | **SSO (Keycloak)** | medium | OIDC into AWX (settings/social-auth) **and** eda-server, against the Meridian realm |
| **H** | **`health.py` probes** | medium | replace `c_awx_stub`: AWX (`/api/v2/ping/`, JTs) + eda-server (activations API) + ingress/TLS + SSO |
| **I** | **Docs** | easy | this README + AWX variants in `docs/03/07/08/09` |

## Phasing

- **Phase 1 — controller + pull/self-heal**: A + B + C + D + E(pull/monitor) + H. Proves the seam end-to-end.
- **Phase 2 — push + SSO**: F (event stream via ingress) + G (Keycloak into AWX + eda-server).

📖 Compare with the AAP build → [docs/07 · Build](../../docs/07-build.md) · [docs/08 · Step by step](../../docs/08-steps.md) · the AAP/AWX seam → [docs/09 · Tests](../../docs/09-tests.md).
