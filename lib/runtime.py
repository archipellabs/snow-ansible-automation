"""Runtime selector — picks the automation control-plane client by runtime so the tests stay
portable across control planes. Today only AAP is provisioned (bootstrap/5A_aap/); AWX (bootstrap/5B_awx/)
is a placeholder, so its client is a clearly-marked stub.

Pick the runtime with the RUNTIME env var or an explicit argument; default 'aap'. To enable AWX later,
add lib/awx.py exposing the same interface as lib.aap.Aap (call / jt_id / wait_job / run_jt /
recent_jobs / wait_triggered_job) and wire it into controller() below.
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
    # awx: add lib/awx.py with the Aap interface, then `from lib.awx import Awx; return Awx()`.
    sys.exit("RUNTIME=awx is not available yet — bootstrap/5B_awx/ is a placeholder (see README roadmap)")
