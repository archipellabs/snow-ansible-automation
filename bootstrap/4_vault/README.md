# vault/ — seed Meridian's HashiCorp Vault

`seed.py` writes the platform's secrets from `.env` into **HashiCorp Vault** (KV v2), so the controller
resolves them at **job runtime** (native lookup) and **no secret is stored in AAP/AWX**. Vault itself runs
in the simulator stack (`simulator/compose.yml`, dev mode), deployed with the fleet.

```bash
ssh -fNL 8200:localhost:8200 azureuser@$AAP_FQDN   # tunnel to the estate's :8200 (or $AWX_FQDN)
python3 bootstrap/4_vault/seed.py                  # write the SN + Keycloak + SSH secrets into Vault
```

Seeds `secret/meridian/{servicenow,keycloak,ssh}`. The controllers' lookup wiring lives in their
`controller/configure.py` (via `lib/vaultwire.py`); the client is `lib/vault.py`.

> **Dev mode is in-memory** — a Vault/VM restart loses the secrets, so **re-run `seed.py`** afterwards
> (else jobs fail, since the credentials source from Vault). Token auth; AppRole is the production path.

📖 The runtime integration → [docs/05 · AAP vs AWX](../../docs/05-aap-vs-awx.md) · the design + gotcha →
[docs/11 · Notes §17](../../docs/11-notes.md).
