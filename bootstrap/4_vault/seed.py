#!/usr/bin/env python3
"""Step 3 (Vault) — seed Meridian's HashiCorp Vault with the secrets AAP/AWX consume at job runtime.

Reads the secrets from `.env` and writes them into Vault (KV v2) so **Vault becomes the single source
of truth**. The controllers don't read them from here — they resolve them live via the native
HashiCorp Vault credential lookup, wired by controller/configure.py. Idempotent (a KV put overwrites).
Run after the Vault container is up (simulator/compose.yml). From the repo root:

  python3 bootstrap/4_vault/seed.py

Reach Vault via `VAULT_ADDR` (defaults to http://localhost:8200). For a remote estate, open a tunnel:
  ssh -fNL 8200:localhost:8200 azureuser@$AAP_FQDN    # or $AWX_FQDN
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv  # noqa: E402
from lib import vault  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_EDA_USERNAME", "SN_EDA_PASSWORD", "VAULT_TOKEN"))
os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")

# What the platform consumes at runtime. Path -> flat key/value (KV v2). The controllers' credentials
# point each field here (ServiceNow PDI -> meridian/servicenow, Keycloak Provisioner -> meridian/keycloak).
SECRETS = {
    "meridian/servicenow": {
        "host": "https://" + os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/"),
        "username": os.environ["SN_EDA_USERNAME"],
        "password": os.environ["SN_EDA_PASSWORD"],
    },
    "meridian/keycloak": {
        "client": "aap-provisioner",
        "secret": os.environ.get("KC_PROVISIONER_SECRET", ""),
    },
}

# The Target SSH private key (the fleet machine credential) lives as a file, not an .env value — seed it
# too so the controller's Machine credential can source `ssh_key_data` from Vault like the rest.
_key = os.path.join(ROOT, "bootstrap", "2_fleet", "keys", "target_key")
if os.path.isfile(_key):
    SECRETS["meridian/ssh"] = {"private_key": open(_key).read()}


def main():
    for path, data in SECRETS.items():
        vault.kv_put(path, data)
        print(f"+ wrote {vault.mount()}/{path}  ({', '.join(sorted(data))})")
    print(f"\n>> {len(SECRETS)} secrets in Vault at {os.environ['VAULT_ADDR']}")


if __name__ == "__main__":
    main()
