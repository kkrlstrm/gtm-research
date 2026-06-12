#!/usr/bin/env bash
# selftest.sh — no-network smoke test of the deterministic parts: imports, the
# invoke_when predicate evaluator, graceful no-DB degradation, py_compile, the YAML
# policy, and workflow syntax. Exits non-zero on the first failed assertion.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

PASS=0; FAIL=0
ok()  { printf '\033[32mok\033[0m   %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '\033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
eq()  { if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (got '$2', want '$3')"; fi; }

echo "== imports + predicate evaluator + graceful no-DB =="
# Run with no DB configured so research_db must degrade to disabled.
OUT=$(RESEARCH_DATABASE_URL='' DATABASE_URL='' python3 - <<'PY'
import sys
from research_engine import waterfall as wf, research_db as rdb
s = [r['rung'] for r in wf.rungs('search_waterfall')]
f = [r['rung'] for r in wf.rungs('fetch_waterfall')]
exa = [r for r in wf.rungs('search_waterfall') if r['rung']=='exa'][0]
jina = [r for r in wf.rungs('fetch_waterfall') if r['rung']=='jina'][0]
par = [r for r in wf.rungs('search_waterfall') if r['rung']=='parallel'][0]
checks = {
  "search_order": ",".join(s),
  "fetch_order": ",".join(f),
  "exa_keyword": wf.should_invoke(exa, {"prev_rungs_empty":True,"query_intent":"keyword"}),
  "exa_semantic": wf.should_invoke(exa, {"prev_rungs_empty":True,"query_intent":"semantic"}),
  "jina_shell": wf.should_invoke(jina, {"prev_rung_failed":False,"is_js_shell":True}),
  "parallel_nogate": wf.gate_satisfied(par, {}),
  "parallel_gate": wf.gate_satisfied(par, {"allow_parallel":True}),
  "rdb_enabled": rdb.enabled(),
  "cache_miss_none": rdb.cache_get_page("https://example.com") is None,
}
for k,v in checks.items(): print(f"{k}={v}")
PY
)
echo "$OUT"
eq "search order"        "$(echo "$OUT" | sed -n 's/^search_order=//p')"  "keyword,exa,tavily,parallel"
eq "fetch order"         "$(echo "$OUT" | sed -n 's/^fetch_order=//p')"   "native,jina,tavily,parallel"
eq "exa skips keyword"   "$(echo "$OUT" | sed -n 's/^exa_keyword=//p')"   "False"
eq "exa fires semantic"  "$(echo "$OUT" | sed -n 's/^exa_semantic=//p')"  "True"
eq "jina fires on shell" "$(echo "$OUT" | sed -n 's/^jina_shell=//p')"    "True"
eq "parallel gated off"  "$(echo "$OUT" | sed -n 's/^parallel_nogate=//p')" "False"
eq "parallel cleared"    "$(echo "$OUT" | sed -n 's/^parallel_gate=//p')" "True"
eq "no-DB disables telemetry" "$(echo "$OUT" | sed -n 's/^rdb_enabled=//p')" "False"
eq "cache miss is None"  "$(echo "$OUT" | sed -n 's/^cache_miss_none=//p')" "True"

echo; echo "== providers import + auto-skip without keys =="
PROV=$(EXA_API_KEY='' PARALLEL_API_KEY='' python3 - <<'PY'
from providers import exa_search, jina_reader, parallel_search, parallel_extract
print("exa", exa_search.configured())
print("jina", jina_reader.configured())
print("parallel", parallel_search.configured())
PY
)
echo "$PROV"
eq "exa skips without key"      "$(echo "$PROV" | sed -n 's/^exa //p')"      "False"
eq "jina works keyless"         "$(echo "$PROV" | sed -n 's/^jina //p')"     "True"
eq "parallel skips without key" "$(echo "$PROV" | sed -n 's/^parallel //p')" "False"

echo; echo "== py_compile =="
if python3 -m py_compile research_engine/*.py providers/*.py bin/*.py 2>/dev/null; then
  ok "py_compile"; else bad "py_compile"; fi

echo; echo "== workflow syntax (if node present) =="
if command -v node >/dev/null 2>&1; then
  for w in .claude/workflows/*.js; do
    if node --check "$w" 2>/dev/null; then ok "$(basename "$w") syntax"; else bad "$(basename "$w") syntax"; fi
  done
else
  echo "  (node not installed — skipping workflow syntax check)"
fi

echo
echo "----------------------------------------"
printf 'selftest: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
