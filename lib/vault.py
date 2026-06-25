"""HashiCorp Vault KV v2 client for the PoC (stdlib only).

Meridian runs Vault in the simulator stack (dev mode). The bootstrap **seeds** secrets here, and the
controllers (AAP/AWX) resolve them at **job runtime** via the native HashiCorp Vault credential lookup
— so this tiny client is only used by the seed step + tests, never on the runtime path.

Reads `VAULT_ADDR` (e.g. http://host:8200) and `VAULT_TOKEN` from the environment. The KV v2 mount
defaults to `secret` (override with `VAULT_KV_MOUNT`). Token auth only (dev mode); AppRole is the
production path. Sits on top of lib.poc (transport).
"""
import os

from lib.poc import http_json


def addr():
    return os.environ["VAULT_ADDR"].rstrip("/")


def mount():
    return os.environ.get("VAULT_KV_MOUNT", "secret")


def _headers():
    return {"X-Vault-Token": os.environ["VAULT_TOKEN"]}


def kv_put(path, data, ctx=None):
    """Write a KV v2 secret at <mount>/<path>; `data` is a flat dict of key -> value."""
    url = f"{addr()}/v1/{mount()}/data/{path}"
    return http_json(url, method="POST", headers=_headers(), body={"data": data}, ctx=ctx)


def kv_get(path, ctx=None):
    """Read a KV v2 secret; returns the inner data dict ({} if the secret is absent)."""
    url = f"{addr()}/v1/{mount()}/data/{path}"
    try:
        return http_json(url, headers=_headers(), ctx=ctx).get("data", {}).get("data", {})
    except Exception:
        return {}
