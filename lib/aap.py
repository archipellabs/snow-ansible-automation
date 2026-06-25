"""AAP layer for the PoC (stdlib only) — the controller/EDA client used by the tests to look up,
launch and poll jobs (and watch EDA-triggered jobs). Sits on top of lib.poc (transport).

The bootstrap controller config (bootstrap/5A_aap/controller/configure.py) keeps its own create-heavy
`get_or_create` client; this one is the read/launch/poll client the tests share.
"""
import os
import sys
import time
import urllib.parse

from lib.poc import insecure_ctx, basic_auth, http_json

JOB_TERMINAL = ("successful", "failed", "error", "canceled")


class Aap:
    """AAP controller + EDA client (admin creds; the gateway has a self-signed cert)."""

    def __init__(self):
        self.fqdn = os.environ["AAP_FQDN"]
        self.ctx = insecure_ctx()
        self.base = f"https://{self.fqdn}/api/controller/v2"
        self.eda = f"https://{self.fqdn}/api/eda/v1"
        self.h = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}

    def call(self, path, body=None, method=None, timeout=30):
        """GET (or POST when body is given; pass method to override). `path` is controller-relative
        unless it's an absolute URL (e.g. an EDA endpoint built from self.eda). `timeout` bounds the
        HTTP call (lower it for liveness probes so a hung control plane fails fast)."""
        url = path if path.startswith("http") else f"{self.base}/{path}"
        return http_json(url, method=method or ("POST" if body is not None else "GET"),
                         headers=self.h, body=body, ctx=self.ctx, timeout=timeout)

    def jt_id(self, name):
        res = self.call("job_templates/?" + urllib.parse.urlencode({"name": name}))["results"]
        if not res:
            sys.exit(f"job template {name!r} not found — run bootstrap/5A_aap/controller/configure.py")
        return res[0]["id"]

    def wait_job(self, jid, timeout=160, interval=4):
        status = "pending"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.call(f"jobs/{jid}/")["status"]
            if status in JOB_TERMINAL:
                break
            time.sleep(interval)
        return status

    def run_jt(self, name, extra_vars, timeout=160):
        """Launch a job template and wait for it; returns (job_id, terminal_status)."""
        jid = self.call(f"job_templates/{self.jt_id(name)}/launch/", {"extra_vars": extra_vars})["id"]
        return jid, self.wait_job(jid, timeout)

    def recent_jobs(self, jt_id, page_size=20):
        q = urllib.parse.urlencode({"order_by": "-id", "page_size": page_size})
        return self.call(f"job_templates/{jt_id}/jobs/?{q}").get("results", [])

    def wait_triggered_job(self, jt_id, baseline, marker, timeout=180, interval=5):
        """Wait for EDA to auto-launch a NEW job (id > baseline) whose extra_vars carry `marker`
        (e.g. the incident/change number). Returns the job dict, or None on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(interval)
            for j in self.recent_jobs(jt_id):
                if j["id"] > baseline and marker in (j.get("extra_vars") or ""):
                    return j
        return None
