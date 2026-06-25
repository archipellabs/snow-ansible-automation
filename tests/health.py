#!/usr/bin/env python3
"""Holistic health dashboard for the PoC stack (stdlib only) — read-only, safe to loop.

  python3 tests/health.py                    # one pass
  python3 tests/health.py --watch [seconds]  # live dashboard, redraws on interval (default 10s)
  python3 tests/health.py --runtime aap|awx  # control-plane probes (default $RUNTIME or aap)

This is not a scenario (it breaks nothing): it answers "is every component alive right now?" across
ServiceNow, the automation control plane (runtime-specific), the target fleet, the Meridian apps and
Keycloak. The control-plane probes are the part that differs per runtime — only those change for AWX.
Exit 0 if all non-skipped checks pass.
"""
import html
import http.client
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh, insecure_ctx, basic_auth, http_json  # noqa: E402
from lib.runtime import controller, runtime_name, fqdn  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

INSECURE = insecure_ctx()
EST = None   # estate host (the VM running the simulator) for the runtime under test; set in run_all()
EDA_ACTIVATIONS = {"pull-incident-remediation", "monitor-health", "push-change-execution",
                   "pull-selfservice-restart", "pull-onboarding"}
EMOJI = {True: "🟢", False: "🔴", None: "⚪"}   # PASS / FAIL / SKIP
PROBE_TIMEOUT = 8   # seconds — a probe must fail fast so the dashboard never freezes on a dead component
SSO_USER = "nadia.haddad"   # a DSI/DBA staff member -> IT-Admins group -> superuser (for the deep AAP login)


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


def c_sn_business_rules():
    # The push trigger: the "EDA - push …" Business Rule for `change` must be active, or the push
    # pattern silently never fires. (Self-service + onboarding are pull now — no Business Rule.)
    snow = Snow(creds="admin")
    q = urllib.parse.urlencode({"sysparm_query": "nameSTARTSWITHEDA - push^active=true",
                                "sysparm_fields": "name", "sysparm_limit": "10"})
    res = snow.result(f"table/sys_script?{q}")
    return len(res) >= 1, f"{len(res)} push Business Rule(s) active (change)"


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


def c_keycloak():
    try:
        st, _ = req(f"https://{EST}:9443/auth/realms/meridian/.well-known/openid-configuration")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "realm 'meridian' not configured (run bootstrap/3_keycloak/configure.py)"
        raise
    return st == 200, f"realm 'meridian' HTTP {st}"


def c_vault():
    """Vault liveness — server-side check (:8200 isn't open externally). Optional: graceful-skip when
    Vault isn't deployed. Dev mode is in-memory, so 'unsealed' also implies the seed survived (re-seed
    after a restart with bootstrap/3_vault/seed.py)."""
    out = ssh("podman exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault status -format=json 2>/dev/null || true",
              fqdn=EST, timeout=20, connect_timeout=PROBE_TIMEOUT)
    if "initialized" not in out:
        return None, "not deployed (optional — simulator/compose + bootstrap/3_vault/seed.py)"
    compact = out.replace(" ", "")
    ok = '"initialized":true' in compact and '"sealed":false' in compact
    return ok, ("up, unsealed" if ok else "present but sealed/uninitialized")


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
    # DEEP probe: this actually launches an ad-hoc job, so it is skipped under --watch (see run_all).
    ctl = controller("aap")
    inv = ctl.call("inventories/?name=Meridian%20Fleet", timeout=PROBE_TIMEOUT)
    cred = ctl.call("credentials/?name=Target%20SSH", timeout=PROBE_TIMEOUT)
    if not inv.get("count") or not cred.get("count"):
        return None, "SKIP (run bootstrap/5A_aap/controller/configure.py first)"
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
        return None, "SKIP (run bootstrap/5B_awx/controller/configure.py first)"
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
        return None, "AWX OIDC not wired (run bootstrap/5B_awx/configure_sso.py)"
    ok = r.status in (301, 302, 303, 307) and "client_id=awx" in loc
    return ok, f"/sso/login/oidc/ → Keycloak(awx)={ok}"


# --- SSO install checks (a login proves wiring, not automation) ---------------------------------
# Light probes verify SSO is *offered/wired* (fast, every tick); the deep ones run a real login flow.

def _gw_oidc():
    u = http_json(f"https://{os.environ['AAP_FQDN']}/api/gateway/v1/ui_auth/",
                  headers={"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])},
                  ctx=INSECURE, timeout=PROBE_TIMEOUT)
    return next((s for s in u.get("ssos", []) if s.get("type") == "oidc"), None)


def c_aap_sso_offered():
    sso = _gw_oidc()
    return bool(sso), "OIDC button present" if sso else "no OIDC button — run bootstrap/5A_aap/configure_sso.py"


def c_aap_sso_login():   # deep
    sso = _gw_oidc()
    if not sso:
        return False, "no OIDC button on the gateway"
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
        return None, "hr-portal SSO not configured (run bootstrap/3_keycloak/configure.py)"
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
            return None, "SSO not configured (run bootstrap/3_keycloak/configure.py)"
        return False, f"grant failed: {ex}"
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as ex:
        return False, f"grant failed: {ex}"


SERVICENOW = [
    ("ServiceNow auth (eda.integration)", c_sn_auth),
    ("EDA account + pull filter", c_sn_eda_account),
    ("Push Business Rules", c_sn_business_rules),
]
COMMON = [
    ("Targets (sshd up)", c_targets),
    ("hr-portal app (/hr/health)", c_hrportal),
    ("Keycloak realm 'meridian'", c_keycloak),
    ("Vault (secret store)", c_vault),
    ("hr-portal SSO wired", c_hrportal_sso_wired),
    ("hr-portal SSO login", c_hrportal_sso_login),
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
# Probes that actively launch work or run a full login flow (too heavy to run every tick) —
# skipped under --watch (shown as ⚪), run only in one-pass.
DEEP = {"EE → targets (ad-hoc ping)", "AWX → targets (ad-hoc ping)", "AAP SSO login", "hr-portal SSO login"}


def run_all(runtime=None, deep=True, only=None):
    """Run the probes and return (all_ok, [(group, name, ok, info, secs), ...], elapsed). Probes run
    CONCURRENTLY (they are I/O-bound) so the dashboard refreshes fast; results keep registry order and
    `elapsed` is the wall-clock for the whole pass (≈ the slowest probe, not the sum). ok None =>
    skipped (secs None). deep=False skips the heavy probes (those that launch jobs / full logins) —
    for --watch. only=<substr> keeps just the checks whose name contains it (e.g. 'sso')."""
    env()
    rt = runtime_name(runtime)
    global EST
    EST = fqdn(rt)   # the estate (simulator) runs on whichever VM hosts the control plane
    groups = [("ServiceNow", SERVICENOW), ("Stack", COMMON), (f"Control plane · {rt}", RUNTIME_PROBES[rt])]
    plan = []  # ordered (group, name, fn_or_None, skip_info)
    for group, checks in groups:
        for name, fn in checks:
            if only and only.lower() not in name.lower():
                continue
            if name in DEEP and not deep:
                plan.append((group, name, None, "deep check — skipped in watch (run one-pass)"))
            else:
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
    runtime = args[args.index("--runtime") + 1] if "--runtime" in args else None
    only = args[args.index("--only") + 1] if "--only" in args else None   # e.g. --only sso
    watch = "--watch" in args
    interval = 10
    if watch:
        i = args.index("--watch")
        if i + 1 < len(args) and args[i + 1].isdigit():
            interval = int(args[i + 1])

    rt = runtime_name(runtime)
    if not watch:
        all_ok, results, elapsed = run_all(rt, only=only)
        print()
        render(rt, results, elapsed=elapsed)
        print()
        sys.exit(0 if all_ok else 1)

    try:
        while True:
            _, results, elapsed = run_all(rt, deep=False, only=only)   # fast liveness only
            print("\033[2J\033[H", end="")  # clear screen, home cursor
            render(rt, results, ts=time.strftime("%Y-%m-%d %H:%M:%S"), elapsed=elapsed)
            print(f"\n  (watch every {interval}s — Ctrl-C to stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
