export const meta = {
  name: 'entity-research',
  description: 'The research fan-out: one bounded deep-research agent per entity, running the config-driven cost waterfall (cached free-first fetch + search), an optional internal cross-reference, and a verify pass — schema-validated, source-verified rows out.',
  whenToUse: 'When you have a company/person (or a list) and want the same factual fields researched for each, verified against real sources, for a sales CSV / CRM. Runs standalone or as a cached web_research upgrade inside gtm-pipeline.',
  phases: [
    { title: 'Setup', detail: 'open a telemetry run row (if a database is configured)' },
    { title: 'Research', detail: 'one bounded research agent per entity → structured findings (config-driven waterfall + internal xref)' },
    { title: 'Verify', detail: 'second agent re-opens each source_url and confirms / blanks unsupported fields' },
    { title: 'Watchdog', detail: 'write per-entity telemetry, pause the run if the trailing verified-rate collapses' },
  ],
}

// ---------------------------------------------------------------------------
// args (pass as a JSON object):
//   entities   : (required) array of strings or { name, domain?, location?, ... }
//   brief      : (required) what to find for each entity, plain English
//   fields     : (required) array of output field names per finding
//   purpose    : (optional) one line of why — sets the accuracy bar
//   sources    : (optional) array of where-to-look hints
//   entityType : (optional) 'company' (DEFAULT) | 'person'
//   crossReference : (optional bool) run bin/known-xref.py (default true for companies)
//   multiPerEntity : (optional bool, default true)
//   verify     : (optional bool, default true)
//   maxFetches : (optional int, default 6)   per-entity page-fetch budget (in-prompt)
//   maxSearches: (optional int, default 4)   per-entity search budget (in-prompt)
//   model      : (optional) 'sonnet' (DEFAULT) | 'opus' | 'haiku' | 'inherit'
//   verifyModel: (optional) defaults to `model`
//   allowParallel : (optional bool) clear the gated Parallel rung for this run
// ---------------------------------------------------------------------------

function resolveArgs(a) {
  let x = a
  if (typeof x === 'string') { try { x = JSON.parse(x) } catch (e) { /* leave as string */ } }
  if (x && typeof x === 'object' && !Array.isArray(x.entities)) {
    if (x.args && Array.isArray(x.args.entities)) x = x.args
    else if (x.input && Array.isArray(x.input.entities)) x = x.input
  }
  return x && typeof x === 'object' ? x : {}
}
const A = resolveArgs(args)
log(`args arrived as ${typeof args}; resolved entities = ${Array.isArray(A.entities) ? A.entities.length : 'none'}`)

if (!Array.isArray(A.entities) || !A.entities.length) {
  log('No entities provided. Pass args = { entities:[...], brief:"...", fields:[...] }.')
  return { error: 'no-entities', rows: [], argsType: typeof args }
}

const brief     = A.brief    || 'Find the requested fields for this entity.'
const fields    = Array.isArray(A.fields) && A.fields.length ? A.fields : ['value']
const purpose   = A.purpose  || 'This is going into a sales CSV — accuracy matters.'
const sources   = Array.isArray(A.sources) ? A.sources : []
const multi     = A.multiPerEntity !== false
const doVerify  = A.verify !== false
const entityType = (A.entityType === 'person') ? 'person' : 'company'
const crossRef  = A.crossReference !== undefined ? !!A.crossReference : (entityType === 'company')
const maxFetches  = Number.isFinite(+A.maxFetches)  ? +A.maxFetches  : 6
const maxSearches = Number.isFinite(+A.maxSearches) ? +A.maxSearches : 4
const allowParallel = !!A.allowParallel
const researchModel = A.model || A.researchModel || 'sonnet'
const verifyModel   = A.verifyModel || researchModel || 'sonnet'
function modelOpt(m) { return m && m !== 'inherit' ? { model: m } : {} }

// Run all commands from the repo root (override with GTM_RESEARCH_ROOT).
const REPO_ROOT = (typeof process !== 'undefined' && process.env && process.env.GTM_RESEARCH_ROOT) || '.'

// Set in the Setup phase. Empty RUN_ID => telemetry disabled; the fan-out runs the
// same, just without cache/telemetry linkage.
let RUN_ID = ''
let TELEMETRY = false

function normalize(e) {
  if (e && typeof e === 'object') {
    const label = e.name || e.label || e.entity || JSON.stringify(e)
    const domain = e.domain || e.website || ''
    const ctx = Object.entries(e)
      .filter(([k]) => !['name', 'label', 'entity'].includes(k))
      .map(([k, v]) => `${k}: ${v}`).join('\n')
    return { label, domain, context: ctx }
  }
  return { label: String(e), domain: '', context: '' }
}
const entities = A.entities.map(normalize)

function findingSchema() {
  const props = {}
  for (const f of fields) props[f] = { type: 'string', description: `${f} (empty string if not found)` }
  props.source_url = { type: 'string', description: 'PRIMARY-source URL where this was verified; empty if unverified' }
  props.verified = { type: 'boolean', description: 'true ONLY if a page you actually opened states the fact' }
  props.note = { type: 'string', description: 'caveats — e.g. "UNVERIFIED — pattern guess" or "NOT FOUND — searched X, absent"' }
  const required = [...fields, 'source_url', 'verified', 'note']
  if (crossRef) {
    props.internal_status = { type: 'string', description: 'COPIED VERBATIM from bin/known-xref.py — never your own judgment' }
    props.internal_ref  = { type: 'string', description: 'copied verbatim from the xref tool' }
    props.internal_note = { type: 'string', description: 'copied verbatim from the xref tool' }
    required.push('internal_status', 'internal_ref', 'internal_note')
  }
  return { type: 'object', additionalProperties: false, properties: props, required }
}
const RESULT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { entity: { type: 'string' }, findings: { type: 'array', items: findingSchema() } },
  required: ['entity', 'findings'],
}

function fetchInstructions(e) {
  const want = `${brief} — fields: ${fields.join(', ')}`
  const ctx = `${RUN_ID ? ` --run-id ${RUN_ID}` : ''} --entity ${JSON.stringify(e.label)}`
  return [
    `HOW TO READ THE WEB (a config-driven cost waterfall does the cheapest-first work — run`,
    `all commands from the gtm-research repo root, ${REPO_ROOT}):`,
    `1. SEARCH — use the search chokepoint (free-first + shared cache + telemetry), NOT a raw`,
    `   web-search tool:`,
    `   \`python3 ${REPO_ROOT}/bin/research-search.py query "<query>" --json${ctx}\``,
    `   Add \`--intent semantic\` ONLY for conceptual/discovery queries; leave it off for fact`,
    `   lookups. Add \`--domains a.com,b.com\` to restrict. Only if it returns no results may`,
    `   you fall back to a native WebSearch tool.`,
    `2. READ A PAGE (cost-aware waterfall + auto-digest + cache):`,
    `   \`python3 ${REPO_ROOT}/bin/page-digest.py "<url>" --entity ${JSON.stringify(e.label)} --want "${want}"${RUN_ID ? ` --run-id ${RUN_ID}` : ''}\``,
    `   Free native fetch → Jina (free) on a JS-shell → a Tavily credit only if both fail; long`,
    `   pages auto-compress to quoted facts. It HARD-STOPS dead URLs (404/410/401) and`,
    `   negative-caches them — if it reports a dead URL, do NOT retry: re-search or corroborate.`,
    `BUDGET: at most ${maxFetches} page reads and ${maxSearches} searches for this entity.`,
    `If you hit the budget, return what you have with honest notes — do not spin.`,
  ].join('\n')
}

const GUARDRAILS = [
  `SCOPE: Answer ONLY for this exact entity. Do not drift to a parent company, a similarly-`,
  `named entity, or a competitor. If two share a name, disambiguate with the known context`,
  `(domain/location) and say which one you resolved in note.`,
  `PRIMARY SOURCE: Prefer the entity's own site / staff directory / filing / the person's own`,
  `profile over aggregators (ZoomInfo, RocketReach, Crunchbase mirrors). Aggregators`,
  `corroborate; they do not establish, and they go stale.`,
  `FIELD DISCIPLINE — every field is exactly one of three explicit states:`,
  `  • a real value  + verified=true  + source_url set (a page you actually opened states it)`,
  `  • ""            + verified=false + note="NOT FOUND — searched <where>, absent"`,
  `  • a guess       + verified=false + note="UNVERIFIED — pattern guess: <why>"`,
  `Never invent a value. Distinguish "searched and absent" from "didn't look" in note.`,
  `For emails, confirm the address or pattern against a real directory/contact page — do not`,
  `guess a person-specific address and call it verified.`,
  `Prefer two independent sources for any high-stakes field.`,
]

function xrefInstruction(e) {
  if (!crossRef) return ''
  const dom = e.domain ? ` --domain ${e.domain}` : ''
  return [
    `\nINTERNAL CROSS-REFERENCE (deterministic — do this once for this entity):`,
    `Run: \`python3 ${REPO_ROOT}/bin/known-xref.py ${JSON.stringify(e.label)}${dom} --json\`.`,
    `Copy internal_status, internal_ref, internal_note VERBATIM from its JSON into EVERY finding`,
    `for this entity. (If no known-companies table is configured it returns net-new — copy that`,
    `as-is.) Never edit them or substitute your own judgment.`,
  ].join('\n')
}

function researchPrompt(e) {
  return [
    `Research this ${entityType}: **${e.label}**`,
    e.context ? `Known context:\n${e.context}` : '',
    `\nGoal: ${brief}`,
    `Purpose: ${purpose}`,
    sources.length ? `Where to look (start here, not exhaustive): ${sources.join(', ')}.` : '',
    `\n${fetchInstructions(e)}`,
    `\nRules:`,
    ...GUARDRAILS,
    xrefInstruction(e),
    `\nReturn ${multi ? 'one or more findings' : 'a single finding'} for this entity.`,
    `For each finding populate these fields: ${fields.join(', ')}.`,
    `\nReturn via the structured output tool. entity must be "${e.label}".`,
  ].filter(Boolean).join('\n')
}

function verifyPrompt(res) {
  return [
    `You are fact-checking research findings for: **${res.entity}**`,
    `Original goal: ${brief}`,
    `\nFindings to check:\n${JSON.stringify(res.findings, null, 2)}`,
    `\nFor each finding: open its source_url and confirm the source actually supports the stated fields.`,
    `To open a source_url, run from ${REPO_ROOT}:`,
    `\`python3 ${REPO_ROOT}/bin/page-digest.py "<source_url>" --no-digest --entity ${JSON.stringify(res.entity)} --want "${brief}"${RUN_ID ? ` --run-id ${RUN_ID}` : ''}\``,
    `— it reads through the cached free-first waterfall (a cache hit / native fetch costs nothing).`,
    `- If the source confirms a field, keep the value and set verified=true.`,
    `- If the source does NOT support a field, blank that field, set verified=false, explain in note.`,
    `- If source_url is empty or dead, set verified=false and note why.`,
    crossRef ? `- Do NOT change internal_status / internal_ref / internal_note — keep them exactly as given.` : '',
    `Do not add new findings. Return the same entity ("${res.entity}") with the corrected findings array.`,
  ].filter(Boolean).join('\n')
}

log(`entity-research: ${entities.length} ${entityType}${entities.length === 1 ? '' : 's'} · fields: ${fields.join(', ')} · crossRef: ${crossRef} · verify: ${doVerify} · budget: ${maxFetches} fetch/${maxSearches} search · model: ${researchModel}`)

// ---- Setup: open the telemetry run row (best-effort; shell-less orchestrator uses an agent) ----
{
  const RUN_SCHEMA = {
    type: 'object', additionalProperties: false,
    properties: { run_id: { type: 'string' }, telemetry_enabled: { type: 'boolean' } },
    required: ['run_id', 'telemetry_enabled'],
  }
  const createCmd = `python3 ${REPO_ROOT}/bin/research-run.py create --entity-type ${entityType} --model ${researchModel === 'inherit' ? 'sonnet' : researchModel} --entity-count ${entities.length} --purpose ${JSON.stringify(purpose)} --brief ${JSON.stringify(brief)} --json`
  const r = await agent(
    [`Run EXACTLY this one command from ${REPO_ROOT} and return the JSON it prints (use "" for run_id if it is null):`,
      '```', createCmd, '```'].join('\n'),
    { label: 'setup:run', phase: 'Setup', model: 'haiku', schema: RUN_SCHEMA })
  if (r && r.run_id) { RUN_ID = r.run_id; TELEMETRY = !!r.telemetry_enabled }
  log(TELEMETRY ? `telemetry on · run_id=${RUN_ID}` : 'telemetry off (no database configured) — running without cache/telemetry linkage')
}

function groupByDomain(list) {
  const g = {}
  list.forEach((e, i) => { const k = (e.domain || '').toLowerCase() || `__solo_${i}`; (g[k] = g[k] || []).push(e) })
  return Object.values(g)
}
function sharedContext(res) {
  const v = ((res && res.findings) || []).filter(f => f.verified)
  if (!v.length) return ''
  return `\nALREADY VERIFIED for this company (reuse these company-level fields and re-cite their source_url; only research what is still missing or person-specific):\n${JSON.stringify(v, null, 2)}`
}
// Verify seam: today Claude does both research and verify. This is the ONE call to swap ~95%
// of verifies to a cheap model later while keeping ~5% on Claude as a spot-check.
async function verifyEntity(res) {
  if (!res) return null
  if (!doVerify) return res
  return agent(verifyPrompt(res), {
    label: `verify:${(res.entity || '').slice(0, 40)}`, phase: 'Verify',
    schema: RESULT_SCHEMA, ...modelOpt(verifyModel),
  })
}
async function researchEntity(e, shared) {
  const prompt = shared ? `${researchPrompt(e)}\n${shared}` : researchPrompt(e)
  const res = await agent(prompt, {
    label: `research:${e.label.slice(0, 40)}`, phase: 'Research',
    schema: RESULT_SCHEMA, ...modelOpt(researchModel),
  })
  return verifyEntity(res)
}
async function runGroup(g) {
  const results = []
  let shared = ''
  for (const e of g) {
    const res = await researchEntity(e, shared)
    results.push({ e, res })
    if (!shared) shared = sharedContext(res)
  }
  return results
}

// Different domains run concurrently up to CONCURRENCY; the watchdog gates between chunks.
const groups = groupByDomain(entities)
const CONCURRENCY = 6
const collected = []
let pausedReason = null

for (let i = 0; i < groups.length && !pausedReason; i += CONCURRENCY) {
  const chunk = groups.slice(i, i + CONCURRENCY)
  const chunkResults = (await parallel(chunk.map(g => () => runGroup(g)))).filter(Boolean).flat()
  collected.push(...chunkResults)

  if (TELEMETRY) {
    const cmds = []
    for (const { e, res } of chunkResults) {
      const fs = (res && res.findings) || []
      const v = fs.filter(f => f.verified).length
      const u = fs.length - v
      const istatus = (fs.find(f => f.internal_status) || {}).internal_status
      cmds.push(`python3 ${REPO_ROOT}/bin/research-run.py telemetry --run-id ${RUN_ID} --entity ${JSON.stringify(e.label)}${e.domain ? ` --entity-domain ${JSON.stringify(e.domain)}` : ''} --verified ${v} --unverified ${u}${istatus ? ` --internal-status ${istatus}` : ''} --json`)
    }
    cmds.push(`python3 ${REPO_ROOT}/bin/research-run.py watchdog --run-id ${RUN_ID} --json`)
    const WD_SCHEMA = {
      type: 'object', additionalProperties: false,
      properties: { paused: { type: 'boolean' }, reason: { type: 'string' }, verified_rate: { type: 'number' }, entities: { type: 'number' } },
      required: ['paused'],
    }
    const wd = await agent(
      [`Run each command below in order from ${REPO_ROOT} using Bash, then return the JSON printed by the LAST (watchdog) command. Use "" for a null reason and -1 for a null verified_rate.`,
        '```', ...cmds, '```'].join('\n'),
      { label: 'watchdog', phase: 'Watchdog', model: 'haiku', schema: WD_SCHEMA })
    if (wd && wd.paused) { pausedReason = wd.reason || 'verified-rate collapsed'; log(`WATCHDOG PAUSED: ${pausedReason} — stopped dispatching new entities.`) }
  }
}

if (TELEMETRY) {
  await agent(
    [`Run this from ${REPO_ROOT} and return its JSON:`, '```',
      `python3 ${REPO_ROOT}/bin/research-run.py finish --run-id ${RUN_ID} --status ${pausedReason ? 'paused' : 'done'} --json`, '```'].join('\n'),
    { label: 'setup:finish', phase: 'Watchdog', model: 'haiku' })
}

const out = collected.map(c => c.res).filter(Boolean)
const rows = []
for (const res of out) { for (const f of (res.findings || [])) rows.push({ entity: res.entity, ...f }) }

const verifiedCount = rows.filter(r => r.verified).length
const outFields = [...fields, 'source_url', 'verified', 'note',
  ...(crossRef ? ['internal_status', 'internal_ref', 'internal_note'] : [])]
log(`Done: ${out.length}/${entities.length} entities, ${rows.length} findings, ${verifiedCount} verified.${pausedReason ? ` PAUSED: ${pausedReason}` : ''}`)

return {
  fields: outFields,
  rows,
  summary: {
    entities_in: entities.length,
    entities_returned: out.length,
    findings: rows.length,
    verified: verifiedCount,
    unverified: rows.length - verifiedCount,
    cross_reference: crossRef,
    run_id: RUN_ID || null,
    telemetry: TELEMETRY,
    paused: !!pausedReason,
    paused_reason: pausedReason,
  },
}
