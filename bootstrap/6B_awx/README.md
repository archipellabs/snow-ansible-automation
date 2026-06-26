# 6B_awx/ — AWX variant

The fully open-source alternative to [`../6A_aap/`](../6A_aap/) — same functionality, no AAP, no
subscription. **Status: complete and validated end-to-end** (`tests/health.py --runtime awx` all green;
all pull scenarios pass) — **bar the push pattern**, which is AAP-only. The runtime seam
(`lib/runtime.py`, `tests/health.py --runtime aap|awx`) lets most of the project be reused as-is. For the
full runtime comparison and how to choose, see **[docs/05 · AAP vs AWX](../../docs/05-aap-vs-awx.md)**.

## The core gap — AWX ≈ Automation Controller only

AAP bundles things AWX does **not** ship. We fill each with its OSS upstream:

| AAP brick | OSS replacement |
|---|---|
| Automation Controller | **AWX** (awx-operator on k3s) |
| EDA Controller | **eda-server** (eda-server-operator) — restores activations + event streams + the `/api/eda/v1` API |
| Platform Gateway (`:443`, routing, SSO, event streams) | **k3s Traefik ingress** + TLS (host/path routing) |
| Automation Hub (private registry) | **`registry:2`** in-cluster for the DE image; collections pulled from **public Galaxy** at project sync |
| Subscription / manifest | — (free) |

> Load-bearing detail: the rulebook's controller credential host is **`/api/v2/`** for AWX (AAP needed
> `/api/controller/`). See [docs/11 · Notes](../../docs/11-notes.md).

## Architecture

**k3s + AWX + eda-server + registry:2 + Traefik ingress/TLS + Keycloak SSO**, on the Ubuntu/D4 VM.

- Choosing **eda-server** (not raw `ansible-rulebook`) gives the **same EDA API as AAP** → the declarative
  EDA config (`6A_aap/eda/`) and the activations health-probe are largely reusable.
- **Traefik** fronts AWX + eda-server (+ the event-stream endpoint) on **:443** by host/path → no extra
  NSG port; `:9443` stays the simulator edge. SSO is **Keycloak** OIDC into AWX **and** eda-server.
- Fits the **D4 (16 GB)**: AWX (~3-4 GB) + eda-server (~2-3 GB) + simulator (~3 GB) ≈ 10-11 GB.

### Decisions
1. Deploy via **k3s + operators** (awx-operator + eda-server-operator). ✅
2. EDA = **eda-server** (full controller), not ansible-rulebook-only. ✅
3. Registry = **registry:2**; **no galaxy_ng** (collections from public Galaxy). ✅
4. Entry/TLS = **Traefik ingress** (k3s built-in). ✅
5. SSO = **Keycloak** OIDC into AWX + eda-server. ✅
6. Push = **AAP-only** ⛔ — the original "event stream via Traefik" plan didn't hold: eda-server 1.0.2 has
   no event-stream/webhook ingress (see workstream F). On AWX every flow is pull; broker alternative deferred.

### Secrets — HashiCorp Vault

Like AAP, the AWX controller sources its credentials' secrets from **HashiCorp Vault** at job runtime
(native lookup — AWX is the controller upstream, so this is at parity). eda-server has no
credential-lookup, so its activation `SN_*` are read from Vault at config time. See
**[docs/11 · Notes §17](../../docs/11-notes.md)**.

## Reused as-is (no change)

- `playbooks/`, `extensions/eda/rulebooks/`, `collections/requirements.yml` (automation content)
- `simulator/` (the estate), `bootstrap/1_infra` (VM — Ubuntu via `osFamily`), `2_fleet`, `3_keycloak`
- `bootstrap/5_servicenow` — CMDB + account + the `servicenow.itsm.now` dynamic inventory
- `lib/poc.py`, `lib/servicenow.py`; `lib/aap.py` is the **template** for `lib/awx.py`

## What's built

Everything, validated end-to-end (`health.py --runtime awx` all green; all pull scenarios pass) — **bar
push** (AAP-only, see below):

- **k3s + operators** — awx-operator (AWX 24.6.1, Traefik ingress on the FQDN) + eda-server-operator
  (1.0.2, `/api/eda/v1` on NodePort 31080) + `registry:2` (NodePort 30500); `install.sh` self-verifies.
- **Decision Environment** — `snow-eda-de` built from public `ubi9-minimal` (the `de-minimal` twin),
  pushed to `registry:2`.
- **Config-as-code** — `controller/configure.py` (the AWX twin: CMDB inventory → 9 hosts, 13 JTs) +
  `eda/configure.py` against eda-server (AWX **OAuth token**, not a controller credential; 4 pull activations).
- **Secrets · SSO · tests** — Vault lookup (parity with AAP), Keycloak OIDC into AWX, runtime-aware
  `health.py` probes — all green.

**Push (change) is AAP-only** ⛔ — eda-server has no event-stream ingress; the broker alternative is
deferred. The build's real gotchas (dead `kube-rbac-proxy` image, `ubi9-minimal` missing tzdata, rootless
PAM) and the honest comparison are in [docs/05 · AAP vs AWX](../../docs/05-aap-vs-awx.md) and
[docs/11 · Notes §14–17](../../docs/11-notes.md).

📖 The runtime comparison → **[docs/05 · AAP vs AWX](../../docs/05-aap-vs-awx.md)** · the AAP build → [docs/08 · Build](../../docs/08-build.md) · the AAP/AWX seam → [docs/10 · Tests](../../docs/10-tests.md).
