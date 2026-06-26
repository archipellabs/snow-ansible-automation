<sub>[↑ Docs map](../README.md#start-here) · [← 09 · Step by step](09-steps.md) · **10 · Tests** · [11 · Notes →](11-notes.md)</sub>

# Tests — health dashboard + scenarios

Two natures, **kept deliberately apart** — and the split is also where the **AAP/AWX portability seam**
lives (only the control-plane probes change per runtime):

- **`tests/health.py`** — a read-only **health dashboard**: *is every component alive right now?*
- **`tests/scenarios/`** — real **functional tests**: *does each end-to-end flow actually work?*

## `health.py` — the live health dashboard

Stdlib-only, **read-only** (it breaks nothing), **safe to loop**. Probes run **concurrently**
(they're I/O-bound) so a full pass is ≈ the slowest probe, not the sum; every call is bounded to **8 s**
so a dead component never freezes the board. Exit `0` when all non-skipped checks pass.

```bash
python3 tests/health.py                    # one pass
python3 tests/health.py --watch [seconds]  # live dashboard, redraws (default 10s)
python3 tests/health.py --only sso         # just the checks whose name matches
python3 tests/health.py --runtime aap|awx  # control-plane probes (default $RUNTIME or aap)
```

**Three groups** (🟢 pass · 🔴 fail · ⚪ skipped):

| Group | Probes |
|---|---|
| **ServiceNow** | auth (`eda.integration`) · EDA account + pull filter (active · TZ GMT · Auto-Remediation) · push Business Rules |
| **Stack** | targets sshd · `hr-portal` `/health` · Keycloak realm `meridian` · **Vault (secret store)** · hr-portal SSO wired · hr-portal SSO login |
| **Control plane · `aap`** | gateway · controller API · subscription · EDA activations (all 5) · EE → targets (ad-hoc ping) · AAP SSO offered · AAP SSO login |
| **Control plane · `awx`** | AWX control plane · controller config (13 JTs · 9 hosts) · eda-server activations (4) · AWX → targets (ad-hoc ping) · AWX SSO offered |

**Light vs deep.** Most probes are *light* (run every tick). A few are **deep** — they launch real
work or a full login flow (the per-runtime ad-hoc fleet ping, the admin-SSO login, the hr-portal SSO
login) — so they're **skipped (⚪) under `--watch`** and only run in a one-pass.

**The AAP / AWX seam.** *ServiceNow* and *Stack* are runtime-agnostic; only the **Control plane** group
swaps per `--runtime` (`aap` → the 7 AAP probes; `awx` → the 5 AWX probes). That single boundary —
isolated in `lib/runtime.py` — is the whole portability story (see [03 · Architecture](03-architecture.md)).

## `tests/scenarios/` — functional tests

Real end-to-end tests, **numbered in run order**, each **re-runnable** and printing **PASS/FAIL**. Run
one at a time:

```bash
python3 tests/scenarios/1_pull_incident_remediation.py
```

| scenario | proves |
|---|---|
| `1_pull_incident_remediation.py` | the full **pull** chain — EDA itself auto-launches the job → resolved |
| `2_push_change_execution.py` | the full **push** chain — approve → EDA executes via the Event Stream → work note |
| `3_monitor_selfheal.py` | the self-driving loop — inject a fault → the monitor opens the incident → remediation |
| `4_selfservice_restart.py` | the self-service catalog — order "Restart a service" → push → request closed |
| `5_employee_onboarding.py` | onboarding — order "Onboard a new employee" → push → Keycloak user created |
| `6_collect_diagnostics.py` | the read-only diagnostics job → work note on the incident |
| `7_db_admin_lifecycle.py` | the DB-admin lifecycle — migration → role → status → backup on `hr-db-01` |

> The scenarios leave their test incidents / changes / request items in the PDI (no cleanup) — a known
> limitation, see [11 · Notes](11-notes.md).

---
<sub>[↑ Docs map](../README.md#start-here) · [← 09 · Step by step](09-steps.md) · **10 · Tests** · [11 · Notes →](11-notes.md)</sub>
