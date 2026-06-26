#!/usr/bin/env python3
"""Holistic health dashboard for the PoC stack (stdlib only) — read-only, safe to loop.

  python3 tests/health.py                    # one pass
  python3 tests/health.py --watch [seconds]  # live dashboard, redraws on interval (default 30s)
  python3 tests/health.py --json             # machine-readable one pass (not compatible with --watch)
  python3 tests/health.py --scenarios        # run the functional scenarios as group 7 (one-pass; mutates state)
  python3 tests/health.py --runtime aap|awx  # control-plane probes (default $RUNTIME or aap)

This is not a scenario (it breaks nothing): it answers "is every component alive right now?". The probe
groups mirror the build phases (docs/08-build.md) — Infra, Fleet, Identity, Secrets, ServiceNow, Ansible
controller — so a green Step N reads off one section. Steps 1–5 are runtime-agnostic; only the Ansible
controller group (Step 6) differs per runtime. Exit 0 when every required check passes; ⚪ marks an
optional component you didn't deploy (Vault, admin SSO) and never counts as a failure.
"""
import html
import http.client
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh, insecure_ctx, basic_auth, http_json  # noqa: E402
from lib.runtime import controller, runtime_name, fqdn  # noqa: E402
from lib.servicenow import Snow  # noqa: E402
import lib.poc as _poc                                   # noqa: E402
_poc.QUIET = True   # the dashboard reports failures itself — suppress http_json's stderr error dump

INSECURE = insecure_ctx()
EST = None   # estate host (the VM running the simulator) for the runtime under test; set in run_all()
RT = None    # the runtime under test ("aap"|"awx"); set in run_all() so shared probes can be runtime-aware
EDA_ACTIVATIONS = {"pull-incident-remediation", "monitor-health", "push-change-execution",
                   "pull-selfservice-restart", "pull-onboarding"}
EMOJI = {True: "🟢", False: "🔴", None: "⚪"}   # 🟢 ok · 🔴 fail (incl. not-built-yet) · ⚪ optional component not deployed
PROBE_TIMEOUT = 8   # seconds — a probe must fail fast so the dashboard never freezes on a dead component
SSO_USER = "nadia.haddad"   # a DSI/DBA staff member -> IT-Admins group -> superuser (for the deep AAP login)

USAGE = """tests/health.py — read-only health dashboard for the PoC stack.

  python3 tests/health.py [--runtime aap|awx] [--only <substr>] [--scenarios [--parallel]] [--watch [seconds] | --json]

Options:
  --runtime aap|awx   which control-plane probes to run (default: $RUNTIME, else aap)
  --only <substr>     run only checks whose name contains <substr> (case-insensitive) —
                      e.g. --only vault · --only sso · --only "vm up"
  --watch [seconds]   live dashboard, redraws every <seconds> (default 30). Runs every probe,
                      including the deep ones — each cycle launches the ad-hoc ping jobs + SSO logins
  --json              machine-readable one-pass JSON to stdout; mutually exclusive with --watch.
                      Shape: {runtime, ok, ts, elapsed_s, summary:{pass,fail,optional}, groups:[{group,
                      checks:[{name, state:pass|fail|optional, ok, info, secs}]}]}. Exit 0 iff ok.
  --scenarios         run tests/scenarios/ as the '7 · Scenarios' group instead of the liveness probes —
                      they MUTATE state, so one-pass only (rejects --watch); works with --json. Progress
                      streams to stderr as each finishes. On AWX the push-change scenario is skipped
                      (⚪, AAP-only). Each scenario is capped at a 120 s timeout.
  --parallel          (with --scenarios) run them concurrently instead of sequentially — faster, but they
                      share the one PDI + fleet, so the monitor scenario can react to another's restart
                      (occasional flakiness). Sequential is the reliable default.
  -h, --help          show this help and exit

Probe groups mirror the build phases (docs/08-build.md) · 🟢 pass · 🔴 fail (incl. not built yet) · ⚪ optional, not deployed:
  1 · Infra               the estate VM — SSH · cloud-init · podman-compose · grown /home
  2 · Fleet               the target servers (sshd) + the Meridian apps
  3 · Identity            Keycloak up (service) + realm seeded + hr-portal SSO (wired · login)
  4 · Secrets             Vault up (unsealed) + seeded (secret/meridian/*)
  5 · ServiceNow          the instance is up, the integration account + pull filter, the CMDB, the push Business Rules
  6 · Ansible controller  the runtime — aap (7 probes) or awx (5 probes)

Read-only and safe to loop. Exit 0 if every required check passes (⚪ optional rows never fail), else 1."""


def req(url, user=None, pw=None, insecure=True, timeout=PROBE_TIMEOUT):
    r = urllib.request.Request(url)
    if user is not None:
        r.add_header("Authorization", basic_auth(user, pw))
    resp = urllib.request.urlopen(r, context=INSECURE if insecure else None, timeout=timeout)
    raw = resp.read()
    try:
        return resp.status, (json.loads(raw) if raw else {})
    except ValueError:
        return resp.status, raw.decode(errors="replace")


# --- component probes: read os.environ, return (ok, info); ok is None => SKIP --------------------

# ServiceNow — instance reachable + the integration wired both ways (pull filter + push Business Rules)

def c_sn_up():
    """State 1 — the ServiceNow instance is up and reachable with admin creds, independent of the
    eda.integration account. The PDI hibernates when idle and its admin password rotates on reset, so this
    isolates 'instance down / SN_PASS stale' (a Step 5 prerequisite) from 'integration not configured yet'."""
    inst = os.environ.get("SN_INSTANCE")
    if not inst:
        return False, "SN_INSTANCE not set in .env"
    try:
        st, _ = req(f"https://{inst}/api/now/table/sys_user?sysparm_limit=1",
                    os.environ.get("SN_USER"), os.environ.get("SN_PASS"), insecure=False)
    except urllib.error.HTTPError as e:
        return False, ("reachable, admin auth rejected (SN_USER/SN_PASS stale — PDI reset?)"
                       if e.code == 401 else f"HTTP {e.code}")
    except Exception as e:
        return False, f"unreachable ({type(e).__name__}) — PDI hibernating?"
    return st == 200, f"up · {inst}"


def c_sn_auth():
    # eda.integration's own auth — proves the instance is awake AND the integration account works.
    st, _ = req(f"https://{os.environ['SN_INSTANCE']}/api/now/table/incident?sysparm_limit=1",
                os.environ["SN_EDA_USERNAME"], os.environ["SN_EDA_PASSWORD"], insecure=False)
    return st == 200, f"HTTP {st} as {os.environ['SN_EDA_USERNAME']}"


def c_sn_eda_account():
    # The pull-side wiring + the load-bearing GMT footgun: a non-GMT user shifts the poll window and
    # EDA silently never matches new incidents (see README Key findings).
    snow = Snow(creds="admin")
    u = snow.get_one("sys_user", "user_name=eda.integration", fields="active,time_zone")
    if not u:
        return False, "eda.integration user missing"
    grp = snow.get_one("sys_user_group", "name=Auto-Remediation")
    tz = u.get("time_zone") or "∅"
    ok = u.get("active") == "true" and tz == "GMT" and bool(grp)
    return ok, f"active={u.get('active')}, tz={tz}, Auto-Remediation group={'✓' if grp else '✗'}"


def c_sn_cmdb():
    """The Meridian CMDB is loaded — 2_cmdb.py populated the cmdb_ci_linux_server CIs (with the custom
    u_role/u_service/u_ssh_port the dynamic inventory reads). Without these the controller's ServiceNow
    inventory source syncs 0 hosts, so this is the data-side companion to the account check."""
    snow = Snow(creds="admin")
    q = urllib.parse.urlencode({"sysparm_query": "u_roleISNOTEMPTY",
                                "sysparm_fields": "name", "sysparm_limit": "50"})
    res = snow.result(f"table/cmdb_ci_linux_server?{q}")
    return len(res) >= 9, f"{len(res)} Meridian server CIs (cmdb_ci_linux_server, u_role set)"


def c_sn_business_rules():
    # The push trigger: the "EDA - push …" Business Rule for `change` must be active, or the push
    # pattern silently never fires. (Self-service + onboarding are pull now — no Business Rule.)
    # Push is AAP-only — eda-server (AWX) has no event-stream ingress — so this is N/A on AWX (⚪, not 🔴).
    if RT == "awx":
        return None, "n/a — push is AAP-only (eda-server has no event-stream ingress)"
    snow = Snow(creds="admin")
    q = urllib.parse.urlencode({"sysparm_query": "nameSTARTSWITHEDA - push^active=true",
                                "sysparm_fields": "name", "sysparm_limit": "10"})
    res = snow.result(f"table/sys_script?{q}")
    return len(res) >= 1, f"{len(res)} push Business Rule(s) active (change)"


def c_vm():
    """The estate VM is provisioned per cloud-init: reachable over SSH, cloud-init finished, and Step 1's
    base in place — podman + podman-compose installed and the RHEL LVM grown so rootless podman has room
    (/home > 1G). Asserting the last two here means a cloud-init regression fails at Step 1 instead of
    surfacing as a baffling 'no space' / 'podman-compose missing' midway through the Step 2 fleet deploy.
    Goes green right after Step 1 (before the fleet exists) so the build is verifiable step by step. Runs
    ssh directly (not lib.poc.ssh) to capture stderr, so a recreated-VM host-key change is reported as the
    actionable `ssh-keygen -R` fix rather than a misleading 'VM not up'."""
    probe = ("v=$(podman --version 2>/dev/null || echo NOPODMAN); "
             "command -v podman-compose >/dev/null 2>&1 && c=1 || c=0; "
             "h=$(df -m --output=size /home 2>/dev/null | tail -1 | tr -d ' '); "
             'echo "$v|$c|$h"')
    try:
        p = subprocess.run(
            ["ssh", "-i", os.path.expanduser(_poc.SSH_KEY), "-o", "StrictHostKeyChecking=accept-new",
             "-o", "BatchMode=yes", "-o", f"ConnectTimeout={PROBE_TIMEOUT}", f"azureuser@{EST}", probe],
            capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return False, "unreachable (SSH timed out — VM not up yet?)"
    out, err = p.stdout.strip(), p.stderr
    if "HOST IDENTIFICATION HAS CHANGED" in err or "Host key verification failed" in err:
        return False, f"host key changed (VM recreated) — run: ssh-keygen -R {EST}"
    if "NOPODMAN" in out:
        return False, "reachable, cloud-init still running (no podman yet)"
    if not out.startswith("podman version"):
        return False, "unreachable (VM not up / SSH not ready)"
    ver, compose, home = (out.split("|") + ["", ""])[:3]
    home_mb = int(home) if home.isdigit() else 0
    gaps = []
    if compose != "1":
        gaps.append("podman-compose missing")
    if home_mb and home_mb < 4000:
        gaps.append(f"/home {home_mb}MB — LVM not grown (rootless podman will run out)")
    if gaps:
        return False, "up, but cloud-init incomplete — " + "; ".join(gaps)
    extra = f" · /home {home_mb // 1024}G" if home_mb else ""
    return True, f"up · {ver} · compose{extra}"


def c_targets():
    names = ["hr-web-01", "crm-web-01", "ged-01", "hr-db-01", "mail-01"]
    out = ssh("for n in " + " ".join(names) + "; do printf '%s=' $n; "
              "podman exec $n systemctl is-active sshd 2>/dev/null | tr '\\n' ',' ; echo; done",
              fqdn=EST, timeout=25, connect_timeout=PROBE_TIMEOUT)
    down = [n for n in names if f"{n}=active" not in out]
    return not down, f"{len(names) - len(down)}/{len(names)} sshd active" + (f" — down: {', '.join(down)}" if down else "")


def c_hrportal():
    st, d = req(f"https://{EST}:9443/hr/health")
    sso = d.get("sso") if isinstance(d, dict) else None
    return st == 200, f"HTTP {st}, sso={sso}"


def c_keycloak_up():
    """State 1 — Keycloak service responds: its container is up (Step 2 brings it up with the fleet),
    proven via the always-present 'master' realm, i.e. independent of whether 'meridian' is seeded yet."""
    try:
        st, _ = req(f"https://{EST}:9443/auth/realms/master/.well-known/openid-configuration")
    except urllib.error.HTTPError as e:
        st = e.code
    except urllib.error.URLError:
        return False, "not reachable (Keycloak container down? — Step 2 fleet)"
    return st == 200, f"/auth up · master realm HTTP {st}"


def c_keycloak():
    """State 2 — Keycloak realm seeded: the 'meridian' realm exists (Step 3, bootstrap/3_keycloak/configure.py)."""
    try:
        st, _ = req(f"https://{EST}:9443/auth/realms/meridian/.well-known/openid-configuration")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "realm 'meridian' not seeded (run bootstrap/3_keycloak/configure.py)"
        raise
    return st == 200, f"realm 'meridian' HTTP {st}"


def c_vault():
    """Vault liveness — server-side check (:8200 isn't open externally). Optional: graceful-skip when
    Vault isn't deployed. Dev mode is in-memory, so 'unsealed' also implies the seed survived (re-seed
    after a restart with bootstrap/4_vault/seed.py)."""
    out = ssh("podman exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault status -format=json 2>/dev/null || true",
              fqdn=EST, timeout=20, connect_timeout=PROBE_TIMEOUT)
    if "initialized" not in out:
        return None, "not deployed (optional — simulator/compose + bootstrap/4_vault/seed.py)"
    compact = out.replace(" ", "")
    ok = '"initialized":true' in compact and '"sealed":false' in compact
    return ok, ("up, unsealed" if ok else "present but sealed/uninitialized")


def c_vault_seeded():
    """State 2 — Vault holds the Meridian secrets: Step 4 (bootstrap/4_vault/seed.py) wrote
    secret/meridian/{servicenow,keycloak,ssh}. Vault-not-deployed is ⚪ (optional, like the up probe);
    Vault up but unseeded is 🔴 — Step 4 isn't done, or in-memory dev Vault restarted and needs re-seeding."""
    tok = os.environ.get("VAULT_TOKEN", "meridian-root")
    mount = os.environ.get("VAULT_KV_MOUNT", "secret")
    cmd = ('if podman exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault status -format=json 2>/dev/null '
           "| grep -q '\"initialized\": *true'; then "
           f'podman exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN={tok} '
           f'vault vault kv list -format=json {mount}/meridian 2>/dev/null || echo "[]"; '
           'else echo NOVAULT; fi')
    out = ssh(cmd, fqdn=EST, timeout=20, connect_timeout=PROBE_TIMEOUT).strip()
    if out == "NOVAULT" or not out:
        return None, "Vault not deployed (optional — see the up probe)"
    try:
        keys = set(json.loads(out))
    except (ValueError, json.JSONDecodeError):
        keys = set()
    if not keys:
        return False, "Vault up but not seeded (run bootstrap/4_vault/seed.py)"
    want = {"servicenow", "keycloak", "ssh"}
    miss = want - keys
    return not miss, f"{len(want & keys)}/3 paths ({', '.join(sorted(keys))})" + (f" — missing {', '.join(sorted(miss))}" if miss else "")


# --- AAP control-plane probes (the runtime-specific part) ---------------------------------------

def c_aap_gateway():
    st, _ = req(f"https://{os.environ['AAP_FQDN']}/")
    return st == 200, f"HTTP {st}"


def c_aap_controller():
    st, d = req(f"https://{os.environ['AAP_FQDN']}/api/controller/v2/ping/")
    return st == 200, f"version {d.get('version')}, {len(d.get('instances', []))} instance(s)"


def c_aap_subscription():
    _, d = req(f"https://{os.environ['AAP_FQDN']}/api/controller/v2/config/",
               os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])
    li = d.get("license_info", {}) if isinstance(d, dict) else {}
    return bool(li.get("valid_key")), f"{li.get('license_type')} valid={li.get('valid_key')}"


def c_aap_activations():
    ctl = controller("aap")
    acts = ctl.call(f"{ctl.eda}/activations/?page_size=50", timeout=PROBE_TIMEOUT)["results"]
    running = {a["name"] for a in acts if a.get("status") == "running"}
    return EDA_ACTIVATIONS <= running, f"{len(running & EDA_ACTIVATIONS)}/{len(EDA_ACTIVATIONS)} EDA activations running"


def c_aap_ee_path():
    # Heavy probe: launches a real ad-hoc job. Runs every pass now (no light/deep split) — see USAGE.
    ctl = controller("aap")
    inv = ctl.call("inventories/?name=Meridian%20Fleet", timeout=PROBE_TIMEOUT)
    cred = ctl.call("credentials/?name=Target%20SSH", timeout=PROBE_TIMEOUT)
    if not inv.get("count") or not cred.get("count"):
        return False, "controller not configured (run bootstrap/6A_aap/controller/configure.py)"
    cmd = ctl.call("ad_hoc_commands/", {"inventory": inv["results"][0]["id"], "credential": cred["results"][0]["id"],
                                        "module_name": "ping", "module_args": ""}, timeout=PROBE_TIMEOUT)
    cid, status = cmd["id"], "pending"
    for _ in range(20):
        time.sleep(3)
        status = ctl.call(f"ad_hoc_commands/{cid}/", timeout=PROBE_TIMEOUT)["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    return status == "successful", f"ad-hoc ping: {status}"


# --- AWX control-plane probes (the `awx` runtime: bare AWX /api/v2 + a separate eda-server) ----------

def c_awx_ping():
    st, d = req(f"https://{fqdn('awx')}/api/v2/ping/")
    return st == 200, f"AWX {d.get('version')}, {len(d.get('instances', []))} instance(s)"


def c_awx_config():
    ctl = controller("awx")
    jts = ctl.call("job_templates/", timeout=PROBE_TIMEOUT).get("count", 0)
    inv = (ctl.call("inventories/?name=Meridian%20Fleet", timeout=PROBE_TIMEOUT).get("results") or [{}])[0]
    hosts = inv.get("total_hosts", 0)
    return jts >= 13 and hosts >= 9, f"{jts} job templates, Meridian Fleet = {hosts} hosts"


def c_awx_eda_activations():
    # eda-server API (k3s NodePort 31080; set EDA_HOST=localhost:31080 to use an SSH tunnel). Auth: EDA_ADMIN_*.
    host = os.environ.get("EDA_HOST") or f"{fqdn('awx')}:31080"
    res = http_json(f"http://{host}/api/eda/v1/activations/?page_size=50",
                    headers={"Authorization": basic_auth(os.environ.get("EDA_ADMIN_USER", "admin"),
                                                          os.environ["EDA_ADMIN_PASSWORD"])},
                    timeout=PROBE_TIMEOUT)
    want = {"pull-incident-remediation", "pull-selfservice-restart", "pull-onboarding", "monitor-health"}
    running = {a["name"] for a in res.get("results", []) if a.get("status") == "running"}
    return want <= running, f"{len(running & want)}/{len(want)} eda-server activations running"


def c_awx_target_path():   # deep — launches an ad-hoc ping (k3s pod -> node gateway -> fleet)
    ctl = controller("awx")
    inv = ctl.call("inventories/?name=Meridian%20Fleet", timeout=PROBE_TIMEOUT)
    cred = ctl.call("credentials/?name=Target%20SSH", timeout=PROBE_TIMEOUT)
    if not inv.get("count") or not cred.get("count"):
        return False, "controller not configured (run bootstrap/6B_awx/controller/configure.py)"
    cmd = ctl.call("ad_hoc_commands/", {"inventory": inv["results"][0]["id"], "credential": cred["results"][0]["id"],
                                        "module_name": "ping", "limit": "hr-web-01"}, timeout=PROBE_TIMEOUT)
    cid, status = cmd["id"], "pending"
    for _ in range(20):
        time.sleep(3)
        status = ctl.call(f"ad_hoc_commands/{cid}/", timeout=PROBE_TIMEOUT)["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    return status == "successful", f"ad-hoc ping hr-web-01: {status}"


def c_awx_sso():
    # OIDC wired: AWX's social-auth login endpoint redirects to the Meridian realm's auth URL.
    conn = http.client.HTTPSConnection(fqdn("awx"), 443, context=INSECURE, timeout=PROBE_TIMEOUT)
    conn.request("GET", "/sso/login/oidc/")
    r = conn.getresponse()
    loc = {k.lower(): v for k, v in r.getheaders()}.get("location", "")
    conn.close()
    if "realms/meridian/protocol/openid-connect/auth" not in loc:
        return None, "AWX OIDC not wired (run bootstrap/6B_awx/configure_sso.py)"
    ok = r.status in (301, 302, 303, 307) and "client_id=awx" in loc
    return ok, f"/sso/login/oidc/ → Keycloak(awx)={ok}"


# --- SSO install checks (a login proves wiring, not automation) ---------------------------------
# These probes verify SSO is *offered/wired* (a cheap check); the companion *login* probes run a real login flow.

def _gw_oidc():
    u = http_json(f"https://{os.environ['AAP_FQDN']}/api/gateway/v1/ui_auth/",
                  headers={"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])},
                  ctx=INSECURE, timeout=PROBE_TIMEOUT)
    return next((s for s in u.get("ssos", []) if s.get("type") == "oidc"), None)


def c_aap_sso_offered():
    sso = _gw_oidc()
    return (True if sso else None), "OIDC button present" if sso else "SSO not federated (optional — bootstrap/6A_aap/configure_sso.py)"


def c_aap_sso_login():   # deep
    sso = _gw_oidc()
    if not sso:
        return None, "SSO not federated (optional — bootstrap/6A_aap/configure_sso.py)"
    gw = f"https://{os.environ['AAP_FQDN']}/api/gateway/v1"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                         urllib.request.HTTPSHandler(context=INSECURE))
    opener.addheaders = [("User-Agent", "meridian-sso-check")]
    page = opener.open(f"https://{os.environ['AAP_FQDN']}{sso['login_url']}", timeout=30).read().decode("utf-8", "replace")
    m = re.search(r'<form[^>]+action="([^"]+)"', page)
    if not m:
        return False, "Keycloak login form not found"
    data = urllib.parse.urlencode({"username": SSO_USER, "password": os.environ["KC_DEMO_PASSWORD"]}).encode()
    opener.open(urllib.request.Request(html.unescape(m.group(1)), data=data), timeout=30).read()
    me = json.loads(opener.open(f"{gw}/me/", timeout=30).read().decode())
    user = (me.get("results") or [me])[0] if isinstance(me, dict) else {}
    ok = user.get("username") == SSO_USER and bool(user.get("is_superuser"))
    return ok, f"logged in as {user.get('username')}, superuser={user.get('is_superuser')}"


def _hr_get(path):
    c = http.client.HTTPSConnection(EST, 9443, context=INSECURE, timeout=PROBE_TIMEOUT)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, body


def c_hrportal_sso_wired():
    st, h, _ = _hr_get("/hr/login")
    loc = h.get("location", "")
    redirect = st in (302, 303, 307) and "protocol/openid-connect/auth" in loc and "client_id=hr-portal" in loc
    _, _, body = _hr_get("/hr/health")
    try:
        sso_on = json.loads(body).get("sso") == "enabled"
    except json.JSONDecodeError:
        sso_on = False
    if not redirect and not sso_on:
        return False, "hr-portal SSO not configured (run bootstrap/3_keycloak/configure.py)"
    return redirect and sso_on, f"/hr/login→Keycloak(hr-portal)={redirect}, sso={sso_on}"


def c_hrportal_sso_login():   # deep
    issuer = f"https://{EST}:9443/auth/realms/meridian"
    with open(os.path.join(ROOT, "simulator", "fleet.yml")) as f:
        person = next(p for p in yaml.safe_load(f)["people"] if p.get("team"))
    username = person["email"].split("@")[0]
    try:
        data = urllib.parse.urlencode({"grant_type": "password", "client_id": "hr-portal",
                                       "client_secret": os.environ["KC_HRPORTAL_CLIENT_SECRET"],
                                       "username": username, "password": os.environ["KC_DEMO_PASSWORD"],
                                       "scope": "openid profile email"}).encode()
        req = urllib.request.Request(f"{issuer}/protocol/openid-connect/token", data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, context=INSECURE, timeout=PROBE_TIMEOUT) as r:
            tok = json.load(r)
        req2 = urllib.request.Request(f"{issuer}/protocol/openid-connect/userinfo",
                                      headers={"Authorization": f"Bearer {tok['access_token']}"})
        with urllib.request.urlopen(req2, context=INSECURE, timeout=PROBE_TIMEOUT) as r:
            ui = json.load(r)
        groups = ui.get("groups") or []
        ok = bool(ui.get("email")) and person["team"] in groups
        return ok, f"{username}: email={ui.get('email')}, team {person['team']} in {groups}"
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return False, "SSO not configured (run bootstrap/3_keycloak/configure.py)"
        return False, f"grant failed: {ex}"
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as ex:
        return False, f"grant failed: {ex}"


SERVICENOW = [
    ("ServiceNow up (instance)", c_sn_up),
    ("ServiceNow auth (eda.integration)", c_sn_auth),
    ("EDA account + pull filter", c_sn_eda_account),
    ("CMDB loaded (server CIs)", c_sn_cmdb),
    ("Push Business Rules", c_sn_business_rules),
]
# Probe groups mirror the build phases in docs/08-build.md — same names, same order — so "is Step N
# green?" reads straight off one section of the board. Steps 1–5 are runtime-agnostic; Step 6 (the
# Ansible controller) swaps per runtime via RUNTIME_PROBES. SERVICENOW (above) is Step 5.
INFRA = [
    ("VM up (SSH · cloud-init)", c_vm),
]
FLEET = [
    ("Targets (sshd up)", c_targets),
    ("hr-portal app (/hr/health)", c_hrportal),
]
IDENTITY = [
    ("Keycloak up (service)", c_keycloak_up),
    ("Keycloak realm seeded", c_keycloak),
    ("hr-portal SSO wired", c_hrportal_sso_wired),
    ("hr-portal SSO login", c_hrportal_sso_login),
]
SECRETS = [
    ("Vault up (unsealed)", c_vault),
    ("Vault seeded (secret/meridian)", c_vault_seeded),
]
RUNTIME_PROBES = {
    "aap": [
        ("AAP gateway", c_aap_gateway),
        ("AAP controller API", c_aap_controller),
        ("AAP subscription", c_aap_subscription),
        ("EDA activations", c_aap_activations),
        ("EE → targets (ad-hoc ping)", c_aap_ee_path),
        ("AAP SSO offered", c_aap_sso_offered),
        ("AAP SSO login", c_aap_sso_login),
    ],
    "awx": [
        ("AWX control plane", c_awx_ping),
        ("AWX controller config", c_awx_config),
        ("eda-server activations", c_awx_eda_activations),
        ("AWX → targets (ad-hoc ping)", c_awx_target_path),
        ("AWX SSO offered", c_awx_sso),
    ],
}


def run_all(runtime=None, only=None):
    """Run the probes and return (all_ok, [(group, name, ok, info, secs), ...], elapsed). Probes run
    CONCURRENTLY (they are I/O-bound) so the dashboard refreshes fast — including the heavy ones that
    launch jobs / full logins, so every pass (one-shot or --watch) exercises the real loop. Results keep
    registry order; `elapsed` is the wall-clock for the whole pass (≈ the slowest probe, not the sum).
    ok None => a probe gracefully skipped itself (precondition not built / optional component), secs None.
    only=<substr> keeps just the checks whose name contains it (e.g. 'sso')."""
    env()
    rt = runtime_name(runtime)
    global EST, RT
    RT = rt
    EST = fqdn(rt)   # the estate (simulator) runs on whichever VM hosts the control plane
    groups = [
        ("1 · Infra", INFRA),
        ("2 · Fleet", FLEET),
        ("3 · Identity", IDENTITY),
        ("4 · Secrets", SECRETS),
        ("5 · ServiceNow", SERVICENOW),
        (f"6 · Ansible controller · {rt}", RUNTIME_PROBES[rt]),
    ]
    plan = []  # ordered (group, name, fn_or_None, skip_info)
    for group, checks in groups:
        for name, fn in checks:
            if only and only.lower() not in name.lower():
                continue
            plan.append((group, name, fn, None))

    def _timed(fn):
        t0 = time.monotonic()
        try:
            ok, info = fn()
        except Exception as e:  # a probe blowing up is itself a failure to report, never fatal
            ok, info = False, str(e)
        return ok, info, time.monotonic() - t0

    runnable = [i for i, p in enumerate(plan) if p[2] is not None]
    t_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(runnable)))) as ex:
        futures = {i: ex.submit(_timed, plan[i][2]) for i in runnable}
        done = {i: futures[i].result() for i in runnable}
    elapsed = time.monotonic() - t_start

    results, all_ok = [], True
    for i, (group, name, fn, skip) in enumerate(plan):
        if fn is None:
            results.append((group, name, None, skip, None))
        else:
            ok, info, secs = done[i]
            results.append((group, name, ok, info, secs))
            if ok is False:
                all_ok = False
    return all_ok, results, elapsed


def to_json(runtime, results, elapsed, all_ok):
    """Machine-readable one-pass result — the same data as render(), for tooling/agents. `state` maps the
    tri-value ok: True→'pass', False→'fail', None→'optional' (an optional component not deployed)."""
    state = {True: "pass", False: "fail", None: "optional"}
    groups, counts = [], {"pass": 0, "fail": 0, "optional": 0}
    for group, name, ok, info, secs in results:
        if not groups or groups[-1]["group"] != group:
            groups.append({"group": group, "checks": []})
        groups[-1]["checks"].append({"name": name, "state": state[ok], "ok": ok,
                                     "info": info, "secs": round(secs, 2) if secs is not None else None})
        counts[state[ok]] += 1
    return {"runtime": runtime, "ok": all_ok, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": round(elapsed, 2) if elapsed is not None else None,
            "summary": counts, "groups": groups}


# --- Step 7: functional scenarios (tests/scenarios/) -------------------------------------------------
# Opt-in via --scenarios. Each scenario MUTATES state (opens incidents, launches jobs), so it is never
# part of the read-only dashboard and never loops under --watch. Run as subprocesses with RUNTIME set —
# exit 0 = PASS. They run SEQUENTIALLY: they share the one ServiceNow PDI + the fleet, so parallel runs
# would cross-contaminate (e.g. the monitor scenario picking up another scenario's incident).
SCEN_DIR = os.path.join(ROOT, "tests", "scenarios")
SCEN_AWX_SKIP = {"2"}   # 2_push_change_execution is AAP-only — eda-server has no event-stream ingress
SCEN_TIMEOUT = 120      # seconds per scenario (they poll for job completion; bump if EE cold-starts are slow)


def run_scenarios(runtime=None, only=None, parallel=False):
    """Run tests/scenarios/ as the '7 · Scenarios' group → (all_ok, results, elapsed), the same shape as
    run_all() so render()/to_json() are reused. Streams per-scenario progress to STDERR as each finishes
    (so --json keeps stdout a clean object). On AWX the push scenario is skipped (⚪). Sequential by
    default; parallel=True runs them concurrently (wall ≈ slowest, not the sum) — but they share the one
    PDI + fleet, so the monitor scenario can react to another's service restart (occasional flakiness)."""
    rt = runtime_name(runtime)
    group = "7 · Scenarios"
    items = []   # (fname, name, skip_info_or_None), in numeric order
    for fname in sorted(f for f in os.listdir(SCEN_DIR) if re.match(r"\d+_.*\.py$", f)):
        num, name = fname.split("_", 1)[0], fname[:-3]
        if only and only.lower() not in name.lower():
            continue
        skip = ("n/a on AWX — push is AAP-only (no event-stream ingress)"
                if rt == "awx" and num in SCEN_AWX_SKIP else None)
        items.append((fname, name, skip))

    def _one(fname):
        t0 = time.monotonic()
        try:
            p = subprocess.run([sys.executable, os.path.join(SCEN_DIR, fname)],
                               capture_output=True, text=True, cwd=ROOT,
                               env=dict(os.environ, RUNTIME=rt), timeout=SCEN_TIMEOUT)
            line = next((l for l in reversed(p.stdout.splitlines()) if "PASS" in l or "FAIL" in l), "")
            ok, info = p.returncode == 0, (line.lstrip("> ").strip() or f"exit {p.returncode}")
        except subprocess.TimeoutExpired:
            ok, info = False, f"timed out (>{SCEN_TIMEOUT}s)"
        return ok, info, time.monotonic() - t0

    run_idx = [i for i, it in enumerate(items) if it[2] is None]
    namew = max((len(it[1]) for it in items), default=20) + 2
    sys.stderr.write(f"\n   7 · Scenarios — {len(run_idx)} to run "
                     f"({'parallel' if parallel else 'sequential'}, RUNTIME={rt})…\n")

    def _emit(name, ok, info, secs):
        sys.stderr.write(f"   {EMOJI[ok]} {secs:6.2f}s  {name.ljust(namew)}{info}\n")
        sys.stderr.flush()

    done, t_start = {}, time.monotonic()
    if parallel and len(run_idx) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(run_idx))) as ex:
            futs = {ex.submit(_one, items[i][0]): i for i in run_idx}
            for fut in as_completed(futs):
                i = futs[fut]; done[i] = fut.result()
                _emit(items[i][1], *done[i])
    else:
        for i in run_idx:
            done[i] = _one(items[i][0])
            _emit(items[i][1], *done[i])
    elapsed = time.monotonic() - t_start

    results = []
    for i, (fname, name, skip) in enumerate(items):
        results.append((group, name, None, skip, None) if skip is not None
                       else (group, name, *done[i]))
    all_ok = all(r[2] is not False for r in results)
    return all_ok, results, elapsed


def render(runtime, results, ts=None, elapsed=None):
    title = f"Meridian PoC · health · {runtime}" + (f" · {ts}" if ts else "")
    width = max(58, len(title) + 2)
    print("  ╭" + "─" * width + "╮")
    print("  │ " + title.ljust(width - 1) + "│")
    print("  ╰" + "─" * width + "╯")

    namew = max((len(n) for _, n, _, _, _ in results), default=20) + 2
    last_group = None
    for group, name, ok, info, secs in results:
        if group != last_group:
            print(f"\n   {group}")
            last_group = group
        t = f"{secs:6.2f}s" if secs is not None else "      -"
        print(f"   {EMOJI[ok]}  {t}  {name.ljust(namew)}{info}")

    npass = sum(1 for _, _, ok, _, _ in results if ok is True)
    nfail = sum(1 for _, _, ok, _, _ in results if ok is False)
    nskip = sum(1 for _, _, ok, _, _ in results if ok is None)
    total = elapsed if elapsed is not None else sum(s for *_, s in results if s is not None)
    verdict = f"🔴  {nfail} failing" if nfail else "✅  all systems go"
    print("\n   " + "─" * (width - 1))
    print(f"   🟢 {npass}   ⚪ {nskip}   🔴 {nfail}   ⏱  {total:.1f}s wall      {verdict}")


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(USAGE)
        return
    runtime = args[args.index("--runtime") + 1] if "--runtime" in args else None
    only = args[args.index("--only") + 1] if "--only" in args else None   # e.g. --only sso
    watch = "--watch" in args
    json_out = "--json" in args
    scenarios = "--scenarios" in args
    parallel = "--parallel" in args
    if scenarios and watch:
        sys.exit("--scenarios and --watch are mutually exclusive (scenarios mutate state — never loop).")
    if json_out and watch:
        sys.exit("--json and --watch are mutually exclusive (--json is a single pass).")
    interval = 30
    if watch:
        i = args.index("--watch")
        if i + 1 < len(args) and args[i + 1].isdigit():
            interval = int(args[i + 1])

    rt = runtime_name(runtime)
    run = (lambda: run_scenarios(rt, only=only, parallel=parallel)) if scenarios else (lambda: run_all(rt, only=only))

    if json_out:
        all_ok, results, elapsed = run()
        print(json.dumps(to_json(rt, results, elapsed, all_ok), ensure_ascii=False, indent=2))
        sys.exit(0 if all_ok else 1)
    if not watch:
        all_ok, results, elapsed = run()
        print()
        render(rt, results, elapsed=elapsed)
        print()
        sys.exit(0 if all_ok else 1)

    try:
        while True:
            _, results, elapsed = run_all(rt, only=only)
            print("\033[2J\033[H", end="")  # clear screen, home cursor
            render(rt, results, ts=time.strftime("%Y-%m-%d %H:%M:%S"), elapsed=elapsed)
            print(f"\n  (watch every {interval}s — Ctrl-C to stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
