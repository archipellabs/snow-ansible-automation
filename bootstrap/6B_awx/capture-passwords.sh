#!/usr/bin/env bash
# Capture the two auto-generated admin passwords from the AWX VM into the repo-root .env:
#   AWX_ADMIN_PASSWORD  <- secret awx-admin-password (ns awx, set by awx-operator)
#   EDA_ADMIN_PASSWORD  <- secret eda-admin-password (ns eda, set by eda-server-operator)
# AWX and eda-server each generate their admin password at install; the config-as-code scripts and
# tests/health.py read them from .env. Run this once after bootstrap/6B_awx/sync.sh finishes the install
# (re-run safely after any reinstall — it only rewrites those two lines).
#
#   ./bootstrap/6B_awx/capture-passwords.sh
set -euo pipefail
cd "$(dirname "$0")"
ENVFILE="../../.env"
FQDN="${AWX_FQDN:-$(grep -E '^AWX_FQDN=' "$ENVFILE" 2>/dev/null | head -1 | cut -d= -f2-)}"
[ -n "$FQDN" ] || { echo "AWX_FQDN not set (env var or repo-root .env)" >&2; exit 1; }
KEY="${HOME}/.ssh/snow-aap-poc"
SSH="ssh -i ${KEY} -o StrictHostKeyChecking=accept-new"

fetch() {  # fetch <namespace> <secret-name> -> the decoded password on stdout
  $SSH "azureuser@${FQDN}" "sudo kubectl -n $1 get secret $2 -o jsonpath='{.data.password}' 2>/dev/null | base64 -d" 2>/dev/null
}

AWX_PW="$(fetch awx awx-admin-password)"
EDA_PW="$(fetch eda eda-admin-password)"
[ -n "$AWX_PW" ] || { echo "could not read awx-admin-password — is AWX installed? (run bootstrap/6B_awx/sync.sh)" >&2; exit 1; }
[ -n "$EDA_PW" ] || { echo "could not read eda-admin-password — eda-server may still be reconciling; retry in a few min" >&2; exit 1; }

# Rewrite only AWX_ADMIN_PASSWORD / EDA_ADMIN_PASSWORD in the repo-root .env (append if absent). Values
# are written verbatim — the .env is parsed, never sourced — so generated passwords are safe as-is.
AWX_PW="$AWX_PW" EDA_PW="$EDA_PW" ENVFILE="$ENVFILE" python3 - <<'PY'
import os
env = os.environ["ENVFILE"]
vals = {"AWX_ADMIN_PASSWORD": os.environ["AWX_PW"], "EDA_ADMIN_PASSWORD": os.environ["EDA_PW"]}
lines = open(env).read().splitlines() if os.path.isfile(env) else []
seen, out = set(), []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else None
    if key in vals:
        out.append(f"{key}={vals[key]}"); seen.add(key)
    else:
        out.append(line)
for key, val in vals.items():
    if key not in seen:
        out.append(f"{key}={val}")
open(env, "w").write("\n".join(out) + "\n")
PY

echo ">> Captured AWX_ADMIN_PASSWORD + EDA_ADMIN_PASSWORD into .env (${#AWX_PW}/${#EDA_PW} chars)."
echo ">> Verify: python3 tests/health.py --runtime awx"
