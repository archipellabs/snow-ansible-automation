"""Runtime selector — picks the automation control-plane client by runtime so the tests and config
stay portable across control planes: AAP (bootstrap/6A_aap/) and AWX (bootstrap/6B_awx/).

Pick the runtime with the RUNTIME env var or an explicit argument; default 'aap'.
  aap  -> lib.aap.Aap   (Platform Gateway, /api/controller/v2, /api/eda/v1)
  awx  -> lib.awx.Awx   (bare AWX controller, /api/v2; EDA is a separate eda-server)
"""
import os
import sys

from lib.aap import Aap

RUNTIMES = ("aap", "awx")


def runtime_name(explicit=None):
    rt = (explicit or os.environ.get("RUNTIME") or "aap").lower()
    if rt not in RUNTIMES:
        sys.exit(f"unknown RUNTIME {rt!r} (expected one of {', '.join(RUNTIMES)})")
    return rt


def controller(runtime=None):
    """Return the control-plane client for the selected runtime (default 'aap')."""
    rt = runtime_name(runtime)
    if rt == "aap":
        return Aap()
    from lib.awx import Awx
    return Awx()


def fqdn(runtime=None):
    """The estate / control-plane host for the selected runtime — AAP_FQDN or AWX_FQDN.

    The simulator (estate) runs on whichever VM hosts the control plane, so its public host is the
    runtime's FQDN. Used by the estate-facing config (Keycloak) and the runtime-aware deploy scripts."""
    rt = runtime_name(runtime)
    return os.environ["AWX_FQDN" if rt == "awx" else "AAP_FQDN"]
