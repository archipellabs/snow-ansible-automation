# bootstrap/ — stand up & configure the platform

Everything you run **once** to provision and wire the PoC. The subfolders are **numbered by the
theoretical run order** (they're idempotent and largely independent, so it's a guide, not a hard
sequence):

1. **`1_infra/`** — the Azure VM as Bicep.
2. **`2_fleet/`** — deploy the target fleet + the `Target SSH` key (definition in `../simulator/`).
3. **`3_keycloak/`** — the Meridian IdP as code (optional SSO layer).
4. **`4_vault/`** — seed Meridian's HashiCorp Vault (the platform's secret store).
5. **`5_servicenow/`** — the ServiceNow objects (numbered `1_account` → `4_push_change_aap`).
6. **`6A_aap/`** — install AAP + config-as-code (`controller/`, `eda/`) + the DE build.
   · **`6B_awx/`** — the open-source AWX alternative to `6A_aap`.

📖 **From zero to running → [docs/08-build.md](../docs/08-build.md)** · every command, step by step →
[docs/09-steps.md](../docs/09-steps.md) · what each script provisions →
[docs/03-architecture.md](../docs/03-architecture.md).
