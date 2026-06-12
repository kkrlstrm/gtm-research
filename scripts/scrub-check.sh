#!/usr/bin/env bash
# scrub-check.sh — pre-publish gate. FAIL-CLOSED: any finding exits non-zero.
#
# Scans the working tree for:
#   1. private-source coupling that must NOT leak into this OSS repo
#   2. the network secret-fetch pattern that must never appear (local env only)
#   3. hardcoded local home paths to a .env
#   4. secret-shaped strings in committed files (placeholders excepted)
# Then runs gitleaks if installed (authoritative).
#
# This script excludes ITSELF (it necessarily contains the patterns it searches for).

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

FAIL=0
red()  { printf '\033[31m%s\033[0m\n' "$1"; }
grn()  { printf '\033[32m%s\033[0m\n' "$1"; }
fail() { red   "FAIL  $1"; FAIL=1; }
pass() { grn   "ok    $1"; }
note() { printf '      %s\n' "$1"; }

GREP=(grep -rniI
  --exclude-dir=.git
  --exclude-dir=__pycache__
  --exclude-dir=node_modules
  --exclude-dir=.venv
  --exclude='.env'
  --exclude='*.lock'
  --exclude='scrub-check.sh')

echo "== scrub-check =="

# 1. private-source coupling --------------------------------------------------------
# These name the private repo this engine was extracted from. None may appear in OSS.
echo "[1/4] private-source coupling strings"
if "${GREP[@]}" \
     -e 'kai-gtm-agents' -e 'kaikarlstrom' -e 'tam-to-target' \
     -e 'openrouter-test' -e 'gtm-daily' \
     -e 'NEON_MASTER_URL' -e 'NEON_RESEARCH_URL' -e 'eb_leads' -e 'eb_replies' \
     -e 'clients\.json' -e 'entity-internal-xref' \
     . ; then
  fail "private-source coupling present — genericize it before publishing"
else
  pass "no private-source coupling"
fi

# 2. network secret-fetch pattern ---------------------------------------------------
echo "[2/4] network secret-fetch pattern"
if "${GREP[@]}" \
     -e 'gh api .*contents/\.env' \
     -e 'eval .*gh api' \
     -e 'curl .*contents/\.env' \
     . ; then
  fail "network secret-fetch bootstrap present — delete it (local env only)"
else
  pass "no network secret-fetch pattern"
fi

# 3. hardcoded local .env paths -----------------------------------------------------
echo "[3/4] hardcoded local home paths"
if "${GREP[@]}" -E '/Users/[A-Za-z0-9._-]+/' . ; then
  fail "hardcoded local home path present — use a relative path or \$ENV"
else
  pass "no hardcoded local home paths"
fi

# 4. secret-shaped strings ----------------------------------------------------------
echo "[4/4] secret-shaped strings in committed files"
SECRET=0
if "${GREP[@]}" -e 'BEGIN [A-Z ]*PRIVATE KEY' . ; then SECRET=1; fi
if grep -rnI \
     --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=node_modules --exclude-dir=.venv \
     --exclude='.env' --exclude='*.example' --exclude='scrub-check.sh' \
     -E '(API_KEY|APIKEY|TOKEN|SECRET|PASSWORD|BEARER)["'"'"' ]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_+/-]{24,}' . \
   | grep -vEi '(<[a-z_]+>|your[_-]|example|placeholder|changeme|xxxx|user:pass|localhost)' ; then
  SECRET=1
fi
if [ "$SECRET" -ne 0 ]; then
  fail "possible secret-shaped string in a committed file — use a placeholder"
else
  pass "no secret-shaped strings"
fi

if command -v gitleaks >/dev/null 2>&1; then
  echo "[+] gitleaks detect"
  if ! gitleaks detect --no-banner --redact --source "$ROOT" >/dev/null 2>&1; then
    fail "gitleaks reported findings (run: gitleaks detect --redact -v)"
  else
    pass "gitleaks clean"
  fi
else
  note "gitleaks not installed — skipping (brew install gitleaks for an authoritative scan)"
fi

echo
if [ "$FAIL" -ne 0 ]; then
  red "✗ scrub-check FAILED — do not publish."
  exit 1
fi
grn "✓ scrub-check passed."
