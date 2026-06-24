# bootstrap/ — stand up & configure the platform

Everything you run **once** to provision and wire the PoC. The subfolders are **numbered by the
theoretical run order** (they're idempotent and largely independent, so it's a guide, not a hard
sequence):

1. **`1_infra/`** — the Azure VM as Bicep.
2. **`2_fleet/`** — deploy the target fleet + the `Target SSH` key (definition in `../simulator/`).
3. **`3_keycloak/`** — the Meridian IdP as code (optional SSO layer).
4. **`4_servicenow/`** — the ServiceNow objects (numbered `1_account` → `4_catalog`).
5. **`5A_aap/`** — install AAP + config-as-code (`controller/`, `eda/`) + the DE build.
   · **`5B_awx/`** — the open-source AWX alternative to `5A_aap` (placeholder).

📖 **From zero to running → [docs/07-build.md](../docs/07-build.md)** · every command, step by step →
[docs/08-steps.md](../docs/08-steps.md) · what each script provisions →
[docs/03-architecture.md](../docs/03-architecture.md).
