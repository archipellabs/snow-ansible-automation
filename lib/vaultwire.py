"""Wire controller credentials' secret fields to HashiCorp Vault (AAP and AWX share this REST API).

Both controllers' configure.py call `wire()` so the Vault plumbing lives in one place: ensure the
"Meridian Vault" lookup credential (token auth, KV v2), then for each (credential, field) attach a
`credential_input_source` pointing at a Vault path **and remove the field's literal** from the
credential — so Vault is the only source (idempotent, and it also cleans up a pre-Vault literal).

`api(method, path, body=None)` is the caller's REST helper; `org` the organization id; `vault_url`
the in-host Vault address (`host.containers.internal:8200` for AAP, the k3s node gateway
`10.42.0.1:8200` for AWX). Returns the lookup credential id, or None if the managed lookup credential
type is absent (Vault wiring skipped). Token auth is dev-mode; AppRole is the production path.
"""
import os
import urllib.parse

LOOKUP_TYPE = "HashiCorp Vault Secret Lookup"
LOOKUP_NAME = "Meridian Vault"


def wire(api, org, vault_url, mappings):
    """mappings: list of (target_cred, field, vault_path, vault_key)."""
    mount = os.environ.get("VAULT_KV_MOUNT", "secret")
    vt = api("GET", "credential_types/?" + urllib.parse.urlencode({"name": LOOKUP_TYPE}))
    if not vt.get("count"):
        print(f"= '{LOOKUP_TYPE}' credential type missing — skipping Vault wiring")
        return None
    inputs = {"url": vault_url, "token": os.environ["VAULT_TOKEN"], "api_version": "v2"}
    found = api("GET", "credentials/?" + urllib.parse.urlencode({"name": LOOKUP_NAME}))
    if found.get("count"):
        vid = found["results"][0]["id"]
        api("PATCH", f"credentials/{vid}/", {"inputs": inputs})        # reconcile url/token on re-run
        print(f"= Credential {LOOKUP_NAME} (lookup) exists (id={vid})")
    else:
        vid = api("POST", "credentials/", {"name": LOOKUP_NAME, "organization": org,
                  "credential_type": vt["results"][0]["id"], "inputs": inputs})["id"]
        print(f"+ Credential {LOOKUP_NAME} (lookup) created (id={vid})")
    for target, field, path, key in mappings:
        meta = {"secret_backend": mount, "secret_path": path, "secret_key": key}
        q = urllib.parse.urlencode({"target_credential": target["id"], "input_field_name": field})
        ex = api("GET", f"credential_input_sources/?{q}")
        if ex.get("count"):
            api("PATCH", f"credential_input_sources/{ex['results'][0]['id']}/",
                {"metadata": meta, "source_credential": vid})
            verb = "="
        else:
            api("POST", "credential_input_sources/", {"target_credential": target["id"],
                "source_credential": vid, "input_field_name": field, "metadata": meta})
            verb = "+"
        # Remove any literal for the now-sourced field so Vault is the only source (no-op if absent).
        cur = api("GET", f"credentials/{target['id']}/").get("inputs", {})
        if field in cur:
            cur.pop(field)
            api("PATCH", f"credentials/{target['id']}/", {"inputs": cur})
        print(f"{verb} input source {target['name']}.{field} <- {mount}/{path}#{key}")
    return vid
