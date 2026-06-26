<sub>[↑ Docs map](../README.md#start-here) · [← 09 · Step by step](09-steps.md) · **10 · Tests** · [11 · Notes →](11-notes.md)</sub>

# Tests — health dashboard + scenarios

Two natures, **kept deliberately apart** — and the split is also where the **AAP/AWX portability seam**
lives (only the control-plane probes change per runtime):

- **`tests/health.py`** — a read-only **health dashboard**: *is every component alive right now?*
- **`tests/scenarios/`** — real **functional tests**: *does each end-to-end flow actually work?*

## `health.py` — the live health dashboard

Stdlib-only, **read-only** (it breaks nothing), **safe to loop**. Probes run **concurrently**
(they're I/O-bound) so a full pass is ≈ the slowest probe, not the sum; the liveness probes fail fast
(bounded to **8 s**) so a dead component never freezes the board, while the deep ones run to job/login
completion. Exit `0` when all non-skipped checks pass.

```bash
python3 tests/health.py                    # one pass
python3 tests/health.py --watch [seconds]  # live dashboard, redraws (default 30s)
python3 tests/health.py --only sso         # just the checks whose name matches
python3 tests/health.py --json             # machine-readable one pass (tooling; not with --watch)
python3 tests/health.py --runtime aap|awx  # control-plane probes (default $RUNTIME or aap)
```

![The `health.py` dashboard — one section per build phase, here a fully-green AAP](images/screenshot-tests.png)

**Six groups — one per build phase** (🟢 pass · 🔴 fail · ⚪ optional, not deployed). They mirror
[08 · Build](08-build.md) by name and order, so a green **Step N** reads straight off as a green group:

| Group (build phase) | Probes |
|---|---|
| **1 · Infra** | **VM up** — SSH · cloud-init · `podman-compose` · grown `/home` |
| **2 · Fleet** | targets sshd · `hr-portal` `/health` |
| **3 · Identity** | **Keycloak up** (service) · **realm seeded** (`meridian`) · hr-portal SSO wired · hr-portal SSO login |
| **4 · Secrets** | **Vault up** (unsealed) · **Vault seeded** (`secret/meridian/*`) |
| **5 · ServiceNow** | **instance up** (admin reachable) · auth (`eda.integration`) · EDA account + pull filter (active · TZ GMT · Auto-Remediation) · **CMDB loaded** (9 server CIs) · push Business Rules (AAP-only — ⚪ on AWX) |
| **6 · Ansible controller · `aap`** | gateway · controller API · subscription · EDA activations (all 5) · EE → targets (ad-hoc ping) · AAP SSO offered · AAP SSO login |
| **6 · Ansible controller · `awx`** | AWX control plane · controller config (13 JTs · 9 hosts) · eda-server activations (4) · AWX → targets (ad-hoc ping) · AWX SSO offered |

**Every probe runs every pass.** A few are **deep** — they launch real work or a full login flow (the
per-runtime ad-hoc fleet ping, the admin-SSO login, the hr-portal SSO login). They run in both one-pass
and `--watch`, so a live board exercises the whole loop — which is why `--watch` defaults to a **30 s**
cadence (each cycle fires those jobs + logins).

**Colours read as build progress.** A row is **🔴** for anything that should exist but isn't built yet,
so the board starts mostly red and greens up as you complete each step. **⚪** is reserved for an
*optional* component you chose not to deploy — Vault, or the controller-admin SSO federation (Step 6e) —
and never counts as a failure (exit stays `0`).

**The AAP / AWX seam.** Phases **1–5** are runtime-agnostic; only **6 · Ansible controller**
swaps per `--runtime` (`aap` → the 7 AAP probes; `awx` → the 5 AWX probes). That single boundary —
isolated in `lib/runtime.py` — is the whole portability story (see [03 · Architecture](03-architecture.md)).

## `tests/scenarios/` — functional tests

Real end-to-end tests, **numbered in run order**, each **re-runnable** and printing **PASS/FAIL**. Run
one at a time:

```bash
python3 tests/scenarios/1_pull_incident_remediation.py
```

…or run the whole suite as the **`7 · Scenarios`** group: `python3 tests/health.py --scenarios` (one-pass —
they mutate state, so it rejects `--watch`; works with `--json`, and per-scenario progress streams to
**stderr** as each finishes, capped at **120 s** each). It runs **sequentially** by default (shared PDI +
fleet); add **`--parallel`** to run concurrently — faster, but the monitor scenario can react to another's
restart (occasional flakiness). On `--runtime awx` it **skips the push scenario** (⚪ — push is AAP-only).

![`health.py --scenarios` — the 7 · Scenarios group, all green](images/screenshot-scenarios.png)

| scenario | proves |
|---|---|
| `1_pull_incident_remediation.py` | the full **pull** chain — EDA itself auto-launches the job → resolved |
| `2_push_change_execution.py` | the full **push** chain — approve → EDA executes via the Event Stream → work note · **AAP-only** (⚪ on AWX) |
| `3_monitor_selfheal.py` | the self-driving loop — inject a fault → the monitor opens the incident → remediation |
| `4_selfservice_restart.py` | the self-service catalog — order "Restart a service" → push → request closed |
| `5_employee_onboarding.py` | onboarding — order "Onboard a new employee" → push → Keycloak user created |
| `6_collect_diagnostics.py` | the read-only diagnostics job → work note on the incident |
| `7_db_admin_lifecycle.py` | the DB-admin lifecycle — migration → role → status → backup on `hr-db-01` |

> The scenarios leave their test incidents / changes / request items in the PDI (no cleanup) — a known
> limitation, see [11 · Notes](11-notes.md).

---
<sub>[↑ Docs map](../README.md#start-here) · [← 09 · Step by step](09-steps.md) · **10 · Tests** · [11 · Notes →](11-notes.md)</sub>
