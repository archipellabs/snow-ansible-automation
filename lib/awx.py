"""AWX layer — the Automation Controller client for the `awx` runtime.

AWX is the upstream open-source of AAP's controller and shares the same REST API, so this is just
lib.aap.Aap with the bare `/api/v2` base (no Platform Gateway, no `/api/controller/`) and AWX admin
creds. EDA is NOT on the controller here — it lives in a separate eda-server — so there is no `eda`
endpoint on this client (the eda-server API is reached separately; see tests/health.py).

Reads env: AWX_FQDN, AWX_ADMIN_USER (default 'admin'), AWX_ADMIN_PASSWORD.
"""
import os

from lib.aap import Aap
from lib.poc import insecure_ctx, basic_auth


class Awx(Aap):
    """AWX controller client — same interface as Aap, against `/api/v2` (Traefik self-signed cert)."""

    def __init__(self):
        self.fqdn = os.environ["AWX_FQDN"]
        self.ctx = insecure_ctx()
        self.base = f"https://{self.fqdn}/api/v2"
        self.eda = None  # AWX has no bundled EDA — eda-server is a separate service
        self.h = {"Authorization": basic_auth(os.environ.get("AWX_ADMIN_USER", "admin"),
                                              os.environ["AWX_ADMIN_PASSWORD"])}
